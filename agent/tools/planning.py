"""Ferramentas do agente de `planning`."""
import logging
import unicodedata
from typing import Any

from google.genai import types

from agent import billing
from agent.billing import PlanLimits
from agent.models import User
from agent.repos import plan_repo

from ..context.planning import PlanningContext

logger = logging.getLogger("agent.tools.planning")

MAX_EXERCISES_PER_CALL = 20

# nomes que não são exercício nenhum: a LLM às vezes preenche o campo obrigatório com eles quando o
# usuário só disse "quero adicionar exercícios" (sem dizer quais)
_GENERIC_EXERCISE_NAMES = {"exercicio", "exercicios", "exercise", "exercises", "treino", "treinos", "plano", "serie", "series"}


def _not_an_exercise(ex_name: str, draft_name: str, ctx: PlanningContext) -> bool:
    """Nome que não é de exercício: genérico ("exercícios"), um treino/plano ("treino de Leg 2") ou o
    nome do próprio plano ("Leg 2") — a LLM às vezes preenche o exercício com o que o usuário chamou de treino."""
    norm = _norm(ex_name)
    first_word = norm.split(" ", 1)[0]
    plan_names = {_norm(draft_name)} | {_norm(p.name) for p in ctx.plans}
    return norm in _GENERIC_EXERCISE_NAMES or first_word in {"treino", "plano"} or norm in plan_names


def _norm(name: str) -> str:
    """Pra comparar nomes: sem acento, sem diferenciar maiúsculas, espaços colapsados."""
    no_accents = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(no_accents.casefold().split())


def _saved_plan_named(ctx: PlanningContext, name: str):
    return next((p for p in ctx.plans if _norm(p.name) == _norm(name)), None)


def _duplicate_name_error(plan) -> dict:
    return {
        "ok": False,
        "error": (
            f"Já existe um plano salvo chamado '{plan.name}' (plan_id plan:{plan.id}). Nada foi criado. "
            "Se a pessoa quer mudar esse plano (adicionar/tirar exercícios, mudar séries), chame edit_plan; "
            "se quer mesmo um plano novo, peça outro nome."
        ),
    }


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
            f"{limits.active_plans}. Pra criar outro a pessoa precisa excluir um plano ou assinar o Pro "
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
        plan_name = (name or "").strip()[:60]
        if not plan_name:
            return {"ok": False, "error": "Ainda não há rascunho: informe o nome do plano junto com os exercícios."}
        existing = _saved_plan_named(ctx, plan_name)
        if existing is not None:
            return _duplicate_name_error(existing)
        if limits.plans_full:
            return _plans_limit_error(limits)
        draft = {"name": plan_name, "items": []}

    seen = {_norm(i["name"]) for i in draft["items"]}
    validated_items = []
    for i, item in enumerate(exercises, start=1):
        ex_name = (item.get("name") or "").strip()[:60]
        target = item.get("target_sets")
        if not ex_name:
            return {"ok": False, "error": f"Exercício {i}: nome vazio."}
        if _not_an_exercise(ex_name, draft["name"], ctx):
            return {
                "ok": False,
                "error": (
                    f"'{ex_name}' não é o nome de um exercício. Nada foi adicionado: pergunte ao usuário "
                    "QUAIS exercícios quer e com quantas séries, nunca invente."
                ),
            }
        if _norm(ex_name) in seen:
            return {
                "ok": False,
                "error": (
                    f"'{ex_name}' já está no plano. Nada foi adicionado. Pra mudar as séries dele, chame set_sets."
                ),
            }
        if target is None or not (1 <= int(target) <= 20):
            return {"ok": False, "error": f"Exercício {i} ({ex_name}): quantidade de séries inválida (1 a 20)."}
        seen.add(_norm(ex_name))
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
                f"{len(draft['items'])} e o usuário tentou adicionar {len(validated_items)}: cabem só mais "
                f"{room}. Nenhum exercício desta mensagem foi adicionado. A pessoa pode mandar só {room} "
                "exercício(s), salvar assim, ou assinar o Pro pra ter exercícios ilimitados."
            ),
        }

    draft = {**draft, "items": draft["items"] + validated_items}
    _set_draft(user, draft)

    return {"ok": True, "plan_name": draft["name"], "items": draft["items"], "editing": draft.get("plan_id") is not None}


def edit_plan(db: Any, user: User, ctx: PlanningContext, plan_id: str) -> dict:
    """Abre um plano salvo como rascunho, com os exercícios dele. Salvar atualiza o mesmo plano."""
    if ctx.draft is not None:
        return {
            "ok": False,
            "error": f"Já há um plano '{ctx.draft['name']}' aberto e não salvo. Salve ou descarte antes de editar outro.",
        }
    info = ctx.find_plan(plan_id)
    if info is None:
        return {"ok": False, "error": f"plan_id inválido: {plan_id!r}. IDs válidos: {ctx.plan_ids()}"}

    items = [{"name": ex.name, "target_sets": pe.target_sets} for pe, ex in plan_repo.get_plan_exercises(db, info.id)]
    _set_draft(user, {"name": info.name, "items": items, "plan_id": info.id})
    return {"ok": True, "editing": info.name, "items": items}


