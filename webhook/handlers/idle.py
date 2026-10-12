from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply

from ._shared import enter_planning, enter_session_setup, idle_menu

def handle_idle(db: Any | None, user: User, text:str | None = None, button_id: str | None = None) -> TextReply | ButtonsReply:
    
    if button_id == "create_plan":
        return enter_planning(db, user)
    if button_id == "start_session":
        return enter_session_setup(db, user)
    return idle_menu(), "idle"
