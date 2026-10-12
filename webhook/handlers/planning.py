from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply
from ..schemas.llm_response import ParsedExercises, ParsedPlan

from ._shared import enter_idle, idle_menu
from ..config import get_llm_client
from ..repos import plan_repo

llm = get_llm_client()

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


def _draft_menu(draft: dict) -> ButtonsReply:
    lines = [f"- {item['name']} ({item['target_sets']}x)" for item in draft["items"]]
    text = (
        f"Plano *{draft['name']}*:\n"
        + "\n".join(lines)
        + "\n\nManda mais exercícios ou escolha uma opção:"
    )
    return ButtonsReply(text, [("plan_save", "Salvar"), ("plan_cancel", "Cancelar")])


def handle_planning(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    step = user.state_data.get("step")

    if step == "awaiting_plan":
    
        plan_name, items = _parse_plan(text)
    
        if not items:
            return TextReply("Não identifiquei nenhum exercício. Por favor, tente novamente!"), "planning"
    
        draft = {"name": plan_name, "items": items}
        user.state_data = {"step": "building", "draft": draft}
        return _draft_menu(draft), "planning"

    elif step == "building":

        draft = user.state_data.get("draft")
        if button_id == "plan_save":
            plan_repo.save_plan(db, user, draft)
            return enter_idle(user, TextReply(f"Plano *{draft['name']}* salvo!"))
        elif button_id == "plan_cancel":
            return enter_idle(user, idle_menu("Treino cancelado!"))
        if text:
            new_items = _parse_exercises(text)
            if new_items:
                draft = {**draft, "items": draft["items"] + new_items}
                user.state_data = {"step": "building", "draft": draft}
        return _draft_menu(draft), "planning"