from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_idle, _PRODUCT_FAQ
from ..config import get_llm_client, get_decider_client

decider = get_decider_client()
llm = get_llm_client()

_AWAITING_USER_ONBOARDING_REPLY_INSTRUCTIONS = (
    "O usuário está no meio do onboarding do chatbot. "
    "O chatbot foi apresentado e ele vai querer começar, "
    "não querer começar ou tirar alguma dúvida fazendo uma pergunta."
)

_AWAITING_USER_ONBOARDING_REPLY_CRITERIA = {
    "start": "Sim vamos começar",
    "not_start": "Não não vamos começar",
    "question": "Pergunta"
}

def handle_onboarding(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    step = user.state_data.get("step")

    if step is None:

        next_state = "onboarding"
        user.state_data = {"step": "awaiting_user_onboarding_reply"}

        reply = (
                "💪 Eai gym bro, seja bem vindo!\n\n"
                "*Registre treinos*, séries e cargas em linguagem natural\n"
                "*Veja seus progressos* em tempo real\n"
                "*Compartilhe* seus resultados com amigos\n\n"
                "Vamos começar?"
            )
        return TextReply(reply), next_state
    
    elif step == "awaiting_user_onboarding_reply":

        if button_id == "onb_no":

            user.onboarding_completed = True
            return enter_idle(db, user, TextReply("🙂 Claro, me avise quando quiser continuar!"))

        elif button_id == "onb_yes":

            user.onboarding_completed = True

            return enter_idle(db, user)
        
    answer = decider.choose(
        state=(text or "").lower(),
        instructions=_AWAITING_USER_ONBOARDING_REPLY_INSTRUCTIONS,
        criteria=_AWAITING_USER_ONBOARDING_REPLY_CRITERIA,
    )

    if (answer.confidence or 0) >= 0.6:
        if answer.choice == "start":

            user.onboarding_completed = True

            return enter_idle(db, user)

        elif answer.choice == "not_start":

            user.onboarding_completed = True

            return enter_idle(db, user, TextReply("🙂 Claro, me avise quando quiser continuar!"))

        elif answer.choice == "question":

            next_state = "onboarding"
            reply = llm.generate(_PRODUCT_FAQ + text)

            return ButtonsReply(f"{reply}\n\nEaí, vamos começar?"), next_state

    reply = ("Não entendi. Por favor, Pode repetir?")
    return TextReply(reply), "onboarding"