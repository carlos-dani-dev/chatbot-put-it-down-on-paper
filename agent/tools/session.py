"""Ferramentas do agente de `session`: declarações (pro Gemini) + execução validada.

Princípio central (ver conversa de referência): o modelo nunca é dono da
decisão. Ele propõe uma chamada de ferramenta usando só os IDs que aparecem
no contexto (nunca nome livre); o código valida tudo de novo antes de ler ou
gravar qualquer coisa no banco. Ação destrutiva (excluir série) nunca executa
na mesma chamada — fica pendente em `user.state_data["pending_action"]` até
uma confirmação explícita separada.
"""
import logging
from typing import Any

from google.genai import types

from agent.models import Exercise, User, WorkoutSession, WorkoutSet
from agent.repos import exercise_repo, plan_repo, session_repo

from ..context.session import SessionContext

logger = logging.getLogger("agent.tools.session")

MAX_SETS_PER_CALL = 20


def _set_pending(user: User, action: dict) -> None:
    user.state_data = {**(user.state_data or {}), "pending_action": action}


def _clear_pending(user: User) -> None:
    user.state_data = {k: v for k, v in (user.state_data or {}).items() if k != "pending_action"}


# --------------------------------------------------------------------------
# Execução (cada função: (db, user, ws, ctx, **kwargs) -> dict JSON-serializável)
# --------------------------------------------------------------------------

def log_sets(
    db: Any,
    user: User,
    ws: WorkoutSession,
    ctx: SessionContext,
    exercise_id: str,
    sets: list[dict],
    new_exercise_name: str | None = None,
) -> dict:
    if not sets:
        return {"ok": False, "error": "Nenhuma série informada."}
    if len(sets) > MAX_SETS_PER_CALL:
        return {"ok": False, "error": f"Máximo de {MAX_SETS_PER_CALL} séries por chamada."}

    plan_exercise_id = None
    if exercise_id == "new":
        name = (new_exercise_name or "").strip()[:60]
        if not name:
            return {"ok": False, "error": "exercise_id é 'new' mas new_exercise_name não foi informado."}
        exercise, _ = exercise_repo.find_or_create(db, name)
    else:
        info = ctx.find_exercise(exercise_id)
        if info is None:
            return {
                "ok": False,
                "error": f"exercise_id inválido: {exercise_id!r}. IDs válidos: {ctx.exercise_ids()} ou 'new'.",
            }
        exercise = db.get(Exercise, info.id)
        if ws.plan_id is not None:
            pe = next(
                (pe for pe, ex in plan_repo.get_plan_exercises(db, ws.plan_id) if ex.id == exercise.id),
                None,
            )
            plan_exercise_id = pe.id if pe is not None else None

    validated: list[tuple[float, int, int | None]] = []
    for i, raw in enumerate(sets, start=1):
        weight = raw.get("weight_kg")
        reps = raw.get("reps")
        rest = raw.get("rest_seconds")

        if weight is None or not (0 <= float(weight) <= 500):
            return {"ok": False, "error": f"Série {i}: carga inválida (precisa estar entre 0 e 500kg)."}
        if reps is None or not (1 <= int(reps) <= 100):
            return {"ok": False, "error": f"Série {i}: repetições inválidas (precisa estar entre 1 e 100)."}
        if rest is not None and not (0 <= int(rest) <= 3600):
            rest = None

        validated.append((float(weight), int(reps), int(rest) if rest is not None else None))

    first, last = session_repo.log_sets(db, ws, exercise.id, plan_exercise_id, validated, raw_text="(agent tool call)")
    done = session_repo.count_sets(db, ws.id, exercise.id)
    target = next((e.target_sets for e in ctx.exercises if e.id == exercise.id), None)

    return {
        "ok": True,
        "exercise_name": exercise.name,
        "sets_logged": [{"weight_kg": w, "reps": r} for w, r, _ in validated],
        "set_range": [first, last],
        "total_done": done,
        "target_sets": target,
    }


def request_delete_sets(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext, set_ids: list[str]) -> dict:
    valid_ids, bad = ctx.find_set_ids(set_ids)
    if not valid_ids:
        return {
            "ok": False,
            "error": f"Nenhum ID de série válido foi reconhecido. Rejeitados: {bad}. IDs válidos nesta sessão: {ctx.set_ids()}",
        }

    targets = [s for s in ctx.sets if s.id in valid_ids]
    preview = [
        f"{s.exercise_name} (série {s.set_number}: {s.weight_kg:g}kg x {s.reps})" for s in targets
    ]

    _set_pending(user, {"type": "delete_sets", "set_ids": valid_ids})

    return {
        "ok": True,
        "requires_confirmation": True,
        "preview": preview,
        "rejected_ids": bad,
    }


def undo_last_set(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext, exercise_id: str) -> dict:
    info = ctx.find_exercise(exercise_id)
    if info is None:
        return {"ok": False, "error": f"exercise_id inválido: {exercise_id!r}. IDs válidos: {ctx.exercise_ids()}"}

    candidates = [s for s in ctx.sets if s.exercise_id == info.id]
    if not candidates:
        return {"ok": False, "error": f"Nenhuma série registrada de {info.name} nesta sessão."}

    last = max(candidates, key=lambda s: s.set_number)
    return request_delete_sets(db, user, ws, ctx, [f"set:{last.id}"])


