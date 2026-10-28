from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from ..models import Exercise, Plan, PlanExercise, User
from . import exercise_repo

_ACTIVE_PLANS = text("""
    SELECT id, name FROM plans
    WHERE user_id = :uid AND archived_at IS NULL
""")

_ACTIVE_PLANS_LIMITED = text("""
    SELECT id, name FROM plans
    WHERE user_id = :uid AND archived_at IS NULL
    ORDER BY created_at DESC, id DESC
    LIMIT :limit
""")


def list_active_plan_names(db: Any, user: User) -> list[str]:
    return [r.name for r in db.execute(_ACTIVE_PLANS, {"uid": user.id})]


def list_active_plans(db: Any, user: User, limit: int = 8):
    return db.execute(_ACTIVE_PLANS_LIMITED, {"uid": user.id, "limit": limit}).all()


def get_user_plan(db: Any, user: User, plan_id: int) -> Plan | None:
    plan = db.get(Plan, plan_id)
    if plan is None or plan.user_id != user.id:
        return None
    return plan


def archive_plan(db: Any, plan: Plan) -> None:
    plan.archived_at = datetime.now(timezone.utc)


def get_plan_exercises(db: Any, plan_id: int) -> list[tuple[PlanExercise, Exercise]]:
    pes = db.scalars(
        select(PlanExercise).where(PlanExercise.plan_id == plan_id).order_by(PlanExercise.position)
    ).all()
    return [(pe, db.get(Exercise, pe.exercise_id)) for pe in pes]


def save_plan(db: Any, user: User, draft: dict) -> Plan:
    plan = Plan(user_id=user.id, name=draft["name"])
    db.add(plan)
    db.flush()

    for position, item in enumerate(draft["items"]):
        exercise, _ = exercise_repo.find_or_create(db, item["name"])

        db.add(PlanExercise(
            plan_id=plan.id,
            exercise_id=exercise.id,
            position=position,
            target_sets=item["target_sets"],
        ))

    return plan
