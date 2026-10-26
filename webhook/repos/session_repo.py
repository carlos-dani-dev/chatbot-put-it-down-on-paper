from typing import Any
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import User, WorkoutSession, WorkoutSet


def start_session(db: Any, user: User, plan_id: int | None) -> WorkoutSession:
    session = WorkoutSession(user_id=user.id, plan_id=plan_id)
    db.add(session)
    db.flush()
    return session


def get_open_session(db: Any, user: User) -> WorkoutSession | None:
    return db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.user_id == user.id, WorkoutSession.ended_at.is_(None)
        )
    )


def count_sets(db: Any, session_id: int, exercise_id: int | None = None) -> int:
    q = select(func.count()).select_from(WorkoutSet).where(WorkoutSet.session_id == session_id)
    if exercise_id is not None:
        q = q.where(WorkoutSet.exercise_id == exercise_id)
    return db.scalar(q)


def close_session(db: Any, ws: WorkoutSession) -> int:
    """Fecha a sessão e devolve quantas séries foram registradas."""
    ws.ended_at = datetime.now(timezone.utc)
    ws.ended_reason = "finished"
    return count_sets(db, ws.id)


def get_last_set(db: Any, session_id: int) -> WorkoutSet | None:
    return db.scalar(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == session_id)
        .order_by(WorkoutSet.id.desc())
        .limit(1)
    )


def undo_last_set(db: Any, ws: WorkoutSession) -> WorkoutSet | None:
    """Remove a última série registrada na sessão, pra ser recadastrada em seguida."""
    last_set = get_last_set(db, ws.id)
    if last_set is None:
        return None

    db.delete(last_set)
    db.flush()
    return last_set


def get_last_weight(db: Any, session_id: int, exercise_id: int) -> float | None:
    w = db.scalar(
        select(WorkoutSet.weight_kg)
        .where(WorkoutSet.session_id == session_id, WorkoutSet.exercise_id == exercise_id)
        .order_by(WorkoutSet.set_number.desc())
        .limit(1)
    )
    return float(w) if w is not None else None


def log_sets(
    db: Any,
    ws: WorkoutSession,
    exercise_id: int,
    plan_exercise_id: int | None,
    sets: list[tuple[float, int, int | None]],
    raw_text: str,
) -> tuple[int, int]:
    n = db.scalar(
        select(func.coalesce(func.max(WorkoutSet.set_number), 0)).where(
            WorkoutSet.session_id == ws.id, WorkoutSet.exercise_id == exercise_id
        )
    )
    first = n + 1
    for weight, reps, rest in sets:
        n += 1
        db.add(WorkoutSet(
            session_id=ws.id, user_id=ws.user_id, exercise_id=exercise_id,
            plan_exercise_id=plan_exercise_id,
            set_number=n, weight_kg=weight, reps=reps, rest_seconds=rest, raw_text=raw_text,
        ))
    db.flush()
    ws.current_exercise_id = exercise_id

    return first, n
