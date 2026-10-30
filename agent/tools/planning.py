"""Ferramentas do agente de `planning`."""
import logging
from typing import Any

from google.genai import types

from agent import billing
from agent.billing import PlanLimits
from agent.models import User
from agent.repos import plan_repo

from ..context.planning import PlanningContext

logger = logging.getLogger("agent.tools.planning")

MAX_EXERCISES_PER_CALL = 20


def _set_draft(user: User, draft: dict) -> None:
    user.state_data = {**(user.state_data or {}), "plan_draft": draft}


def _clear_draft(user: User) -> None:
    user.state_data = {k: v for k, v in (user.state_data or {}).items() if k != "plan_draft"}


def _set_pending(user: User, action: dict) -> None:
    user.state_data = {**(user.state_data or {}), "pending_action": action}


def _clear_pending(user: User) -> None:
    user.state_data = {k: v for k, v in (user.state_data or {}).items() if k != "pending_action"}


def _plans_limit_error(limits: PlanLimits) -> dict:
    return {
        "ok": False,
        "limit_reached": "plans",
        "error": (
            f"Limite do plano Grátis: até {limits.max_plans} plano ativo, e o usuário já tem "
            f"{limits.active_plans}. Pra criar outro ele precisa excluir um plano ou assinar o Pro "
            "(planos ilimitados). Nada foi criado."
        ),
    }


def add_to_draft(
    db: Any, user: User, ctx: PlanningContext, exercises: list[dict], name: str | None = None
) -> dict:
    if not exercises:
        return {"ok": False, "error": "Nenhum exercício informado."}
    if len(exercises) > MAX_EXERCISES_PER_CALL:
        return {"ok": False, "error": f"Máximo de {MAX_EXERCISES_PER_CALL} exercícios por chamada."}

    limits = ctx.limits
    draft = ctx.draft
    if draft is None:
        if limits.plans_full:
            return _plans_limit_error(limits)
        plan_name = (name or "").strip()[:60]
        if not plan_name:
            return {"ok": False, "error": "Ainda não há rascunho — informe o nome do plano junto com os exercícios."}
        draft = {"name": plan_name, "items": []}

    validated_items = []
    for i, item in enumerate(exercises, start=1):
        ex_name = (item.get("name") or "").strip()[:60]
        target = item.get("target_sets")
        if not ex_name:
            return {"ok": False, "error": f"Exercício {i}: nome vazio."}
        if target is None or not (1 <= int(target) <= 20):
            return {"ok": False, "error": f"Exercício {i} ({ex_name}): quantidade de séries inválida (1 a 20)."}
        validated_items.append({"name": ex_name, "target_sets": int(target)})

    # recusa a chamada inteira em vez de aceitar só parte: nada entra no plano sem o usuário saber
    max_ex = limits.max_exercises_per_plan
    if max_ex is not None and len(draft["items"]) + len(validated_items) > max_ex:
        room = max(0, max_ex - len(draft["items"]))
        return {
            "ok": False,
            "limit_reached": "exercises",
            "error": (
                f"Limite do plano Grátis: até {max_ex} exercícios por plano. O rascunho tem "
                f"{len(draft['items'])} e o usuário tentou adicionar {len(validated_items)} — cabem só mais "
                f"{room}. Nenhum exercício desta mensagem foi adicionado. Ele pode mandar só {room} "
                "exercício(s), salvar assim, ou assinar o Pro pra ter exercícios ilimitados."
            ),
        }

    draft = {**draft, "items": draft["items"] + validated_items}
    _set_draft(user, draft)

    return {"ok": True, "plan_name": draft["name"], "items": draft["items"]}


def save_draft(db: Any, user: User, ctx: PlanningContext) -> dict:
    if ctx.draft is None or not ctx.draft.get("items"):
        return {"ok": False, "error": "Não há nenhum rascunho com exercícios pra salvar."}

    # última barreira: confere de novo na hora de gravar
    limits = ctx.limits
    if limits.plans_full:
        return _plans_limit_error(limits)
    max_ex = limits.max_exercises_per_plan
    if max_ex is not None and len(ctx.draft["items"]) > max_ex:
        return {
            "ok": False,
            "limit_reached": "exercises",
            "error": f"Limite do plano Grátis: até {max_ex} exercícios por plano, e o rascunho tem "
                     f"{len(ctx.draft['items'])}. Ele precisa tirar exercícios ou assinar o Pro.",
        }

    plan = plan_repo.save_plan(db, user, ctx.draft)
    name = ctx.draft["name"]
    _clear_draft(user)
    return {"ok": True, "next_state": "idle", "plan_name": name, "plan_id": plan.id}


def discard_draft(db: Any, user: User, ctx: PlanningContext) -> dict:
    if ctx.draft is None:
        return {"ok": False, "error": "Não há nenhum rascunho pra descartar."}
    _clear_draft(user)
    return {"ok": True, "next_state": "idle"}


def list_plans(db: Any, user: User, ctx: PlanningContext) -> dict:
    if not ctx.plans:
        return {"ok": True, "plans": [], "note": "nenhum plano salvo ainda"}
    return {"ok": True, "plans": [p.name for p in ctx.plans]}


def list_plan_exercises(db: Any, user: User, ctx: PlanningContext, plan_id: str) -> dict:
    info = ctx.find_plan(plan_id)
    if info is None:
        return {"ok": False, "error": f"plan_id inválido: {plan_id!r}. IDs válidos: {ctx.plan_ids()}"}
    items = [
        {"name": ex.name, "target_sets": pe.target_sets}
        for pe, ex in plan_repo.get_plan_exercises(db, info.id)
    ]
    return {"ok": True, "plan_name": info.name, "exercises": items}


