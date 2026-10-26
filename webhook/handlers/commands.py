from ..models import User
from ..schemas.replies import TextReply, ButtonsReply, ListReply

DASHBOARD_URL = "https://dashboard.exemplo.com/treinos"

_RESULTS_WRONG_STATE_MESSAGES = {
    "onboarding":    "🤔 Conclua o tutorial antes de ver os resultados.",
    "planning":      "🤔 Termine de planejar o treino antes de ver os resultados.",
    "session_setup": "🤔 Termine de escolher o treino antes de ver os resultados.",
    "session":       "🤔 Termine a sessão de treino antes de ver os resultados.",
}


def _handle_resultados(user: User) -> tuple[TextReply | ButtonsReply | ListReply, str]:
    if user.state == "idle":
        return TextReply(f"📊 Confira seus resultados aqui: {DASHBOARD_URL}"), "idle"

    return TextReply(_RESULTS_WRONG_STATE_MESSAGES[user.state]), user.state


def handle_universal_command(
    user: User, text: str | None
) -> tuple[TextReply | ButtonsReply | ListReply, str] | None:
    if text is None:
        return None

    command = text.strip().lower()

    if command == "/resultados":
        return _handle_resultados(user)

    return None
