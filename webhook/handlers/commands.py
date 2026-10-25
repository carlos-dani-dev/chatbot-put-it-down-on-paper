from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply, ListReply

from ._shared import enter_idle
from ..repos import session_repo

DASHBOARD_URL = "https://dashboard.exemplo.com/treinos"

_CANCEL_WRONG_STATE_MESSAGE = (
    "🤔 Esse comando só funciona durante o planejamento de um treino ou numa sessão em andamento."
)

_RESULTS_WRONG_STATE_MESSAGES = {
    "onboarding":    "🤔 Conclua o tutorial antes de ver os resultados.",
    "planning":      "🤔 Termine de planejar o treino antes de ver os resultados.",
    "session_setup": "🤔 Termine de escolher o treino antes de ver os resultados.",
    "session":       "🤔 Termine a sessão de treino antes de ver os resultados.",
}


def _handle_cancelar(db: Any, user: User) -> tuple[TextReply | ButtonsReply | ListReply, str]:
    if user.state == "session":
        ws = session_repo.get_open_session(db, user)
        if ws is not None:
            session_repo.cancel_session(db, ws)
        return enter_idle(db, user)

    if user.state == "planning":
        return enter_idle(db, user)

    return TextReply(_CANCEL_WRONG_STATE_MESSAGE), user.state


def _handle_resultados(user: User) -> tuple[TextReply | ButtonsReply | ListReply, str]:
    if user.state == "idle":
        return TextReply(f"📊 Confira seus resultados aqui: {DASHBOARD_URL}"), "idle"

    message = _RESULTS_WRONG_STATE_MESSAGES.get(user.state, _CANCEL_WRONG_STATE_MESSAGE)
    return TextReply(message), user.state


def handle_universal_command(
    db: Any, user: User, text: str | None
) -> tuple[TextReply | ButtonsReply | ListReply, str] | None:
    if text is None:
        return None

    command = text.strip().lower()

    if command == "/cancelar":
        return _handle_cancelar(db, user)

    if command == "/resultados":
        return _handle_resultados(user)

    return None
