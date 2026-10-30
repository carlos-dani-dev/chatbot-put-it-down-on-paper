import re
from typing import Any

from .models import User
from .schemas.replies import TextReply, ButtonsReply, ListReply
from .repos import plan_repo, chat_message_repo
from .config import get_llm_client

llm = get_llm_client()


def _recent_history(db: Any, user: User, limit: int = 2) -> str:
    last_messages = chat_message_repo.get_last_messages(db, user, limit=limit)
    return "\n".join(f"{m.role}: {m.content}" for m in reversed(last_messages)) or "(nenhuma mensagem anterior)"


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]*)\]\((https?://[^\s)]+)\)")


def _fix_whatsapp_bold(text: str) -> str:
    text = text.replace("**", "*")
    text = re.sub(r"\*([^*\n]+)\*", lambda m: f"*{m.group(1).strip()}*", text)
    # WhatsApp não entende link em Markdown ([texto](url)) — só autolinka URL crua no texto.
    return _MARKDOWN_LINK_RE.sub(lambda m: m.group(2), text)


def _generate(prompt: str) -> str:
    return _fix_whatsapp_bold(llm.generate(prompt))


# --------------------------------------------------------------------------
# Persona e funcionalidades — base de todos os prompts. Cada estado declara o
# que o usuário consegue fazer ali (nome em negrito + como pedir), e a LLM é
# instruída a lembrar o usuário dessas possibilidades.
# --------------------------------------------------------------------------

ONBOARDING_FEATURES = [
    ("Registrar treinos", "mandar o exercício, a carga e as repetições em texto normal (ex.: supino 80kg 8 reps) que eu anoto tudo"),
    ("Criar planos de treino", "montar planos com os exercícios e a meta de séries de cada um"),
    ("Treino livre", "registrar um treino sem plano nenhum"),
    ("Acompanhar a evolução", "ver recordes, melhor carga e uma página de resultados com calendário e gráficos"),
    ("Começar agora", "é só confirmar que quer começar"),
]

IDLE_FEATURES = [
    ("Começar um treino", "escolher um plano salvo ou fazer um treino livre e registrar as séries"),
    ("Criar um plano", "montar um plano novo com exercícios e meta de séries"),
    ("Ver meus planos", "listar os planos salvos"),
    ("Ver exercícios de um plano", "mostrar o que tem em um plano salvo"),
    ("Editar ou excluir um plano", "mexer em um plano que já existe"),
    ("Ver resultados", "receber o link da página com calendário de treinos, progressão por exercício e recordes"),
]

SESSION_SETUP_FEATURES = [
    ("Treinar com um plano", "escolher um dos planos salvos pra seguir hoje"),
    ("Treino livre", "treinar sem plano e registrar o que for fazendo"),
    ("Mexer nos planos", "criar, editar ou excluir um plano antes de treinar"),
    ("Voltar", "voltar pro menu principal"),
]

ARNALDO_STYLE_RULES = (
    "- É MUITO importante lembrar o usuário das funcionalidades e possibilidades: sempre que "
    "fizer sentido, termine a resposta sugerindo o próximo passo mais útil, citando de 1 a 3 "
    "funcionalidades da lista acima com o nome em *negrito* (ex.: *Ver resultados*). Escolha só "
    "as que combinam com o momento — nunca despeje a lista inteira de uma vez.\n"
    "- Use emojis, mas com moderação: 1 ou 2 por mensagem, no máximo (ex.: 💪 🏋️ 📈 ✅ 📋), "
    "sempre combinando com o conteúdo — nunca encha a mensagem de emojis.\n"
    "- Negrito no WhatsApp é um asterisco só de cada lado, colado na palavra (*assim*), nunca "
    "dois e nunca com espaço entre o asterisco e a palavra.\n"
    "- Seja sucinto: mensagens curtas, fáceis de ler no celular.\n"
)


def arnaldo_header(where: str, features: list[tuple[str, str]]) -> str:
    """Abertura dos prompts: quem é o Arnaldo, onde o usuário está e o que ele pode fazer ali."""
    features_text = "\n".join(f"- *{name}*: {how}" for name, how in features)
    return (
        "Você é o *Arnaldo* 💪, um agente de IA no WhatsApp cuja missão é facilitar o controle de "
        "carga e o registro de treinos de musculação. O usuário fala em linguagem natural e você "
        "organiza, registra e acompanha a evolução dele. Fale como um parceiro de treino: direto, "
        "animado e sem enrolação.\n\n"
        f"Onde o usuário está agora: {where}\n\n"
        "O que ele pode fazer neste momento (ao sugerir, use estes nomes em negrito):\n"
        f"{features_text}\n\n"
    )


_ENTER_PLANNING_PROMPT = (
    arnaldo_header(
        "começando a criar um plano de treino novo.",
        [
            ("Criar o plano", "me mandar o nome do plano e os exercícios com a quantidade de séries (ex.: Peito: supino 3 séries, crucifixo 3 séries)"),
            ("Ver meus planos", "listar os planos que já estão salvos"),
            ("Começar um treino", "desistir de planejar e ir direto treinar"),
        ],
    )
    + "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino que ele já tem salvos (pode estar vazio): {plans}\n\n"
    "{note}"
    "Escreva a próxima mensagem do Arnaldo, respeitando:\n"
    "- Se ele já tiver planos salvos, cite os nomes deles.\n"
    "- É de suma importância pedir SEMPRE o nome do plano novo, os exercícios e a "
    "quantidade de séries para cada um deles — sem isso o plano não pode ser criado.\n"
    + ARNALDO_STYLE_RULES
)

_IDLE_MENU_PROMPT = (
    arnaldo_header("no menu principal.", IDLE_FEATURES)
    + "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do Arnaldo, respeitando:\n"
    "- É fundamental perguntar o que ele quer fazer, destacando principalmente *Começar um "
    "treino* e *Criar um plano*, e lembrando que ele pode *Ver resultados* quando quiser.\n"
    + ARNALDO_STYLE_RULES
)


def enter_idle(db: Any, user: User, reply=None, clear_data: bool = True) -> tuple[TextReply | ButtonsReply, str]:

    if clear_data:
        user.state_data = {}

    return (reply or idle_menu(db, user)), "idle"


def enter_planning(db, user: User, note: str = "") -> tuple[TextReply | ButtonsReply, str]:
    names = plan_repo.list_active_plan_names(db, user)
    user.state_data = {"step": "awaiting_plan"}

    prompt = _ENTER_PLANNING_PROMPT.format(
        history=_recent_history(db, user),
        plans=", ".join(names) if names else "(nenhum)",
        note=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt)), "planning"


def plan_picker(plans, note: str = "") -> ListReply | ButtonsReply:
    if not plans:
            return ButtonsReply(
                f"{note}📋 Você ainda não tem treinos salvos.\n"
                "Que tal começar um *treino livre*? É só escolher aqui embaixo!"
                "\n\nSe quiser *voltar* para o menu anterior é só me avisar!",
                [("free", "Treino livre")],
            )

    rows = [(f"plan:{p.id}", p.name[:24]) for p in plans]
    rows += [("free", "Treino livre")]
    return ListReply(f"{note}🏋️ Boa, então conta pra mim, qual treino vamos fazer hoje?"
                     "\n\nSe quiser *voltar* para o menu anterior é só me avisar!", "Escolher", rows)


def enter_session_setup(db: Any, user: User) -> tuple[TextReply | ButtonsReply, str]:

    user.state_data = {"step": "choosing_plan"}

    return plan_picker(plan_repo.list_active_plans(db, user, limit=8)), "session_setup"


def idle_menu(db: Any, user: User, intro: str = "") -> TextReply:

    prompt = _IDLE_MENU_PROMPT.format(
        history=_recent_history(db, user),
        intro=f"{intro}\n\n" if intro else "",
    )
    return TextReply(_generate(prompt))
