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


def _fix_whatsapp_bold(text: str) -> str:
    text = text.replace("**", "*")
    return re.sub(r"\*([^*\n]+)\*", lambda m: f"*{m.group(1).strip()}*", text)


def _generate(prompt: str) -> str:
    return _fix_whatsapp_bold(llm.generate(prompt))


_ENTER_PLANNING_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer criar um "
    "plano de treino novo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino que ele já tem salvos (pode estar vazio): {plans}\n\n"
    "{note}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Se ele já tiver planos salvos, cite os nomes deles.\n"
    "- É de suma importância pedir SEMPRE o nome do plano novo, os exercícios e a"
    "quantidade de séries para cada um deles — sem isso o plano não pode ser criado.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_IDLE_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está de volta "
    "ao menu principal.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
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