def _find_items(draft: dict, names: list[str]) -> tuple[list[int], list[str]]:
    by_name = {_norm(item["name"]): i for i, item in enumerate(draft["items"])}
    found, missing = [], []
    for name in names:
        index = by_name.get(_norm(name or ""))
        (missing.append(name) if index is None else found.append(index))
    return found, missing


def remove_from_draft(db: Any, user: User, ctx: PlanningContext, exercises: list[str]) -> dict:
    draft = ctx.draft
    if draft is None:
        return {"ok": False, "error": "Não há nenhum plano aberto. Pra mexer num plano salvo, chame edit_plan antes."}
    if not exercises:
        return {"ok": False, "error": "Nenhum exercício informado."}

    found, missing = _find_items(draft, exercises)
    if missing:  # tudo ou nada: não tira só parte sem o usuário saber
        current = [item["name"] for item in draft["items"]]
        return {"ok": False, "error": f"Não estão no plano: {missing}. Nada foi removido. Exercícios do plano: {current}."}

    items = [item for i, item in enumerate(draft["items"]) if i not in set(found)]
    _set_draft(user, {**draft, "items": items})
    return {"ok": True, "plan_name": draft["name"], "items": items, "editing": draft.get("plan_id") is not None}


def set_sets(db: Any, user: User, ctx: PlanningContext, exercise: str, target_sets: int) -> dict:
    draft = ctx.draft
    if draft is None:
        return {"ok": False, "error": "Não há nenhum plano aberto. Pra mexer num plano salvo, chame edit_plan antes."}
    if target_sets is None or not (1 <= int(target_sets) <= 20):
        return {"ok": False, "error": "Quantidade de séries inválida (1 a 20)."}

    found, _missing = _find_items(draft, [exercise])
    if not found:
        current = [item["name"] for item in draft["items"]]
        return {"ok": False, "error": f"'{exercise}' não está no plano. Exercícios do plano: {current}."}

    items = [dict(item) for item in draft["items"]]
    items[found[0]]["target_sets"] = int(target_sets)
    _set_draft(user, {**draft, "items": items})
    return {"ok": True, "plan_name": draft["name"], "items": items, "editing": draft.get("plan_id") is not None}


def save_draft(db: Any, user: User, ctx: PlanningContext) -> dict:
    if ctx.draft is None or not ctx.draft.get("items"):
        return {"ok": False, "error": "Não há nenhum rascunho com exercícios pra salvar."}

    # última barreira: confere de novo na hora de gravar
    limits = ctx.limits
    editing_id = ctx.draft.get("plan_id")
    if editing_id is None:
        existing = _saved_plan_named(ctx, ctx.draft["name"])
        if existing is not None:
            return _duplicate_name_error(existing)
        if limits.plans_full:  # editar não cria plano: só o plano novo esbarra no limite de planos
            return _plans_limit_error(limits)
    max_ex = limits.max_exercises_per_plan
    if max_ex is not None and len(ctx.draft["items"]) > max_ex:
        return {
            "ok": False,
            "limit_reached": "exercises",
            "error": f"Limite do plano Grátis: até {max_ex} exercícios por plano, e o rascunho tem "
                     f"{len(ctx.draft['items'])}. A pessoa precisa tirar exercícios ou assinar o Pro.",
        }

    if editing_id is not None:
        plan = plan_repo.get_user_plan(db, user, editing_id)
        if plan is None or plan.archived_at is not None:
            _clear_draft(user)
            return {"ok": False, "error": "Esse plano foi excluído enquanto era editado. Nada foi salvo."}
        plan_repo.update_plan(db, plan, ctx.draft)
    else:
        plan = plan_repo.save_plan(db, user, ctx.draft)
    name = ctx.draft["name"]
    _clear_draft(user)
    return {"ok": True, "next_state": "idle", "plan_name": name, "plan_id": plan.id, "updated": editing_id is not None}


def discard_draft(db: Any, user: User, ctx: PlanningContext) -> dict:
    if ctx.draft is None:
        return {"ok": False, "error": "Não há nenhum rascunho pra descartar."}
    _clear_draft(user)
    if ctx.draft.get("plan_id") is not None:
        return {"ok": True, "next_state": "idle", "plan_name": ctx.draft["name"], "editing": True,
                "note": f"edição descartada: o plano '{ctx.draft['name']}' continua como estava"}
    return {"ok": True, "next_state": "idle", "plan_name": ctx.draft["name"], "editing": False}


def list_plans(db: Any, user: User, ctx: PlanningContext) -> dict:
    if not ctx.plans:
        return {"ok": True, "plans": [], "note": "nenhum plano salvo ainda"}
    return {"ok": True, "plans": [{"plan_id": f"plan:{p.id}", "name": p.name} for p in ctx.plans]}


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


