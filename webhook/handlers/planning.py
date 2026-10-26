from typing import Any

from ..models import User
from ..schemas.replies import TextReply
from ..schemas.llm_response import ParsedExercises, ParsedPlan

from ._shared import (
    enter_idle,
    enter_planning,
    list_plans_menu,
    no_plans_saved,
    list_exercises_menu,
    plan_exercise_list,
    delete_plan_menu,
    no_plans_to_delete,
    no_exercises_identified,
    draft_menu,
    plan_saved,
    plan_canceled,
    plan_deleted,
    _PRODUCT_FAQ,
)
from ..config import get_llm_client, get_decider_client
from ..repos import plan_repo

llm = get_llm_client()
decider = get_decider_client()

_AWAITING_PLAN_INSTRUCTIONS = (
    "O usuário está descrevendo um plano de treino novo, com nome e exercícios, "
    "quer ver os planos que já tem salvos, quer ver os exercícios de um plano "
    "salvo, quer excluir um plano salvo, quer desistir de planejar, ou pode "
    "estar tirando uma dúvida sobre como o app funciona."
)

_AWAITING_PLAN_CRITERIA = {
    "create_plan": "Está descrevendo um plano de treino, com exercícios",
    "start": "Só confirmou que quer planejar um treino novo, sem citar nome ou exercícios ainda",
    "list_plans": "Quer ver quais planos de treino já tem salvos",
    "list_exercises": "Quer ver quais exercícios tem um dos planos de treino já salvos, ou perguntou como está indo ou qual o status de um plano específico",
    "delete_plan": "Quer excluir um plano de treino já salvo",
    "cancel": "Quer desistir, abortar, parar ou cancelar o planejamento desse treino",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não dá pra saber o que ele quer",
}

_LIST_EXERCISES_INSTRUCTIONS = (
    "O usuário está escolhendo de qual plano de treino salvo ele quer ver os "
    "exercícios, dizendo o nome do plano, ou pode estar mudando de ideia."
)

_DELETE_PLAN_INSTRUCTIONS = (
    "O usuário está escolhendo qual plano de treino salvo ele quer excluir, "
    "dizendo o nome do plano, ou pode estar mudando de ideia e desistindo."
)

_BUILDING_INSTRUCTIONS = (
    "O usuário já tem um rascunho de plano de treino e pode estar adicionando "
    "mais exercícios a ele, ou tirando uma dúvida sobre como o app funciona."
)

_BUILDING_CRITERIA = {
    "add_exercises": "Está citando mais exercícios pra incluir no plano",
    "save": "Quer salvar o plano do jeito que está",
    "cancel": "Quer desistir, abortar, parar, cancelar ou descartar o plano que estava montando",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não é possível identificar exercício nenhum na mensagem",
}

_EXERCISES_PROMPT = (
    "A mensagem a seguir é de um usuário de um app de treino, listando exercícios "
    "pra adicionar a um plano de treino. Extraia cada exercício mencionado e a "
    "quantidade de séries (sets) planejadas pra ele. Não existe meta de repetições, "
    "apenas de séries — ignore qualquer número de repetições mencionado.\n\n"
    "Mensagem: {text}"
)

_PLAN_PROMPT = (
    "A mensagem a seguir é de um usuário de um app de treino, descrevendo um plano "
    "de treino novo. Extraia o nome do plano (se não houver um nome explícito, use "
    "'Treino') e os exercícios mencionados, cada um com a quantidade de séries "
    "(sets) planejadas. Não existe meta de repetições, apenas de séries — ignore "
    "qualquer número de repetições mencionado.\n\n"
    "Mensagem: {text}"
)

def _parse_exercises(text: str) -> list[dict]:
    result = llm.generate(_EXERCISES_PROMPT.format(text=text), schema=ParsedExercises)
    parsed = ParsedExercises.model_validate_json(result)
    return [item.model_dump() for item in parsed.items]


def _parse_plan(text: str) -> tuple[str, list[dict]]:
    result = llm.generate(_PLAN_PROMPT.format(text=text), schema=ParsedPlan)
    parsed = ParsedPlan.model_validate_json(result)
    return parsed.name, [item.model_dump() for item in parsed.items]


def _save_plan(db: Any, user: User, draft: dict) -> tuple[TextReply, str]:
    plan_repo.save_plan(db, user, draft)
    return enter_idle(db, user, plan_saved(db, user, draft))


def _cancel_plan(db: Any, user: User) -> tuple[TextReply, str]:
    return enter_idle(db, user, plan_canceled(db, user))


