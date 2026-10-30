"""Ferramentas do agente de `idle`."""
import logging
from typing import Any

from google.genai import types

from agent import billing
from agent.config import settings
from agent.models import User
from agent.repos import plan_repo
from agent.signing import SigningKeyMissing, sign_result_link

from ..context.idle import IdleContext

logger = logging.getLogger("agent.tools.idle")


def start_planning(db: Any, user: User, ctx: IdleContext) -> dict:
    return {"ok": True, "next_state": "planning"}


def manage_plans(db: Any, user: User, ctx: IdleContext) -> dict:
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


def assinar_pro(db: Any, user: User, ctx: IdleContext) -> dict:
    return billing.pro_charge_result(db, user)


def mostrar_resultados(db: Any, user: User, ctx: IdleContext) -> dict:
    try:
        link = sign_result_link(user.id)
    except SigningKeyMissing:
        logger.error("mostrar_resultados: DASHBOARD_SIGNING_KEY não configurada — link não gerado")
        return {"ok": False, "error": "Não consegui gerar o link dos resultados agora, tenta de novo mais tarde. 🙏"}
    url = f"{settings.public_base_url}/webhook/dashboard?user_id={link['user_id']}&exp={link['exp']}&sig={link['sig']}"
    return {"ok": True, "url": url}


TOOL_IMPLS = {
    "start_planning": start_planning,
    "start_session": start_session,
    "manage_plans": manage_plans,
    "list_plans": list_plans,
    "list_plan_exercises": list_plan_exercises,
    "mostrar_resultados": mostrar_resultados,
    "assinar_pro": assinar_pro,
}


def build_declarations(ctx: IdleContext) -> list[types.FunctionDeclaration]:
    decls = [
        types.FunctionDeclaration(
            name="start_planning",
            description="O usuário quer planejar/criar um plano de treino NOVO, do zero.",
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
        types.FunctionDeclaration(
            name="mostrar_resultados",
            description="Gera um link pra página de resultados/progresso do usuário.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="assinar_pro",
            description=(
                "O usuário quer assinar/contratar/pagar o plano Pro. Gera um Pix de cobrança "
                "(não ativa nada: o Pro só é ativado depois que o pagamento for confirmado)."
            ),
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
        decls.append(types.FunctionDeclaration(
            name="manage_plans",
            description=(
                "O usuário quer editar, excluir ou gerenciar de algum jeito um plano de treino "
                "que JÁ EXISTE (ex.: 'exclua o plano de peito') — não é pra criar um plano novo."
            ),
            parameters=types.Schema(type="OBJECT", properties={}),
        ))
    return decls