# --------------------------------------------------------------------------
# Respostas montadas em código. Depois de uma ação que deu certo, a resposta é só "como o plano
# ficou + próximos passos" — uma 2ª chamada à LLM pra escrever isso custava o dobro de cada
# mensagem do planejamento. Erros e limites continuam voltando pra LLM explicar.
# --------------------------------------------------------------------------
def _plan_lines(items: list[dict]) -> str:
    return "\n".join(f"• {i['name']} ({i['target_sets']} {'série' if i['target_sets'] == 1 else 'séries'})"
                     for i in items)


def _draft_reply(result: dict, headline: str) -> str:
    name = result["plan_name"]
    if not result["items"]:
        return (f"{headline}\n\nO plano *{name}* ficou sem exercícios. Manda os exercícios com as séries "
                "(ex.: supino 3 séries) ou, se quiser desistir, *Descartar o plano*.")
    status = "em edição, ainda não salvo" if result.get("editing") else "rascunho, ainda não salvo"
    return (f"{headline}\n\n📋 *{name}* ({status}):\n{_plan_lines(result['items'])}\n\n"
            "Quer *Adicionar exercícios*, *Remover um exercício* ou *Salvar o plano*?")


def coded_reply(tool: str, result: dict) -> str | None:
    """A resposta pronta pra uma ferramenta que deu certo, ou None se a LLM precisa responder."""
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    if tool == "add_to_draft":
        return _draft_reply(result, "✅ Anotado!")
    if tool == "remove_from_draft":
        return _draft_reply(result, "🗑️ Tirei do plano.")
    if tool == "set_sets":
        return _draft_reply(result, "✅ Séries atualizadas.")
    if tool == "save_draft":
        verb = "atualizado" if result.get("updated") else "salvo"
        return f"✅ Plano *{result['plan_name']}* {verb}! Quando quiser, é só *Começar um treino* com ele. 💪"
    if tool == "discard_draft":
        if result.get("editing"):
            return f"👍 Descartei as mudanças: o plano *{result['plan_name']}* continua como estava."
        return "👍 Rascunho descartado. Quando quiser, é só *Criar um plano* ou *Começar um treino*."
    if tool == "request_delete_plan":
        return (f"⚠️ Quer mesmo excluir o plano *{result['plan_name']}*? Os treinos feitos com ele deixam de "
                "aparecer na página de resultados.\n\nResponda *sim* pra confirmar ou *não* pra cancelar.")
    if tool == "confirm_pending_action" and result.get("action") == "delete_plan":
        return f"🗑️ Plano *{result['plan_name']}* excluído. Quando quiser, é só *Criar um plano* ou *Começar um treino*."
    if tool == "cancel_pending_action":
        return "👍 Beleza, não excluí nada."
    return None  # edit_plan, listas etc.: a LLM ainda pode ter mais o que fazer com o resultado


def assinar_pro(db: Any, user: User, ctx: PlanningContext) -> dict:
    return billing.pro_charge_result(db, user)


TOOL_IMPLS = {
    "add_to_draft": add_to_draft,
    "edit_plan": edit_plan,
    "remove_from_draft": remove_from_draft,
    "set_sets": set_sets,
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
                "Cria (se ainda não existir) ou acrescenta exercícios ao plano que está aberto: um "
                "rascunho novo ou um plano salvo em edição. Só com exercícios que o usuário nomeou."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(
                        type="STRING",
                        description="Nome do plano, obrigatório só se ainda não houver rascunho.",
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
        names = [item["name"] for item in ctx.draft["items"]]
        decls.append(types.FunctionDeclaration(
            name="remove_from_draft",
            description="Tira exercícios do plano que está aberto (rascunho novo ou plano salvo em edição).",
            parameters=types.Schema(
                type="OBJECT",
                properties={"exercises": types.Schema(type="ARRAY", items=types.Schema(type="STRING", enum=names))},
                required=["exercises"],
            ),
        ))
        decls.append(types.FunctionDeclaration(
            name="set_sets",
            description="Muda a quantidade de séries de um exercício do plano que está aberto.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "exercise": types.Schema(type="STRING", enum=names),
                    "target_sets": types.Schema(type="INTEGER", minimum=1, maximum=20),
                },
                required=["exercise", "target_sets"],
            ),
        ))
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

    if ctx.plans and ctx.draft is None:
        decls.append(types.FunctionDeclaration(
            name="edit_plan",
            description=(
                "Abre um plano JÁ SALVO pra edição (adicionar ou tirar exercícios, mudar séries). "
                "Depois de aberto, use add_to_draft, remove_from_draft e set_sets; save_draft atualiza o mesmo plano."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={"plan_id": types.Schema(type="STRING", enum=ctx.plan_ids())},
                required=["plan_id"],
            ),
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
