from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply
from ..schemas.llm_response import ParsedExercises, ParsedPlan

from ._shared import enter_idle, idle_menu, _PRODUCT_FAQ
from ..config import get_llm_client, get_decider_client
from ..repos import plan_repo

llm = get_llm_client()
decider = get_decider_client()

_AWAITING_PLAN_INSTRUCTIONS = (
    "O usuário está descrevendo um plano de treino novo, com nome e exercícios, "
    "ou pode estar tirando uma dúvida sobre como o app funciona."
)

_AWAITING_PLAN_CRITERIA = {
    "create_plan": "Está descrevendo um plano de treino, com exercícios",
    "question": "Está fazendo uma pergunta sobre como o app funciona",
    "unclear": "Não dá pra saber o que ele quer",
}

_BUILDING_INSTRUCTIONS = (
    "O usuário já tem um rascunho de plano de treino e pode estar adicionando "
    "mais exercícios a ele, ou tirando uma dúvida sobre como o app funciona."
)

_BUILDING_CRITERIA = {
    "add_exercises": "Está citando mais exercícios pra incluir no plano",
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


def _draft_menu(draft: dict) -> ButtonsReply:
    lines = [f"- {item['name']} ({item['target_sets']}x)" for item in draft["items"]]
    text = (
        f"📝 Plano *{draft['name']}*:\n"
        + "\n".join(lines)
        + "\n\nManda mais exercícios ou escolha uma opção:"
    )
    return ButtonsReply(text, [("plan_save", "Salvar"), ("plan_cancel", "Cancelar")])


def handle_planning(db: Any | None, user: User, text: str | None = None, button_id: str | None = None) -> tuple[TextReply | ButtonsReply, str]:

    step = user.state_data.get("step")

    if step == "awaiting_plan":

        answer = decider.choose(
            state=text,
            instructions=_AWAITING_PLAN_INSTRUCTIONS,
            criteria=_AWAITING_PLAN_CRITERIA,
        )

        if (answer.confidence or 0) >= 0.6 and answer.choice == "question":
            reply = llm.generate(_PRODUCT_FAQ + text)
            return TextReply(f"{reply}\n\nMe manda o nome e os exercícios do plano quando quiser."), "planning"
        
        plan_name, items = _parse_plan(text)

        if not items:
            return TextReply("🤔 Não identifiquei nenhum exercício. Por favor, tente novamente!"), "planning"

        draft = {"name": plan_name, "items": items}
        user.state_data = {"step": "building", "draft": draft}
        return _draft_menu(draft), "planning"

    elif step == "building":

        draft = user.state_data.get("draft")
        if button_id == "plan_save":
            plan_repo.save_plan(db, user, draft)
            return enter_idle(user, TextReply(f"✅ Plano *{draft['name']}* salvo!\n\nMe avisa quando você quiser *Começar um treino* ou *Planejar outro treino*"))
        elif button_id == "plan_cancel":
            return enter_idle(user, idle_menu("↩️ Treino cancelado!"))

        if text:
            answer = decider.choose(
                state=text,
                instructions=_BUILDING_INSTRUCTIONS,
                criteria=_BUILDING_CRITERIA,
            )

            if (answer.confidence or 0) >= 0.6 and answer.choice == "question":
                reply = llm.generate(_PRODUCT_FAQ + text)
                return TextReply(f"{reply}\n\nManda mais exercícios ou escolha uma opção."), "planning"

            new_items = _parse_exercises(text)
            if new_items:
                draft = {**draft, "items": draft["items"] + new_items}
                user.state_data = {"step": "building", "draft": draft}
        return _draft_menu(draft), "planning"