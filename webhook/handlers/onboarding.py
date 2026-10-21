from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_idle
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

_PRODUCT_FAQ = (
    "Você é o assistente do chatbot de treino no WhatsApp. O usuário está no "
    "onboarding e fez uma pergunta sobre o produto. Responda breve e "
    "amigável, usando só estas informações:\n\n"
    "- Registra treinos, séries e cargas mandando mensagem de texto normal.\n"
    "- Um modelo de IA interpreta o que foi digitado (exercício, peso, reps).\n"
    "- Dá pra criar plano de treino com antecedência ou treinar livre.\n"
    "- O progresso fica salvo automaticamente, comparado com a meta do plano.\n\n"
    f"Pergunta do usuário: "
)

def handle_onboarding(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    step = user.state_data.get("step")

    if step is None:

        next_state = "onboarding"
        user.state_data = {"step": "awaiting_user_onboarding_reply"}

        reply = (
                "💪 Eai gym bro, seja bem vindo!\n\n"
                "- Registre treinos, séries e cargas em linguagem natural\n"
                "- Veja seus progressos em tempo real\n"
                "- Compartilhe seus resultados com amigos\n\n"
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
        
    answer = decider.choose(
        state=text,
        instructions=_AWAITING_USER_ONBOARDING_REPLY_INSTRUCTIONS,
        criteria=_AWAITING_USER_ONBOARDING_REPLY_CRITERIA,
    )

    if (answer.confidence or 0) >= 0.6:
        if answer.choice == "start":

            user.onboarding_completed = True

            return enter_idle(user)

        elif answer.choice == "not_start":

            user.onboarding_completed = True

            return enter_idle(user, TextReply("🙂 Claro, me avise quando quiser continuar!"))

        elif answer.choice == "question":

            reply = llm.generate(_PRODUCT_FAQ + text)
            return ButtonsReply(
                f"{reply}\n\nEaí, vamos começar?", [("onb_yes", "Sim"), ("onb_no", "Não")]
            ), "onboarding"

    reply = (
                "🤔 Não entendi. Por favor, assinale a mensagem abaixo:\n"
                "Vamos começar?"
                )
    return ButtonsReply(reply, [("onb_yes", "Sim"), ("onb_no", "Não")] ), "onboarding"