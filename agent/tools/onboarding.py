"""Ferramentas do agente de `onboarding`."""
import logging
from typing import Any

from google.genai import types

from agent.models import User

from ..context.onboarding import OnboardingContext

logger = logging.getLogger("agent.tools.onboarding")


def confirm_start(db: Any, user: User, ctx: OnboardingContext) -> dict:
    user.onboarding_completed = True
    return {"ok": True, "next_state": "idle"}


def decline_start(db: Any, user: User, ctx: OnboardingContext) -> dict:
    user.onboarding_completed = True
    return {"ok": True, "next_state": "idle", "note": "usuário preferiu não começar agora"}


TOOL_IMPLS = {
    "confirm_start": confirm_start,
    "decline_start": decline_start,
}


def build_declarations(ctx: OnboardingContext) -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="confirm_start",
            description="O usuário confirmou que quer começar a usar o app agora.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="decline_start",
            description="O usuário disse que não quer começar agora, prefere depois.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
    ]
