"""Ferramentas do agente de `idle`."""
import logging
from typing import Any

from google.genai import types

from webhook.models import User
from webhook.repos import plan_repo

from ..context.idle import IdleContext

logger = logging.getLogger("agent.tools.idle")


def start_planning(db: Any, user: User, ctx: IdleContext) -> dict:
    return {"ok": True, "next_state": "planning"}


def start_session(db: Any, user: User, ctx: IdleContext) -> dict:
    return {"ok": True, "next_state": "session_setup"}


def list_plans(db: Any, user: User, ctx: IdleContext) -> dict:
    if not ctx.plans:
        return {"ok": True, "plans": [], "note": "nenhum plano salvo ainda"}
    return {"ok": True, "plans": [p.name for p in ctx.plans]}


def list_plan_exercises(db: Any, user: User, ctx: IdleContext, plan_id: str) -> dict:
    info = ctx.find_plan(plan_id)
    if info is None:
        return {"ok": False, "error": f"plan_id inválido: {plan_id!r}. IDs válidos: {ctx.plan_ids()}"}

    items = [
        {"name": ex.name, "target_sets": pe.target_sets}
        for pe, ex in plan_repo.get_plan_exercises(db, info.id)
    ]
    return {"ok": True, "plan_name": info.name, "exercises": items}


TOOL_IMPLS = {
    "start_planning": start_planning,
    "start_session": start_session,
    "list_plans": list_plans,
    "list_plan_exercises": list_plan_exercises,
}


def build_declarations(ctx: IdleContext) -> list[types.FunctionDeclaration]:
    decls = [
        types.FunctionDeclaration(
            name="start_planning",
            description="O usuário quer planejar/criar um plano de treino novo.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="start_session",
            description="O usuário quer começar/registrar um treino agora.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="list_plans",
            description="Lista os planos de treino salvos do usuário, sem sair do menu principal.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
    ]
    if ctx.plans:
        decls.append(types.FunctionDeclaration(
            name="list_plan_exercises",
            description="Mostra os exercícios de um plano salvo específico, sem sair do menu principal.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"plan_id": types.Schema(type="STRING", enum=ctx.plan_ids())},
                required=["plan_id"],
            ),
        ))
    return decls