def confirm_pending_action(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext) -> dict:
    pending = ctx.pending_action
    if not pending:
        return {"ok": False, "error": "Não há nenhuma ação pendente de confirmação."}

    if pending.get("type") == "delete_sets":
        ids = pending.get("set_ids", [])
        rows = db.query(WorkoutSet).filter(
            WorkoutSet.id.in_(ids),
            WorkoutSet.session_id == ws.id,
        ).all()
        found_ids = {r.id for r in rows}
        missing = [i for i in ids if i not in found_ids]
        for row in rows:
            db.delete(row)
        db.flush()
        _clear_pending(user)
        return {"ok": True, "action": "delete_sets", "deleted_count": len(rows), "missing_ids": missing}

    return {"ok": False, "error": f"Tipo de ação pendente desconhecido: {pending.get('type')!r}"}


def cancel_pending_action(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext) -> dict:
    if not ctx.pending_action:
        return {"ok": False, "error": "Não há nenhuma ação pendente."}
    _clear_pending(user)
    return {"ok": True}


def finish_session(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext) -> dict:
    n = session_repo.close_session(db, ws)
    return {"ok": True, "sets_registered": n, "next_state": "idle"}


def list_exercises(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext) -> dict:
    if not ctx.exercises:
        return {"ok": True, "exercises": [], "note": "Treino livre, sem plano — nenhum exercício pré-definido ainda."}
    return {
        "ok": True,
        "exercises": [
            {"name": e.name, "done_sets": e.done_sets, "target_sets": e.target_sets}
            for e in ctx.exercises
        ],
    }


def check_performance(db: Any, user: User, ws: WorkoutSession, ctx: SessionContext, exercise_id: str) -> dict:
    info = ctx.find_exercise(exercise_id)
    if info is None:
        return {"ok": False, "error": f"exercise_id inválido: {exercise_id!r}. IDs válidos: {ctx.exercise_ids()}"}

    sets = session_repo.get_last_session_sets(db, user, info.id, exclude_session_id=ws.id)
    if not sets:
        return {"ok": True, "has_history": False, "exercise_name": info.name}

    return {
        "ok": True,
        "has_history": True,
        "exercise_name": info.name,
        "sets": [{"weight_kg": float(s.weight_kg), "reps": s.reps} for s in sets],
    }


TOOL_IMPLS = {
    "log_sets": log_sets,
    "request_delete_sets": request_delete_sets,
    "undo_last_set": undo_last_set,
    "confirm_pending_action": confirm_pending_action,
    "cancel_pending_action": cancel_pending_action,
    "finish_session": finish_session,
    "list_exercises": list_exercises,
    "check_performance": check_performance,
}


# --------------------------------------------------------------------------
# Declarações dinâmicas (enums construídos a cada turno a partir do contexto real)
# --------------------------------------------------------------------------

def build_declarations(ctx: SessionContext) -> list[types.FunctionDeclaration]:
    decls: list[types.FunctionDeclaration] = []

    exercise_enum = ctx.exercise_ids() + ["new"]
    decls.append(types.FunctionDeclaration(
        name="log_sets",
        description="Registra uma ou mais séries de um exercício que o usuário acabou de fazer.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "exercise_id": types.Schema(
                    type="STRING",
                    enum=exercise_enum,
                    description="Um dos IDs de exercício do contexto, ou 'new' se não for nenhum deles.",
                ),
                "new_exercise_name": types.Schema(
                    type="STRING",
                    description="Nome do exercício novo — só preencher se exercise_id for 'new'.",
                ),
                "sets": types.Schema(
                    type="ARRAY",
                    description="Cada série feita (ordem cronológica).",
                    items=types.Schema(
                        type="OBJECT",
                        properties={
                            "weight_kg": types.Schema(type="NUMBER", minimum=0, maximum=500),
                            "reps": types.Schema(type="INTEGER", minimum=1, maximum=100),
                            "rest_seconds": types.Schema(type="INTEGER", minimum=0, maximum=3600, nullable=True),
                        },
                        required=["weight_kg", "reps"],
                    ),
                ),
            },
            required=["exercise_id", "sets"],
        ),
    ))

    if ctx.sets:
        decls.append(types.FunctionDeclaration(
            name="request_delete_sets",
            description=(
                "Propõe excluir uma ou mais séries JÁ REGISTRADAS. Ação destrutiva: só prepara a "
                "exclusão e pede confirmação, não apaga nada ainda."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "set_ids": types.Schema(
                        type="ARRAY",
                        items=types.Schema(type="STRING", enum=ctx.set_ids()),
                    ),
                },
                required=["set_ids"],
            ),
        ))
        decls.append(types.FunctionDeclaration(
            name="undo_last_set",
            description=(
                "Propõe excluir a última série registrada de um exercício específico. Ação "
                "destrutiva: só prepara a exclusão e pede confirmação, não apaga nada ainda."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "exercise_id": types.Schema(type="STRING", enum=ctx.exercise_ids()),
                },
                required=["exercise_id"],
            ),
        ))

    if ctx.pending_action:
        decls.append(types.FunctionDeclaration(
            name="confirm_pending_action",
            description="Confirma e executa de fato a ação pendente (ex.: a exclusão de séries já proposta).",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))
        decls.append(types.FunctionDeclaration(
            name="cancel_pending_action",
            description="Cancela a ação pendente, sem executar nada.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))

    decls.append(types.FunctionDeclaration(
        name="finish_session",
        description="Encerra o treino de hoje.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ))
    decls.append(types.FunctionDeclaration(
        name="list_exercises",
        description="Lista os exercícios do treino de hoje com o progresso de séries de cada um.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ))

    if ctx.exercises:
        decls.append(types.FunctionDeclaration(
            name="check_performance",
            description="Mostra carga e séries de um exercício na última vez que foi treinado em outra sessão.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"exercise_id": types.Schema(type="STRING", enum=ctx.exercise_ids())},
                required=["exercise_id"],
            ),
        ))

    return decls
