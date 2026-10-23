from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_planning, enter_session_setup, idle_menu, _PRODUCT_FAQ
from ..config import get_llm_client, get_decider_client

llm = get_llm_client()
decider = get_decider_client()

_IDLE_INSTRUCTIONS = (
    "O usuário está no menu principal do chatbot de treino e pode querer "
    "planejar um treino novo, iniciar um treino agora, ou tirar uma dúvida "
    "sobre como o app funciona."
)

_IDLE_CRITERIA = {
    "create_plan": "Quer planejar um treino novo",
    "start_session": "Quer iniciar um treino agora",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não dá pra saber o que ele quer",
}


def handle_idle(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    if button_id == "create_plan":
        return enter_planning(db, user)
    if button_id == "start_session":
        return enter_session_setup(db, user)

    if text:
        answer = decider.choose(
            state=text.lower(),
            instructions=_IDLE_INSTRUCTIONS,
            criteria=_IDLE_CRITERIA,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "create_plan":
                return enter_planning(db, user)
            elif answer.choice == "start_session":
                return enter_session_setup(db, user)
            elif answer.choice == "question":
                reply = llm.generate(_PRODUCT_FAQ + text)
                return idle_menu(reply), "idle"

    return idle_menu(), "idle"
