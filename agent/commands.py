from typing import Any

from . import billing
from .models import User
from .schemas.replies import MultiTextReply, TextReply

# comando que o botão do Pro no site deixa pronto (site/app.py) e que as boas-vindas ensinam
PRO_COMMAND = "/pro"


def _handle_pro(db: Any, user: User) -> tuple[TextReply | MultiTextReply, str]:
    """Gera o Pix em qualquer estado — inclusive na primeira mensagem de alguém que veio do site —
    e não muda o estado: quem estava no meio de um treino continua nele."""
    return billing.pro_charge_reply(billing.pro_charge_result(db, user)), user.state


def handle_universal_command(
    db: Any, user: User, text: str | None
) -> tuple[TextReply | MultiTextReply, str] | None:
    if text is None:
        return None

    if text.strip().lower() == PRO_COMMAND:
        return _handle_pro(db, user)

    return None
