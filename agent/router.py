"""Roteador: Mercury (via os handlers existentes do webhook) decide o ESTADO;
dentro do estado `session`, quem decide e executa é o agente de tool-calling.
Os outros 4 estados continuam exatamente como no `webhook/` (sem duplicar
lógica que não está em escopo agora).
"""
from typing import Any

from webhook.models import User
from webhook.schemas.replies import ButtonsReply, ListReply, TextReply

from . import session_agent


def handle_message(
    db: Any, user: User, text: str | None, button_id: str | None = None
) -> tuple[TextReply | ButtonsReply | ListReply, str]:
    if user.state == "session":
        return session_agent.handle(db, user, text, button_id)

    from webhook.handlers import HANDLERS

    return HANDLERS[user.state](db, user, text, button_id)
