"""Contexto pro agente de `onboarding`."""
from dataclasses import dataclass
from typing import Any

from webhook.models import User
from webhook.handlers._shared import _recent_history


@dataclass
class OnboardingContext:
    step: str | None
    history: str


def build_context(db: Any, user: User) -> OnboardingContext:
    return OnboardingContext(
        step=(user.state_data or {}).get("step"),
        history=_recent_history(db, user),
    )
