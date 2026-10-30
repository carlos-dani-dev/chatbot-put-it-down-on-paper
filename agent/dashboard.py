"""Monta os dados pra página de resultados (`GET /webhook/dashboard`) — só
leitura, sem LLM nenhuma no meio: tudo vem direto de consultas no banco.

Sessões de planos excluídos (archived_at preenchido) ficam de fora de tudo,
junto com as séries delas; treino livre (sem plano) continua aparecendo.
"""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from .models import Plan, PlanExercise, User, WorkoutSession, WorkoutSet

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
FREE_TRAINING_LABEL = "Treino livre"
PODIUM_SIZE = 3
FREQUENCY_WEEKS = 8
HEATMAP_WEEKS = 12
VOLUME_SESSIONS = 12
RECORDS_LIMIT = 5
MAX_VALID_SESSION_MINUTES = 240  # acima disso o usuário esqueceu de finalizar o treino
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def _format_kg(value: float) -> str:
    """Carga/volume compacto: 62.5 -> '62.5', 3240 -> '3.2K', 1250000 -> '1.3M'."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.0f}K"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{round(value, 1):g}"


def _format_relative_date(dt: datetime) -> str:
    local = _local(dt)
    delta_days = (datetime.now(LOCAL_TZ).date() - local.date()).days
    label = local.strftime("%d/%m")
    if delta_days == 0:
        return f"Hoje, {label}"
    if delta_days == 1:
        return f"Ontem, {label}"
    if delta_days < 7:
        return f"{WEEKDAYS[local.weekday()].capitalize()}, {label}"
    return label


def _format_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}min"
    return f"{minutes // 60}h{minutes % 60:02d}"


def _session_minutes(ws: WorkoutSession) -> int | None:
    if ws.ended_at is None:
        return None
    minutes = int((_local(ws.ended_at) - _local(ws.started_at)).total_seconds() // 60)
    return minutes if 1 <= minutes <= MAX_VALID_SESSION_MINUTES else None


def _epley_1rm(weight: float, reps: int) -> float:
    return weight if reps <= 1 else weight * (1 + reps / 30)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _plan_name(ws: WorkoutSession) -> str:
    return ws.plan.name if ws.plan else FREE_TRAINING_LABEL


def build_dashboard_context(db: Any, user: User) -> dict:
    member_since = _local(user.created_at).strftime("%m/%Y") if user.created_at else None

    # todo treino com pelo menos uma série conta, finalizado ou não — sessão largada sem
    # "finalizar" ainda é treino feito
    sessions = db.scalars(
        select(WorkoutSession)
        .outerjoin(Plan, Plan.id == WorkoutSession.plan_id)
        .where(
            WorkoutSession.user_id == user.id,
            WorkoutSession.id.in_(select(WorkoutSet.session_id).where(WorkoutSet.user_id == user.id)),
            or_(WorkoutSession.plan_id.is_(None), Plan.archived_at.is_(None)),
        )
        .order_by(WorkoutSession.started_at)
    ).all()  # ordem cronológica: do mais antigo pro mais recente

    if not sessions:
        return {"user": user, "member_since": member_since, "has_data": False}

    sets_by_session: dict[int, list[WorkoutSet]] = {
        ws.id: db.scalars(
            select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)
        ).all()
        for ws in sessions
    }
    volume_by_session = {
        sid: sum(float(s.weight_kg) * s.reps for s in sets) for sid, sets in sets_by_session.items()
    }

    plan_targets: dict[int, dict[int, int]] = {}
    for plan_id in {ws.plan_id for ws in sessions if ws.plan_id is not None}:
        plan_targets[plan_id] = {
            pe.exercise_id: pe.target_sets
            for pe in db.scalars(select(PlanExercise).where(PlanExercise.plan_id == plan_id)).all()
        }

    def adherence_pct(ws: WorkoutSession) -> int | None:
        targets = plan_targets.get(ws.plan_id) if ws.plan_id else None
        if not targets:
            return None
        done_by_ex: dict[int, int] = defaultdict(int)
        for s in sets_by_session[ws.id]:
            done_by_ex[s.exercise_id] += 1
        done = sum(min(done_by_ex[eid], target) for eid, target in targets.items())
        return round(done / sum(targets.values()) * 100)

    today = datetime.now(LOCAL_TZ).date()
    this_week = _week_start(today)

    def status(ws: WorkoutSession) -> str | None:
        if ws.ended_at is not None:
            return None
        return "Em andamento" if user.state == "session" and ws is sessions[-1] else "Não finalizado"

    def session_detail(ws: WorkoutSession) -> dict:
        grouped: dict[int, dict] = {}
        for s in sets_by_session[ws.id]:
            entry = grouped.setdefault(s.exercise_id, {"name": s.exercise.name, "sets": [], "best": 0.0})
            entry["sets"].append(f"{float(s.weight_kg):g}kg × {s.reps}")
            entry["best"] = max(entry["best"], float(s.weight_kg))
        minutes = _session_minutes(ws)
        return {
            "plan_name": _plan_name(ws),
            "time_label": _local(ws.started_at).strftime("%H:%M"),
            "volume_label": _format_kg(volume_by_session[ws.id]),
            "duration_label": _format_duration(minutes) if minutes else None,
            "sets_count": len(sets_by_session[ws.id]),
            "adherence": adherence_pct(ws),
            "status": status(ws),
            "exercises": [
                {"name": e["name"], "sets": e["sets"], "best_label": _format_kg(e["best"])}
                for e in grouped.values()
            ],
        }

    # --- calendário: treinos por dia (data local) ---
    calendar_days: dict[str, list[dict]] = defaultdict(list)
    for ws in sessions:
        calendar_days[_local(ws.started_at).date().isoformat()].append(session_detail(ws))
    first_day = _local(sessions[0].started_at).date()
    calendar = {
        "days": calendar_days,
        "first_month": f"{first_day.year}-{first_day.month:02d}",
        "today": today.isoformat(),
    }

    # --- treino mais recente ---
    last = sessions[-1]
    last_session = {"date_label": _format_relative_date(last.started_at), **session_detail(last)}

    # --- histórico por exercício (todas as sessões visíveis) + recordes ---
    history: dict[int, dict] = {}
    records: list[dict] = []
    plan_last_seen: dict[str, int] = {}
    plan_exercise_seen: dict[str, dict[int, int]] = {}
    plan_session_count: dict[str, int] = defaultdict(int)

    for order, ws in enumerate(sessions):
        plan_name = _plan_name(ws)
        plan_last_seen[plan_name] = order
        plan_session_count[plan_name] += 1
        local_label = _local(ws.started_at).strftime("%d/%m")

        best_in_session: dict[int, WorkoutSet] = {}
        volume_in_session: dict[int, float] = defaultdict(float)
        for s in sets_by_session[ws.id]:
            volume_in_session[s.exercise_id] += float(s.weight_kg) * s.reps
            current = best_in_session.get(s.exercise_id)
            if current is None or (float(s.weight_kg), s.reps) > (float(current.weight_kg), current.reps):
                best_in_session[s.exercise_id] = s

        seen = plan_exercise_seen.setdefault(plan_name, {})
        for exercise_id, best in best_in_session.items():
            weight, reps = float(best.weight_kg), best.reps
            h = history.setdefault(exercise_id, {
                "name": best.exercise.name, "points": [], "volume": 0.0, "best_set": None,
            })
            previous_best = max((p["weight_kg"] for p in h["points"]), default=None)
            if previous_best is not None and weight > previous_best:
                records.append({
                    "exercise": h["name"],
                    "weight_label": _format_kg(weight),
                    "gain_label": _format_kg(weight - previous_best),
                    "date_label": _format_relative_date(ws.started_at),
                })
            h["points"].append({
                "label": local_label,
                "weight_kg": weight,
                "reps": reps,
                "e1rm": round(_epley_1rm(weight, reps), 1),
            })
            h["volume"] += volume_in_session[exercise_id]
            if h["best_set"] is None or (weight, reps) > h["best_set"]:
                h["best_set"] = (weight, reps)
            seen[exercise_id] = order

    for h in history.values():
        pts = h["points"]
        best_weight, best_reps = h["best_set"]
        h["best_label"] = _format_kg(best_weight)
        h["best_set_label"] = f"{best_weight:g}kg × {best_reps}"
        h["e1rm_label"] = _format_kg(max(p["e1rm"] for p in pts))
        h["volume_label"] = _format_kg(h.pop("volume"))
        h["sessions"] = len(pts)
        h["is_pr"] = pts[-1]["weight_kg"] >= best_weight
        h["delta_kg"] = round(pts[-1]["weight_kg"] - pts[0]["weight_kg"], 1)
        h["delta_label"] = f"{abs(h['delta_kg']):g}"
        h["first_label"] = pts[0]["label"]
        del h["best_set"]

    plans = []
    for name in sorted(plan_last_seen, key=plan_last_seen.get, reverse=True):
        seen = plan_exercise_seen[name]
        ordered = sorted(seen, key=lambda eid: (seen[eid], len(history[eid]["points"])), reverse=True)
        plans.append({"name": name, "exercises": [history[eid] for eid in ordered]})

    # --- aderência por plano (séries feitas vs. meta do plano) ---
    adherence_by_plan: dict[str, list[int]] = defaultdict(list)
    for ws in sessions:
        pct = adherence_pct(ws)
        if pct is not None:
            adherence_by_plan[_plan_name(ws)].append(pct)
    adherence = sorted(
        (
            {"name": name, "pct": round(sum(vals) / len(vals)), "sessions": len(vals)}
            for name, vals in adherence_by_plan.items()
        ),
        key=lambda a: a["pct"],
        reverse=True,
    )

    # --- frequência semanal (últimas N semanas) ---
    frequency = []
    for i in range(FREQUENCY_WEEKS - 1, -1, -1):
        week = this_week - timedelta(weeks=i)
        week_sessions = [ws for ws in sessions if _week_start(_local(ws.started_at).date()) == week]
        frequency.append({
            "label": "Esta" if i == 0 else week.strftime("%d/%m"),
            "count": len(week_sessions),
            "volume": round(sum(volume_by_session[ws.id] for ws in week_sessions)),
        })

    # --- mapa de calor (últimas semanas, uma coluna por semana, seg→dom) ---
    volume_by_day: dict[date, float] = defaultdict(float)
    for ws in sessions:
        volume_by_day[_local(ws.started_at).date()] += volume_by_session[ws.id]
    max_day_volume = max(volume_by_day.values(), default=0) or 1
    heatmap_start = this_week - timedelta(weeks=HEATMAP_WEEKS - 1)
    heatmap = []
    for w in range(HEATMAP_WEEKS):
        column = []
        for d in range(7):
            day = heatmap_start + timedelta(weeks=w, days=d)
            vol = volume_by_day.get(day, 0)
            level = 0 if vol <= 0 else max(1, min(4, math.ceil(vol / max_day_volume * 4)))
            column.append({
                "level": level,
                "future": day > today,
                "title": f"{day.strftime('%d/%m')} · " + (f"{_format_kg(vol)} kg" if vol else "sem treino"),
            })
        heatmap.append(column)
    trained_days = sum(1 for day in volume_by_day if day >= heatmap_start)

    # --- volume por treino (últimas sessões) ---
    volume_series = [
        {"label": _local(ws.started_at).strftime("%d/%m"), "volume": round(volume_by_session[ws.id]), "plan": _plan_name(ws)}
        for ws in sessions[-VOLUME_SESSIONS:]
    ]

    # --- treinos mais feitos (pódio) ---
    ranking = sorted(plan_session_count.items(), key=lambda kv: kv[1], reverse=True)[:PODIUM_SIZE]
    max_count = ranking[0][1]
    podium = [
        {"rank": i + 1, "name": name, "count": count, "height_pct": max(28, round(count / max_count * 100))}
        for i, (name, count) in enumerate(ranking)
    ]

    return {
        "user": user,
        "member_since": member_since,
        "has_data": True,
        "calendar": calendar,
        "last_session": last_session,
        "plans": plans,
        "records": list(reversed(records))[:RECORDS_LIMIT],
        "adherence": adherence,
        "frequency": frequency,
        "heatmap": heatmap,
        "heatmap_weeks": HEATMAP_WEEKS,
        "trained_days": trained_days,
        "volume_series": volume_series,
        "podium": podium,
    }
