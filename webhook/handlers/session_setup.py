from typing import Any

from ..models import User
from ..schemas.replies import TextReply

from ._shared import enter_idle, plan_picker, _PRODUCT_FAQ
from ..config import get_llm_client, get_decider_client
from ..repos import plan_repo, session_repo

llm = get_llm_client()
decider = get_decider_client()

_SESSION_SETUP_INSTRUCTIONS = (
    "O usuário está escolhendo como começar um treino: seguindo um plano "
    "salvo, em treino livre, voltando atrás, ou tirando uma dúvida sobre "
    "como o app funciona."
)

_SESSION_SETUP_CRITERIA = {
    "free": "Quer treinar livre, sem seguir um plano",
    "back": "Quer voltar, desistir de iniciar um treino agora",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não dá pra saber o que ele quer",
}


def _start_free(db: Any, user: User) -> tuple[TextReply, str]:
    session = session_repo.start_session(db, user, plan_id=None)
    user.state_data = {"session_id": session.id}
    return TextReply("💪 Treino livre iniciado! Bora!"), "session"


def handle_session_setup(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[Any, str]:

    if button_id == "back":
        return enter_idle(user)

    if button_id == "free":
        return _start_free(db, user)

    if button_id and button_id.startswith("plan:"):
        plan_id = int(button_id.split(":", 1)[1])
        plan = plan_repo.get_user_plan(db, user, plan_id)
        if plan is None:
            return plan_picker(plan_repo.list_active_plans(db, user, limit=8), "Não achei esse treino. "), "session_setup"

        session = session_repo.start_session(db, user, plan_id=plan.id)
        user.state_data = {"session_id": session.id}
        return TextReply(
            f"💪 Treino *{plan.name}* iniciado! Bora!\n"
            "Começa me mandando o exercício, o peso que você usou e quantas séries você fez."
            ), "session"

    if text:
        answer = decider.choose(
            state=text,
            instructions=_SESSION_SETUP_INSTRUCTIONS,
            criteria=_SESSION_SETUP_CRITERIA,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "free":
                return _start_free(db, user)
            elif answer.choice == "back":
                return enter_idle(user)
            elif answer.choice == "question":
                reply = llm.generate(_PRODUCT_FAQ + text)
                return plan_picker(plan_repo.list_active_plans(db, user, limit=8), f"{reply}\n\n"), "session_setup"

        return plan_picker(
            plan_repo.list_active_plans(db, user, limit=8),
            "🤔 Não entendi. Escolha um dos treinos da lista abaixo.\n\n",
        ), "session_setup"

    return plan_picker(plan_repo.list_active_plans(db, user, limit=8)), "session_setup"