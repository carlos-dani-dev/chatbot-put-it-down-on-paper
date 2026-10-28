"""Monta o contexto de uma sessão de treino pro agente de tool-calling.

Reaproveita os models/repos do `webhook/` — mesmo banco, mesmas tabelas.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from webhook.models import Exercise, User, WorkoutSession, WorkoutSet
from webhook.repos import plan_repo, session_repo
from webhook.handlers._shared import _recent_history


@dataclass
class ExerciseInfo:
    id: int
    name: str
    target_sets: int | None  # None = fora do plano (treino livre ou extra)
    done_sets: int


@dataclass
class SetInfo:
    id: int
    exercise_id: int
    exercise_name: str
    weight_kg: float
    reps: int
    set_number: int


@dataclass
class SessionContext:
    ws: WorkoutSession
    plan_name: str | None
    exercises: list[ExerciseInfo]
    sets: list[SetInfo]
    pending_action: dict | None
    history: str

    def exercise_ids(self) -> list[str]:
        return [f"ex:{e.id}" for e in self.exercises]

    def set_ids(self) -> list[str]:
        return [f"set:{s.id}" for s in self.sets]

    def find_exercise(self, enum_value: str | None) -> ExerciseInfo | None:
        if not enum_value or not enum_value.startswith("ex:"):
            return None
        try:
            eid = int(enum_value.split(":", 1)[1])
        except ValueError:
            return None
        return next((e for e in self.exercises if e.id == eid), None)

    def find_set_ids(self, enum_values: list[str]) -> tuple[list[int], list[str]]:
        """Devolve (ids válidos, valores rejeitados) a partir de uma lista de 'set:<id>'."""
        valid = {s.id for s in self.sets}
        ok: list[int] = []
        bad: list[str] = []
        for v in enum_values or []:
            sid = None
            if isinstance(v, str) and v.startswith("set:"):
                try:
                    sid = int(v.split(":", 1)[1])
                except ValueError:
                    sid = None
            if sid is not None and sid in valid:
                ok.append(sid)
            else:
                bad.append(v)
        return ok, bad


def build_context(db: Any, user: User, ws: WorkoutSession) -> SessionContext:
    exercises: list[ExerciseInfo] = []
    plan_name: str | None = None

    if ws.plan_id is not None:
        plan = plan_repo.get_user_plan(db, user, ws.plan_id)
        plan_name = plan.name if plan is not None else None
        for pe, ex in plan_repo.get_plan_exercises(db, ws.plan_id):
            done = session_repo.count_sets(db, ws.id, ex.id)
            exercises.append(ExerciseInfo(id=ex.id, name=ex.name, target_sets=pe.target_sets, done_sets=done))

    known_ids = {e.id for e in exercises}
    for ex in session_repo.get_logged_exercises(db, ws.id):
        if ex.id not in known_ids:
            done = session_repo.count_sets(db, ws.id, ex.id)
            exercises.append(ExerciseInfo(id=ex.id, name=ex.name, target_sets=None, done_sets=done))
            known_ids.add(ex.id)

    name_by_exercise_id = {e.id: e.name for e in exercises}
    rows = db.scalars(
        select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)
    ).all()
    sets = [
        SetInfo(
            id=row.id,
            exercise_id=row.exercise_id,
            exercise_name=name_by_exercise_id.get(row.exercise_id, "?"),
            weight_kg=float(row.weight_kg),
            reps=row.reps,
            set_number=row.set_number,
        )
        for row in rows
    ]

    return SessionContext(
        ws=ws,
        plan_name=plan_name,
        exercises=exercises,
        sets=sets,
        pending_action=(user.state_data or {}).get("pending_action"),
        history=_recent_history(db, user, limit=4),
    )
