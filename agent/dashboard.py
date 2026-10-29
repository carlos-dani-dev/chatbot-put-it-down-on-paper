"""Monta os dados pra página de resultados (`GET /webhook/dashboard`) — só
leitura, sem LLM nenhuma no meio: tudo vem direto de consultas no banco.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from .models import Exercise, User, WorkoutSession, WorkoutSet

RECENT_SESSIONS_LIMIT = 8


def build_dashboard_context(db: Any, user: User) -> dict:
    sessions = db.scalars(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user.id, WorkoutSession.ended_at.is_not(None))
        .order_by(WorkoutSession.started_at.desc())
    ).all()

    total_sets = db.scalar(
        select(func.count()).select_from(WorkoutSet).where(WorkoutSet.user_id == user.id)
    ) or 0
    total_volume = db.scalar(
        select(func.coalesce(func.sum(WorkoutSet.weight_kg * WorkoutSet.reps), 0))
        .where(WorkoutSet.user_id == user.id)
    ) or 0

    last_session = sessions[0] if sessions else None
    days_since_last = None
    if last_session is not None:
        started_at = last_session.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        days_since_last = (datetime.now(timezone.utc) - started_at).days

    recent_sessions = []
    for ws in sessions[:RECENT_SESSIONS_LIMIT]:
        sets = db.scalars(
            select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)
        ).all()

        by_exercise: dict[int, dict] = {}
        for s in sets:
            entry = by_exercise.setdefault(s.exercise_id, {"name": s.exercise.name, "sets": []})
            entry["sets"].append({"weight_kg": float(s.weight_kg), "reps": s.reps})

        recent_sessions.append({
            "date": ws.started_at,
            "plan_name": ws.plan.name if ws.plan else "Treino livre",
            "exercises": list(by_exercise.values()),
            "total_sets": len(sets),
        })

    progress_rows = db.execute(
        select(
            WorkoutSet.exercise_id,
            Exercise.name,
            WorkoutSession.started_at,
            func.max(WorkoutSet.weight_kg),
        )
        .join(Exercise, Exercise.id == WorkoutSet.exercise_id)
        .join(WorkoutSession, WorkoutSession.id == WorkoutSet.session_id)
        .where(WorkoutSet.user_id == user.id)
        .group_by(WorkoutSet.exercise_id, WorkoutSession.id, Exercise.name, WorkoutSession.started_at)
        .order_by(WorkoutSession.started_at)
    ).all()

    progress_by_exercise: dict[int, dict] = {}
    for exercise_id, name, started_at, max_weight in progress_rows:
        entry = progress_by_exercise.setdefault(exercise_id, {"name": name, "points": []})
        entry["points"].append({"label": started_at.strftime("%d/%m"), "weight_kg": float(max_weight)})

    exercises_progress = sorted(
        progress_by_exercise.values(), key=lambda e: len(e["points"]), reverse=True
    )

    return {
        "user": user,
        "stats": {
            "total_sessions": len(sessions),
            "total_sets": total_sets,
            "total_volume_kg": round(float(total_volume)),
            "days_since_last": days_since_last,
        },
        "recent_sessions": recent_sessions,
        "exercises_progress": exercises_progress,
    }
