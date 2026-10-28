"""Contexto pro agente de `onboarding`."""
from dataclasses import dataclass
from typing import Any

from agent.models import User
from agent.shared import _recent_history


@dataclass
class OnboardingContext:
    step: str | None
    history: str


def build_context(db: Any, user: User) -> OnboardingContext:
    return OnboardingContext(
        step=(user.state_data or {}).get("step"),
        history=_recent_history(db, user),
    )
