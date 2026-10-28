"""Contexto pro agente de `planning`."""
from dataclasses import dataclass
from typing import Any

from agent.models import User
from agent.repos import plan_repo
from agent.shared import _recent_history


@dataclass
class PlanInfo:
    id: int
    name: str


@dataclass
class PlanningContext:
    plans: list[PlanInfo]
    draft: dict | None  # {"name": str, "items": [{"name", "target_sets"}, ...]}
    pending_action: dict | None
    history: str

    def plan_ids(self) -> list[str]:
        return [f"plan:{p.id}" for p in self.plans]

    def find_plan(self, enum_value: str | None) -> PlanInfo | None:
        if not enum_value or not enum_value.startswith("plan:"):
            return None
        try:
            pid = int(enum_value.split(":", 1)[1])
        except ValueError:
            return None
        return next((p for p in self.plans if p.id == pid), None)


def build_context(db: Any, user: User) -> PlanningContext:
    rows = plan_repo.list_active_plans(db, user, limit=8)
    state_data = user.state_data or {}
    return PlanningContext(
        plans=[PlanInfo(id=r.id, name=r.name) for r in rows],
        draft=state_data.get("plan_draft"),
        pending_action=state_data.get("pending_action"),
        history=_recent_history(db, user),
    )