def request_delete_plan(db: Any, user: User, ctx: PlanningContext, plan_id: str) -> dict:
    info = ctx.find_plan(plan_id)
    if info is None:
        return {"ok": False, "error": f"plan_id inválido: {plan_id!r}. IDs válidos: {ctx.plan_ids()}"}

    _set_pending(user, {"type": "delete_plan", "plan_id": info.id, "plan_name": info.name})
    return {"ok": True, "requires_confirmation": True, "plan_name": info.name}


def confirm_pending_action(db: Any, user: User, ctx: PlanningContext) -> dict:
    pending = ctx.pending_action
    if not pending:
        return {"ok": False, "error": "Não há nenhuma ação pendente de confirmação."}

    if pending.get("type") == "delete_plan":
        plan = plan_repo.get_user_plan(db, user, pending.get("plan_id"))
        if plan is None:
            _clear_pending(user)
            return {"ok": False, "error": "Esse plano não existe mais."}
        plan_repo.archive_plan(db, plan)
        _clear_pending(user)
        return {"ok": True, "action": "delete_plan", "plan_name": plan.name, "next_state": "idle"}

    return {"ok": False, "error": f"Tipo de ação pendente desconhecido: {pending.get('type')!r}"}


def cancel_pending_action(db: Any, user: User, ctx: PlanningContext) -> dict:
    if not ctx.pending_action:
        return {"ok": False, "error": "Não há nenhuma ação pendente."}
    _clear_pending(user)
    return {"ok": True}


def go_to_session(db: Any, user: User, ctx: PlanningContext) -> dict:
    if ctx.draft is not None:
        items = ", ".join(f"{i['name']} ({i['target_sets']}x)" for i in ctx.draft["items"])
        return {
            "ok": False,
            "error": (
                f"Há um plano '{ctx.draft['name']}' ainda não salvo (exercícios: {items or 'nenhum ainda'}). "
                "Salve ou descarte esse rascunho antes de começar um treino."
            ),
        }
    return {"ok": True, "next_state": "session_setup"}


def assinar_pro(db: Any, user: User, ctx: PlanningContext) -> dict:
    return billing.pro_charge_result(db, user)


TOOL_IMPLS = {
    "add_to_draft": add_to_draft,
    "save_draft": save_draft,
    "discard_draft": discard_draft,
    "list_plans": list_plans,
    "list_plan_exercises": list_plan_exercises,
    "request_delete_plan": request_delete_plan,
    "confirm_pending_action": confirm_pending_action,
    "cancel_pending_action": cancel_pending_action,
    "go_to_session": go_to_session,
    "assinar_pro": assinar_pro,
}


def build_declarations(ctx: PlanningContext) -> list[types.FunctionDeclaration]:
    decls = [
        types.FunctionDeclaration(
            name="add_to_draft",
            description=(
                "Cria (se ainda não existir) ou acrescenta exercícios ao rascunho de plano de "
                "treino que está sendo montado."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(
                        type="STRING",
                        description="Nome do plano — obrigatório só se ainda não houver rascunho.",
                    ),
                    "exercises": types.Schema(
                        type="ARRAY",
                        items=types.Schema(
                            type="OBJECT",
                            properties={
                                "name": types.Schema(type="STRING"),
                                "target_sets": types.Schema(type="INTEGER", minimum=1, maximum=20),
                            },
                            required=["name", "target_sets"],
                        ),
                    ),
                },
                required=["exercises"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_plans",
            description="Lista os planos de treino já salvos.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
    ]

    if ctx.draft is not None and ctx.draft.get("items"):
        decls.append(types.FunctionDeclaration(
            name="save_draft",
            description="Salva de vez o rascunho de plano que está sendo montado.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))
    if ctx.draft is not None:
        decls.append(types.FunctionDeclaration(
            name="discard_draft",
            description="Descarta o rascunho de plano que está sendo montado, sem salvar.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))

    if ctx.plans:
        decls.append(types.FunctionDeclaration(
            name="list_plan_exercises",
            description="Mostra os exercícios de um plano salvo específico.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"plan_id": types.Schema(type="STRING", enum=ctx.plan_ids())},
                required=["plan_id"],
            ),
        ))
        decls.append(types.FunctionDeclaration(
            name="request_delete_plan",
            description="Propõe excluir um plano salvo. Ação destrutiva: só prepara, não apaga ainda.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"plan_id": types.Schema(type="STRING", enum=ctx.plan_ids())},
                required=["plan_id"],
            ),
        ))

    if ctx.pending_action:
        decls.append(types.FunctionDeclaration(
            name="confirm_pending_action",
            description="Confirma e executa de fato a ação pendente (ex.: exclusão de plano já proposta).",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))
        decls.append(types.FunctionDeclaration(
            name="cancel_pending_action",
            description="Cancela a ação pendente, sem executar nada.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ))

    if not ctx.limits.is_pro:
        decls.append(types.FunctionDeclaration(
            name="assinar_pro",
            description=(
                "O usuário quer assinar/contratar/pagar o plano Pro (ex.: depois de bater o limite do "
                "plano Grátis). Gera um Pix de cobrança; o Pro só é ativado quando o pagamento cair."
            ),
            parameters=types.Schema(type="OBJECT", properties={}),
        ))

    decls.append(types.FunctionDeclaration(
        name="go_to_session",
        description="O usuário quer começar/registrar um treino agora, em vez de planejar.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ))

    return decls
