"""Agente de tool-calling pro estado `session_setup`."""
import logging

from google import genai
from google.genai import types

from agent.config import settings
from agent.shared import _fix_whatsapp_bold, enter_idle, enter_planning
from agent.schemas.replies import TextReply

from ..tools import session_setup as tools_mod
from ..context.session_setup import build_context

logger = logging.getLogger("agent.session_setup")

_MODEL = "gemini-flash-lite-latest"
_TEMPERATURE = 0.1
_MAX_TOOL_ROUNDS = 3


def _system_prompt(ctx) -> str:
    plans_line = ", ".join(p.name for p in ctx.plans) or "nenhum"
    return (
        "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está escolhendo "
        "como começar um treino agora.\n\n"
        f"Planos de treino salvos: {plans_line}\n\n"
        f"Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes:\n"
        "- Pra se referir a um plano específico, use SEMPRE o plan_id exato oferecido pela "
        "ferramenta — nunca invente um ID.\n"
        "- Se ele quer treinar livre, chame start_free_session.\n"
        "- Se ele quer seguir um plano salvo específico, chame start_session_with_plan.\n"
        "- Se ele quer voltar/desistir, chame go_back.\n"
        "- Se ele claramente quer mexer nos planos em vez de treinar agora, chame go_to_planning.\n"
        "- Se ele perguntar sobre resultados/progresso/histórico de treinos, não existe ferramenta "
        "pra isso aqui — responda em texto explicando que pra ver os resultados ele precisa "
        "terminar de escolher o treino (ou voltar) e chegar no menu principal.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou.\n"
        "- NUNCA mencione o identificador técnico (como 'plan:1') na resposta final — fale sempre "
        "só pelo nome.\n"
        "- Negrito estilo WhatsApp é um asterisco só de cada lado, colado na palavra (*assim*), "
        "nunca dois.\n"
        "- Se não entender, peça pra escolher um dos planos ou treino livre."
    )


def handle(db, user, text, button_id=None):
    if not text:
        ctx = build_context(db, user)
        plans_line = ", ".join(p.name for p in ctx.plans) or "nenhum plano salvo"
        return TextReply(f"Qual treino vamos fazer? Planos salvos: {plans_line}, ou *treino livre*."), "session_setup"

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
            return TextReply(final_text), "session_setup"

        fc = function_calls[0]
        args = dict(fc.args or {})
        logger.info("agent.session_setup tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

        if fc.name == "go_back":
            return enter_idle(db, user)
        if fc.name == "go_to_planning":
            return enter_planning(db, user)

        impl = tools_mod.TOOL_IMPLS.get(fc.name)
        if impl is None:
            result = {"ok": False, "error": f"Ferramenta desconhecida: {fc.name}"}
        else:
            try:
                result = impl(db, user, ctx, **args)
            except TypeError as exc:
                result = {"ok": False, "error": f"Argumentos inválidos para {fc.name}: {exc}"}

        logger.info("agent.session_setup tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)

        if result.get("ok") and result.get("next_state") == "session":
            plan_name = result.get("plan_name")
            if plan_name:
                msg = (
                    f"💪 Treino *{plan_name}* iniciado! Bora!\n"
                    "Começa me mandando o exercício, o peso que você usou e quantas séries você fez."
                )
            else:
                msg = "💪 Treino livre iniciado! Bora!"
            return TextReply(msg), "session"

        contents.append(candidate.content)
        response_parts = [types.Part(function_response=types.FunctionResponse(name=fc.name, response=result))]
        contents.append(types.Content(role="user", parts=response_parts))

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), "session_setup"
