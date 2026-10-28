"""Ferramentas do agente de `session_setup`."""
import logging
from typing import Any

from google.genai import types

from agent.models import User
from agent.repos import session_repo

from ..context.session_setup import SessionSetupContext

logger = logging.getLogger("agent.tools.session_setup")


def start_free_session(db: Any, user: User, ctx: SessionSetupContext) -> dict:
    session = session_repo.start_session(db, user, plan_id=None)
    user.state_data = {"session_id": session.id}
    return {"ok": True, "next_state": "session", "mode": "free"}


def start_session_with_plan(db: Any, user: User, ctx: SessionSetupContext, plan_id: str) -> dict:
    info = ctx.find_plan(plan_id)
    if info is None:
        return {"ok": False, "error": f"plan_id inválido: {plan_id!r}. IDs válidos: {ctx.plan_ids()}"}
    session = session_repo.start_session(db, user, plan_id=info.id)
    user.state_data = {"session_id": session.id}
    return {"ok": True, "next_state": "session", "plan_name": info.name}


def go_back(db: Any, user: User, ctx: SessionSetupContext) -> dict:
    return {"ok": True, "next_state": "idle"}


def go_to_planning(db: Any, user: User, ctx: SessionSetupContext) -> dict:
    return {"ok": True, "next_state": "planning"}


TOOL_IMPLS = {
    "start_free_session": start_free_session,
    "start_session_with_plan": start_session_with_plan,
    "go_back": go_back,
    "go_to_planning": go_to_planning,
}


def build_declarations(ctx: SessionSetupContext) -> list[types.FunctionDeclaration]:
    decls = [
        types.FunctionDeclaration(
            name="start_free_session",
            description="Inicia um treino livre, sem seguir nenhum plano salvo.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
    ]
    if ctx.plans:
        decls.append(types.FunctionDeclaration(
            name="start_session_with_plan",
            description="Inicia um treino seguindo um plano salvo específico.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"plan_id": types.Schema(type="STRING", enum=ctx.plan_ids())},
                required=["plan_id"],
            ),
        ))
    decls.append(types.FunctionDeclaration(
        name="go_back",
        description="O usuário quer voltar, desistir de começar um treino agora.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ))
    decls.append(types.FunctionDeclaration(
        name="go_to_planning",
        description="O usuário quer mexer nos planos de treino (criar/ver/excluir) em vez de começar um treino agora.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ))
    return decls
