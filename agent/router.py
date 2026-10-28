"""Roteador: cada um dos 5 estados tem seu próprio agente de tool-calling.
Não há mais fallback pro `webhook.handlers.HANDLERS` — os 5 estados do
chatbot passam pelo padrão agêntico.
"""
from typing import Any

from webhook.models import User
from webhook.schemas.replies import ButtonsReply, ListReply, TextReply

from .agents import idle as idle_agent
from .agents import onboarding as onboarding_agent
from .agents import planning as planning_agent
from .agents import session as session_agent
from .agents import session_setup as session_setup_agent

_AGENTS = {
    "onboarding": onboarding_agent.handle,
    "idle": idle_agent.handle,
    "planning": planning_agent.handle,
    "session_setup": session_setup_agent.handle,
    "session": session_agent.handle,
}


def handle_message(
    db: Any, user: User, text: str | None, button_id: str | None = None
) -> tuple[TextReply | ButtonsReply | ListReply, str]:
    return _AGENTS[user.state](db, user, text, button_id)
