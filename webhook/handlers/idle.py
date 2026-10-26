from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_planning, enter_session_setup, idle_menu, _PRODUCT_FAQ
from ..config import get_llm_client, get_decider_client
from .planning import handle_planning
from .session_setup import handle_session_setup

llm = get_llm_client()
decider = get_decider_client()

_IDLE_INSTRUCTIONS = (
    "O usuário está no menu principal do chatbot de treino e pode querer "
    "planejar um treino novo (criar, listar ou excluir planos salvos), "
    "começar ou registrar um treino agora, ou tirar uma dúvida sobre como "
    "o app funciona."
)

_IDLE_CRITERIA = {
    "planning": "Quer fazer algo com planos de treino: criar um novo, ver os que já tem salvos, ou excluir um",
    "session": "Quer começar, continuar ou registrar algo de um treino agora: seguir um plano salvo, treinar livre, ou relatar uma série/exercício",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não dá pra saber o que ele quer",
}


def handle_idle(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    if text:
        answer = decider.choose(
            state=text.lower(),
            instructions=_IDLE_INSTRUCTIONS,
            criteria=_IDLE_CRITERIA,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "planning":
                user.state_data = {"step": "awaiting_plan"}
                return handle_planning(db, user, text, button_id)
            elif answer.choice == "session":
                user.state_data = {"step": "choosing_plan"}
                return handle_session_setup(db, user, text, button_id)
            elif answer.choice == "question":
                reply = llm.generate(_PRODUCT_FAQ + text)
                return idle_menu(db, user, reply), "idle"

    return idle_menu(db, user), "idle"
