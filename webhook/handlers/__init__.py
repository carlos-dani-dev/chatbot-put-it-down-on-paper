from .onboarding import handle_onboarding
from .idle import handle_idle
from .planning import handle_planning
from .session_setup import handle_session_setup
from .session import handle_session

HANDLERS = {
    "onboarding":    handle_onboarding,
    "idle":          handle_idle,
    "planning":      handle_planning,
    "session_setup": handle_session_setup,
    "session":       handle_session,
}