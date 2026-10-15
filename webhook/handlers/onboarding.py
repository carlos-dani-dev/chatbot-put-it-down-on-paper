from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_idle


def handle_onboarding(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    step = user.state_data.get("step")

    if step is None:

        next_state = "onboarding"
        user.state_data = {"step": "awaiting_user_onboarding_reply"}

        reply = (
                "💪 Eai gym bro, seja bem vindo!\n"
                "=> Registre treinos, séries e cargas em linguagem natural\n"
                "=> Veja seus progressos em tempo real\n"
                "=> Compartilhe seus resultados com amigos\n"
                "Vamos começar?"
            )
        return ButtonsReply(reply, [("onb_yes", "Sim"), ("onb_no", "Não")]), next_state
    
    elif step == "awaiting_user_onboarding_reply":

        if button_id == "onb_no":
            
            user.onboarding_completed = True
            return enter_idle(user, TextReply("🙂 Claro, me avise quando quiser continuar!"))
        
        elif button_id == "onb_yes":
            
            user.onboarding_completed = True
            
            return enter_idle(user)
        
    reply = (
                "🤔 Não entendi. Por favor, assinale a mensagem abaixo:\n"
                "Vamos começar?"
            )
    return ButtonsReply(reply, [("onb_yes", "Sim"), ("onb_no", "Não")] ), "onboarding"