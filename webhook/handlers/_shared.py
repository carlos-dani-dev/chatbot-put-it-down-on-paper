from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply, ListReply
from ..repos import plan_repo

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


def enter_idle(user: User, reply=None) -> tuple[TextReply | ButtonsReply, str]:

    user.state_data = {}

    return (reply or idle_menu()), "idle"


def enter_planning(db, user: User) -> tuple[TextReply | ButtonsReply, str]:
    names = plan_repo.list_active_plan_names(db, user)
    user.state_data = {"step": "awaiting_plan"}

    intro = "💪 Vamos criar um treino novo!"
    if names:
        intro += f"\nVocê já criou: {', '.join(names)}."
    return TextReply(
        intro + "\n\nMe manda o nome e os exercícios, ex.: *Costas: remada 4, puxada 3*"
    ), "planning"


def plan_picker(plans, note: str="") -> ListReply | ButtonsReply:
    if not plans:
            return ButtonsReply(
                f"{note}📋 Você ainda não tem treinos salvos.",
                [("free", "Treino livre"), ("back", "Voltar")],
            )

    rows = [(f"plan:{p.id}", p.name[:24]) for p in plans]
    rows += [("free", "Treino livre"), ("back", "Voltar")]
    return ListReply(f"{note}🏋️ Boa, então conta pra mim, qual treino vamos fazer hoje?", "Escolher", rows)


def enter_session_setup(db: Any, user: User) -> tuple[TextReply | ButtonsReply, str]:

    user.state_data = {"step": "choosing_plan"}
    
    return plan_picker(plan_repo.list_active_plans(db, user, limit=8)), "session_setup"


def idle_menu(intro: str = "") -> TextReply:
    text = f"{intro}\n\n" if intro else ""
    text += (
         "🏋️ Eai, o que vamos fazer agora?\n"
         "Você prefere **Planejar um treino** ou **Começar uma sessão de treino**?"
        )
    return TextReply(text)