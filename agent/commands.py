from typing import Any

from . import billing
from .models import User
from .schemas.replies import MultiTextReply, TextReply

# comando que o botão do Pro no site deixa pronto (site/app.py) e que as boas-vindas ensinam
PRO_COMMAND = "/pro"


def parse_pro_command(text: str | None) -> tuple[bool, str | None]:
    """("/pro") -> (True, None) · ("/pro PARCEIRO-JOAO") -> (True, "PARCEIRO-JOAO") · o resto -> (False, None).

    Só uma palavra depois do /pro vira cupom: "/pro quero assinar" segue pro agente como texto comum."""
    words = (text or "").split()
    if not words or words[0].lower() != PRO_COMMAND or len(words) > 2:
        return False, None
    return True, (words[1] if len(words) == 2 else None)


def _handle_pro(db: Any, user: User) -> tuple[TextReply | MultiTextReply, str]:
    """Gera o Pix em qualquer estado — inclusive na primeira mensagem de alguém que veio do site —
    e não muda o estado: quem estava no meio de um treino continua nele."""
    return billing.pro_charge_reply(billing.pro_charge_result(db, user)), user.state


def handle_universal_command(
    db: Any, user: User, text: str | None
) -> tuple[TextReply | MultiTextReply, str] | None:
    is_pro, coupon = parse_pro_command(text)
    if not is_pro:
        return None
    if coupon is not None:
        return billing.redeem_coupon_reply(db, user, coupon), user.state
    return _handle_pro(db, user)
