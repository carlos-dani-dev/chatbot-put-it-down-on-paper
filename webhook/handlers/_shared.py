from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply, ListReply
from ..repos import plan_repo, chat_message_repo
from ..config import get_llm_client

llm = get_llm_client()

_PRODUCT_FAQ = (
    "Você é o assistente do chatbot de treino no WhatsApp. O usuário fez uma "
    "pergunta sobre o produto. Responda breve e amigável, usando só estas "
    "informações:\n\n"
    "- Registra treinos, séries e cargas mandando mensagem de texto normal.\n"
    "- Um modelo de IA interpreta o que foi digitado (exercício, peso, reps).\n"
    "- Dá pra criar plano de treino com antecedência ou treinar livre.\n"
    "- O progresso fica salvo automaticamente, comparado com a meta do plano.\n\n"
    "Pergunta do usuário: "
)

_IDLE_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está de volta "
    "ao menu principal.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- No máximo 3 linhas.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona)."
)

def enter_idle(db: Any, user: User, reply=None, clear_data: bool = True) -> tuple[TextReply | ButtonsReply, str]:

    if clear_data:
        user.state_data = {}

    return (reply or idle_menu(db, user)), "idle"


def enter_planning(db, user: User) -> tuple[TextReply | ButtonsReply, str]:
    names = plan_repo.list_active_plan_names(db, user)
    user.state_data = {"step": "awaiting_plan"}

    intro = "💪 Vamos criar um treino novo!"
    if names:
        intro += f"\nVocê já criou: {', '.join(names)}."
    return TextReply(
        intro + "\n\nMe manda o nome, os exercícios e a quantidade de séries de cada um.\nex.: *Costas: remada 4, puxada 3*"
    ), "planning"


def plan_picker(plans, note: str="") -> ListReply | ButtonsReply:
    if not plans:
            return ButtonsReply(
                f"{note}📋 Você ainda não tem treinos salvos."
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

    last_messages = chat_message_repo.get_last_messages(db, user, limit=2)
    history = "\n".join(f"{m.role}: {m.content}" for m in reversed(last_messages)) or "(nenhuma mensagem anterior)"

    prompt = _IDLE_MENU_PROMPT.format(
        history=history,
        intro=f"{intro}\n\n" if intro else "",
    )
    return TextReply(llm.generate(prompt))