"""Monta os dados pra página de resultados (`GET /webhook/dashboard`) — só
leitura, sem LLM nenhuma no meio: tudo vem direto de consultas no banco.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .models import User, WorkoutSession, WorkoutSet

PODIUM_SIZE = 3
FREE_TRAINING_LABEL = "Treino livre"


def _format_compact_kg(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _format_relative_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta_days = (datetime.now(timezone.utc).date() - dt.date()).days
    date_label = dt.strftime("%d/%m")
    if delta_days == 0:
        return f"Hoje, {date_label}"
    if delta_days == 1:
        return f"Ontem, {date_label}"
    return date_label


def build_dashboard_context(db: Any, user: User) -> dict:
    sessions = db.scalars(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user.id, WorkoutSession.ended_at.is_not(None))
        .order_by(WorkoutSession.started_at.desc())
    ).all()

    if not sessions:
        return {"user": user, "has_data": False}

    sets_by_session: dict[int, list[WorkoutSet]] = {
        ws.id: db.scalars(
            select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)
        ).all()
        for ws in sessions
    }

    # --- treino mais recente ---
    last = sessions[0]
    last_sets = sets_by_session[last.id]
    last_volume = sum(float(s.weight_kg) * s.reps for s in last_sets)

    seen_ids: set[int] = set()
    last_exercises: list[dict] = []
    for s in last_sets:
        if s.exercise_id in seen_ids:
            continue
        seen_ids.add(s.exercise_id)
        last_exercises.append({
            "name": s.exercise.name,
            "best_kg": max(float(x.weight_kg) for x in last_sets if x.exercise_id == s.exercise_id),
        })

    last_session = {
        "date_label": _format_relative_date(last.started_at),
        "plan_name": last.plan.name if last.plan else FREE_TRAINING_LABEL,
        "volume_label": _format_compact_kg(last_volume),
        "exercises": last_exercises,
    }

    # --- progresso agrupado por plano (vira as abas) ---
    plan_order: list[str] = []
    plan_exercises: dict[str, dict[int, dict]] = {}
    plan_session_count: dict[str, int] = {}

    for ws in reversed(sessions):  # ordem cronológica — os pontos de progresso precisam ir do mais antigo pro mais recente
        plan_name = ws.plan.name if ws.plan else FREE_TRAINING_LABEL
        if plan_name not in plan_exercises:
            plan_exercises[plan_name] = {}
            plan_order.append(plan_name)
        plan_session_count[plan_name] = plan_session_count.get(plan_name, 0) + 1

        per_exercise_best: dict[int, dict] = {}
        for s in sets_by_session[ws.id]:
            entry = per_exercise_best.setdefault(s.exercise_id, {"name": s.exercise.name, "weight_kg": 0.0})
            entry["weight_kg"] = max(entry["weight_kg"], float(s.weight_kg))

        for exercise_id, info in per_exercise_best.items():
            ex_entry = plan_exercises[plan_name].setdefault(
                exercise_id, {"name": info["name"], "points": []}
            )
            ex_entry["points"].append({
                "label": ws.started_at.strftime("%d/%m"),
                "weight_kg": info["weight_kg"],
            })

    plans = []
    for plan_name in plan_order:
        exercises = sorted(
            plan_exercises[plan_name].values(), key=lambda e: len(e["points"]), reverse=True
        )
        for ex in exercises:
            best = max(p["weight_kg"] for p in ex["points"])
            ex["best_kg"] = best
            ex["best_label"] = _format_compact_kg(best)
            ex["is_pr"] = ex["points"][-1]["weight_kg"] >= best
            ex["delta_kg"] = round(ex["points"][-1]["weight_kg"] - ex["points"][0]["weight_kg"], 1)
            ex["first_label"] = ex["points"][0]["label"]
        plans.append({"name": plan_name, "exercises": exercises})

    plans.sort(key=lambda p: plan_session_count[p["name"]], reverse=True)

    # --- treinos mais feitos (pódio) ---
    ranking = sorted(plan_session_count.items(), key=lambda kv: kv[1], reverse=True)[:PODIUM_SIZE]
    max_count = ranking[0][1] if ranking else 1
    podium = [
        {
            "rank": i + 1,
            "name": name,
            "count": count,
            "height_pct": max(28, round(count / max_count * 100)),
        }
        for i, (name, count) in enumerate(ranking)
    ]

    return {
        "user": user,
        "has_data": True,
        "last_session": last_session,
        "plans": plans,
        "podium": podium,
    }
