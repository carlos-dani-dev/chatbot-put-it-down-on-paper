"""Agente de tool-calling pro estado `planning`."""
import logging

from google import genai
from google.genai import types

from webhook.config import settings
from webhook.handlers._shared import _fix_whatsapp_bold, enter_idle, enter_session_setup
from webhook.schemas.replies import TextReply

from ..tools import planning as tools_mod
from ..context.planning import build_context

logger = logging.getLogger("agent.planning")

_MODEL = "gemini-flash-lite-latest"
_TEMPERATURE = 0.1
_MAX_TOOL_ROUNDS = 4


def _system_prompt(ctx) -> str:
    plans_line = ", ".join(p.name for p in ctx.plans) or "nenhum"
    if ctx.draft:
        items = ", ".join(f"{i['name']} ({i['target_sets']}x)" for i in ctx.draft["items"])
        draft_line = f"Rascunho em andamento: '{ctx.draft['name']}' — exercícios: {items or 'nenhum ainda'}."
    else:
        draft_line = "Nenhum rascunho em andamento."

    pending_line = (
        f"AÇÃO PENDENTE DE CONFIRMAÇÃO (ainda não executada): {ctx.pending_action}"
        if ctx.pending_action else "Nenhuma ação pendente de confirmação agora."
    )

    return (
        "Você é o assistente de um chatbot de treino no WhatsApp, no fluxo de planejamento "
        "de um plano de treino.\n\n"
        f"Planos salvos: {plans_line}\n"
        f"{draft_line}\n"
        f"{pending_line}\n\n"
        f"Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes, sem exceção:\n"
        "- Pra se referir a um plano salvo específico, use SEMPRE o plan_id exato oferecido "
        "pela ferramenta — nunca invente um ID.\n"
        "- Se o usuário descrever um plano novo ou mais exercícios, chame add_to_draft — é "
        "fundamental sempre ter nome do plano e exercícios com quantidade de séries antes "
        "de poder salvar.\n"
        "- Excluir um plano é uma ação destrutiva: chame request_delete_plan pra só PROPOR. "
        "Só chame confirm_pending_action depois que o usuário confirmar explicitamente, numa "
        "mensagem separada — nunca confirme sozinho na mesma mensagem em que foi só proposto.\n"
        "- Se o usuário claramente quiser treinar agora em vez de planejar, chame go_to_session.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou de verdade.\n"
        "- NUNCA mencione o identificador técnico (como 'plan:1') na resposta final — fale sempre "
        "só pelo nome.\n"
        "- Negrito estilo WhatsApp é um asterisco só de cada lado, colado na palavra (*assim*), "
        "nunca dois.\n"
        "- Se não for nada disso (ex.: dúvida genérica), responda direto em texto."
    )


def handle(db, user, text, button_id=None):
    if not text:
        return TextReply("Me manda o nome e os exercícios do plano, ou o que você quiser fazer. 🙂"), "planning"

    client = genai.Client(api_key=settings.gemini_api_key)
    next_state = "planning"
    contents = [types.Content(role="user", parts=[types.Part(text=text)])]

    for _round in range(_MAX_TOOL_ROUNDS):
        ctx = build_context(db, user)
        declarations = tools_mod.build_declarations(ctx)
        config = types.GenerateContentConfig(
            system_instruction=_system_prompt(ctx),
            temperature=_TEMPERATURE,
            tools=[types.Tool(function_declarations=declarations)],
        )
        response = client.models.generate_content(model=_MODEL, contents=contents, config=config)
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if (candidate and candidate.content and candidate.content.parts) else []
        function_calls = [p.function_call for p in parts if p.function_call is not None]

        if not function_calls:
            final_text = _fix_whatsapp_bold((response.text or "").strip() or "🤔 Não consegui entender, pode repetir?")
            return TextReply(final_text), next_state

        fc = function_calls[0]
        args = dict(fc.args or {})
        logger.info("agent.planning tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

        if fc.name == "go_to_session":
            result = tools_mod.go_to_session(db, user, ctx)
            logger.info("agent.planning tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)
            if result.get("ok"):
                return enter_session_setup(db, user)
            contents.append(candidate.content)
            response_parts = [types.Part(function_response=types.FunctionResponse(name=fc.name, response=result))]
            contents.append(types.Content(role="user", parts=response_parts))
            continue

        contents.append(candidate.content)
        impl = tools_mod.TOOL_IMPLS.get(fc.name)

        if impl is None:
            result = {"ok": False, "error": f"Ferramenta desconhecida: {fc.name}"}
        else:
            try:
                result = impl(db, user, ctx, **args)
            except TypeError as exc:
                result = {"ok": False, "error": f"Argumentos inválidos para {fc.name}: {exc}"}

        logger.info("agent.planning tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)

        if isinstance(result, dict) and result.pop("next_state", None) == "idle":
            next_state = "idle"

        response_parts = [types.Part(function_response=types.FunctionResponse(name=fc.name, response=result))]
        contents.append(types.Content(role="user", parts=response_parts))

        if next_state == "idle":
            closing = client.models.generate_content(
                model=_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=_system_prompt(ctx), temperature=_TEMPERATURE),
            )
            final_text = _fix_whatsapp_bold((closing.text or "").strip() or "✅ Pronto!")
            return enter_idle(db, user, TextReply(final_text))

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), next_state
