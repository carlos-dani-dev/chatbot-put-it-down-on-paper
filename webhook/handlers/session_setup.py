from typing import Any

from ..models import User
from ..schemas.replies import TextReply

from ._shared import enter_idle, plan_picker
from ..repos import plan_repo, session_repo



def handle_session_setup(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[Any, str]:

    if button_id == "back":
        return enter_idle(user)

    if button_id == "free":
        session = session_repo.start_session(db, user, plan_id=None)
        user.state_data = {"session_id": session.id}
        return TextReply("💪 Treino livre iniciado! Bora!"), "session"

    if button_id and button_id.startswith("plan:"):
        plan_id = int(button_id.split(":", 1)[1])
        plan = plan_repo.get_user_plan(db, user, plan_id)
        if plan is None:
            return plan_picker(plan_repo.list_active_plans(db, user, limit=8), "Não achei esse treino. "), "session_setup"

        session = session_repo.start_session(db, user, plan_id=plan.id)
        user.state_data = {"session_id": session.id}
        return TextReply(f"💪 Treino *{plan.name}* iniciado! Bora!"), "session"

    return plan_picker(plan_repo.list_active_plans(db, user, limit=8)), "session_setup"