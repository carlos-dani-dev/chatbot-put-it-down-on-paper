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
    db.flush()  # com autoflush=False, sem isso o plano ainda apareceria como ativo na mesma transação


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

    db.flush()  # grava os exercícios já: uma consulta na mesma transação veria o plano vazio
    return plan


def update_plan(db: Any, plan: Plan, draft: dict) -> Plan:
    """Grava a edição no MESMO plano (o histórico de treinos continua ligado a ele).

    Os itens que continuam no plano são atualizados no lugar, em vez de apagados e recriados:
    cada série registrada aponta pro item (plan_exercise_id), e apagar o item soltaria essas séries.
    Só o item de um exercício removido é apagado — as séries dele ficam, sem o vínculo com o plano.
    """
    current = {pe.exercise_id: pe for pe, _ex in get_plan_exercises(db, plan.id)}
    kept: set[int] = set()

    for position, item in enumerate(draft["items"]):
        exercise, _ = exercise_repo.find_or_create(db, item["name"])
        pe = current.get(exercise.id)
        if pe is not None and exercise.id not in kept:
            pe.position = position
            pe.target_sets = item["target_sets"]
        else:
            db.add(PlanExercise(plan_id=plan.id, exercise_id=exercise.id, position=position,
                                target_sets=item["target_sets"]))
        kept.add(exercise.id)

    for exercise_id, pe in current.items():
        if exercise_id not in kept:
            db.delete(pe)

    db.flush()
    return plan
