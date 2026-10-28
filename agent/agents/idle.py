"""Agente de tool-calling pro estado `idle`."""
import logging

from google import genai
from google.genai import types

from webhook.config import settings
from webhook.handlers._shared import _fix_whatsapp_bold, enter_planning, enter_session_setup, idle_menu
from webhook.schemas.replies import TextReply

from ..tools import idle as tools_mod
from ..context.idle import build_context

logger = logging.getLogger("agent.idle")

_MODEL = "gemini-flash-lite-latest"
_TEMPERATURE = 0.1
_MAX_TOOL_ROUNDS = 3


def _system_prompt(ctx) -> str:
    plans_line = ", ".join(p.name for p in ctx.plans) or "nenhum"
    return (
        "Você é o assistente de um chatbot de treino no WhatsApp, no menu principal.\n\n"
        f"Planos de treino salvos do usuário: {plans_line}\n\n"
        f"Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes:\n"
        "- Pra se referir a um plano específico, use SEMPRE o plan_id exato oferecido pela "
        "ferramenta — nunca invente um ID.\n"
        "- Se ele quer planejar um treino novo, chame start_planning.\n"
        "- Se ele quer começar/registrar um treino agora, chame start_session.\n"
        "- Se ele perguntar quais planos tem, chame list_plans.\n"
        "- Se ele perguntar os exercícios de um plano específico (ou como está indo um plano), "
        "chame list_plan_exercises.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou — nunca invente "
        "nomes de planos ou exercícios que não vieram de um resultado de ferramenta.\n"
        "- NUNCA mencione o identificador técnico (como 'plan:1') na resposta final — fale sempre "
        "só pelo nome.\n"
        "- Negrito estilo WhatsApp é um asterisco só de cada lado, colado na palavra (*assim*), "
        "nunca dois.\n"
        "- Se a mensagem for uma pergunta genérica sobre o app, responda direto em texto."
    )


def handle(db, user, text, button_id=None):
    if not text:
        return idle_menu(db, user), "idle"

    client = genai.Client(api_key=settings.gemini_api_key)
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
            return TextReply(final_text), "idle"

        fc = function_calls[0]
        args = dict(fc.args or {})
        logger.info("agent.idle tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

        if fc.name == "start_planning":
            return enter_planning(db, user)
        if fc.name == "start_session":
            return enter_session_setup(db, user)

        contents.append(candidate.content)
        impl = tools_mod.TOOL_IMPLS.get(fc.name)

        if impl is None:
            result = {"ok": False, "error": f"Ferramenta desconhecida: {fc.name}"}
        else:
            try:
                result = impl(db, user, ctx, **args)
            except TypeError as exc:
                result = {"ok": False, "error": f"Argumentos inválidos para {fc.name}: {exc}"}

        logger.info("agent.idle tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)

        response_parts = [types.Part(function_response=types.FunctionResponse(name=fc.name, response=result))]
        contents.append(types.Content(role="user", parts=response_parts))

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), "idle"