def handle_planning(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply, str]:

    step = user.state_data.get("step")

    if step == "awaiting_plan":

        answer = decider.choose(
            state=(text or "").lower(),
            instructions=_AWAITING_PLAN_INSTRUCTIONS,
            criteria=_AWAITING_PLAN_CRITERIA,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "question":
                reply = llm.generate(_PRODUCT_FAQ + text)
                return enter_planning(db, user, reply)
            elif answer.choice == "start":
                return enter_planning(db, user)
            elif answer.choice == "list_plans":
                plans = plan_repo.list_active_plans(db, user, limit=8)
                if not plans:
                    return no_plans_saved(db, user), "planning"
                return list_plans_menu(db, user, plans), "planning"
            elif answer.choice == "list_exercises":
                plans = plan_repo.list_active_plans(db, user, limit=8)
                if not plans:
                    return no_plans_saved(db, user), "planning"
                user.state_data = {"step": "listing_exercises"}
                return list_exercises_menu(db, user, plans), "planning"
            elif answer.choice == "delete_plan":
                plans = plan_repo.list_active_plans(db, user, limit=8)
                if not plans:
                    return no_plans_to_delete(db, user), "planning"
                user.state_data = {"step": "deleting_plan"}
                return delete_plan_menu(db, user, plans), "planning"
            elif answer.choice == "cancel":
                return _cancel_plan(db, user)

        plan_name, items = _parse_plan(text)

        if not items:
            return no_exercises_identified(db, user), "planning"

        draft = {"name": plan_name, "items": items}
        user.state_data = {"step": "building", "draft": draft}
        return draft_menu(db, user, draft), "planning"

    elif step == "deleting_plan":

        plans = plan_repo.list_active_plans(db, user, limit=8)
        if not plans:
            return enter_idle(db, user)

        criteria = {f"plan:{p.id}": p.name for p in plans}
        criteria["none"] = "Mudou de ideia, não quer excluir nenhum plano"

        answer = decider.choose(
            state=(text or "").lower(),
            instructions=_DELETE_PLAN_INSTRUCTIONS,
            criteria=criteria,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "none":
                return enter_idle(db, user)
            if answer.choice.startswith("plan:"):
                plan_id = int(answer.choice.split(":", 1)[1])
                plan = plan_repo.get_user_plan(db, user, plan_id)
                if plan is not None:
                    plan_repo.archive_plan(db, plan)
                    return enter_idle(db, user, plan_deleted(db, user, plan.name))

        return delete_plan_menu(db, user, plans), "planning"

    elif step == "listing_exercises":

        plans = plan_repo.list_active_plans(db, user, limit=8)
        if not plans:
            return enter_idle(db, user)

        criteria = {f"plan:{p.id}": p.name for p in plans}
        criteria["none"] = "Mudou de ideia, não quer ver os exercícios de nenhum plano"

        answer = decider.choose(
            state=(text or "").lower(),
            instructions=_LIST_EXERCISES_INSTRUCTIONS,
            criteria=criteria,
        )

        if (answer.confidence or 0) >= 0.6:
            if answer.choice == "none":
                return enter_idle(db, user)
            if answer.choice.startswith("plan:"):
                plan_id = int(answer.choice.split(":", 1)[1])
                plan = plan_repo.get_user_plan(db, user, plan_id)
                if plan is not None:
                    items = ", ".join(
                        f"{ex.name} ({pe.target_sets}x)"
                        for pe, ex in plan_repo.get_plan_exercises(db, plan.id)
                    )
                    user.state_data = {"step": "awaiting_plan"}
                    return plan_exercise_list(db, user, plan.name, items), "planning"

        return list_exercises_menu(db, user, plans, "🤔 Não entendi. "), "planning"

    elif step == "building":

        draft = user.state_data.get("draft")

        if text:
            answer = decider.choose(
                state=text.lower(),
                instructions=_BUILDING_INSTRUCTIONS,
                criteria=_BUILDING_CRITERIA,
            )

            if (answer.confidence or 0) >= 0.6:
                if answer.choice == "save":
                    return _save_plan(db, user, draft)
                elif answer.choice == "cancel":
                    return _cancel_plan(db, user)
                elif answer.choice == "question":
                    reply = llm.generate(_PRODUCT_FAQ + text)
                    return draft_menu(db, user, draft, reply), "planning"

            new_items = _parse_exercises(text)
            if new_items:
                draft = {**draft, "items": draft["items"] + new_items}
                user.state_data = {"step": "building", "draft": draft}
        return draft_menu(db, user, draft), "planning"