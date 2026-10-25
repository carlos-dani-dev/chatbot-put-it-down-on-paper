from datetime import timedelta
from typing import Any

from ..models import User
from ..schemas.replies import TextReply
from ..config import get_decider_client
from ..repos import plan_repo, session_repo
from ._shared import enter_idle

decider = get_decider_client()

RESUME_CHECK_THRESHOLD = timedelta(minutes=30)

_RESUME_STATES = {"planning", "session_setup", "session"}

_RESUME_PROMPTS = {
    "session_setup": "⏳ Ainda está escolhendo o treino de hoje?",
}


def _resume_prompt(db: Any, user: User) -> str:
    if user.state == "planning":
        draft = user.state_data.get("draft")
        if draft:
            return f"⏳ Ainda está montando o plano *{draft['name']}*?"
        return "⏳ Ainda está criando um plano de treino novo?"

    if user.state == "session":
        ws = session_repo.get_open_session(db, user)
        if ws is not None and ws.plan_id is not None:
            plan = plan_repo.get_user_plan(db, user, ws.plan_id)
            if plan is not None:
                return f"⏳ Ainda está treinando o plano *{plan.name}*?"
        return "⏳ Ainda está no seu treino livre?"

    return _RESUME_PROMPTS.get(user.state, "⏳ Ainda quer continuar?")

_RESUME_INSTRUCTIONS = (
    "O usuário ficou um tempo sem responder e foi perguntado se ainda quer "
    "continuar o que estava fazendo no chatbot."
)

_RESUME_CRITERIA = {
    "continue": "Quer continuar, sim",
    "stop": "Não quer continuar, prefere parar",
    "unclear": "Não dá pra saber",
}


def handle_resume_check(db: Any, user: User, text: str | None, gap) -> tuple[TextReply, str] | None:
    if user.state not in _RESUME_STATES:
        return None

    if user.state_data.get("resume_check"):
        answer = decider.choose(
            state=(text or "").lower(),
            instructions=_RESUME_INSTRUCTIONS,
            criteria=_RESUME_CRITERIA,
        )
        user.state_data = {k: v for k, v in user.state_data.items() if k != "resume_check"}

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "stop":
                return enter_idle(db, user, clear_data=False)
            elif answer.choice == "continue":
                return TextReply("👍 Beleza, pode mandar de novo!"), user.state

        user.state_data = {**user.state_data, "resume_check": True}
        return TextReply("🤔 Não entendi\nMe conta, ainda quer continuar?"), user.state

    if gap is not None and gap >= RESUME_CHECK_THRESHOLD:
        user.state_data = {**user.state_data, "resume_check": True}
        return TextReply(_resume_prompt(db, user)), user.state

    return None
