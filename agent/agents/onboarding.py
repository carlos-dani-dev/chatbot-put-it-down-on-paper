"""Agente de tool-calling pro estado `onboarding`."""
import logging

from google import genai
from google.genai import types

from agent.config import settings
from agent.shared import _fix_whatsapp_bold, enter_idle
from agent.schemas.replies import TextReply

from ..tools import onboarding as tools_mod
from ..context.onboarding import build_context

logger = logging.getLogger("agent.onboarding")

_MODEL = "gemini-flash-lite-latest"
_TEMPERATURE = 0.1

_WELCOME_MESSAGE = (
    "💪 Eai gym bro, seja bem vindo!\n\n"
    "*Registre treinos*, séries e cargas em linguagem natural\n"
    "*Veja seus progressos* em tempo real\n"
    "*Compartilhe* seus resultados com amigos\n\n"
    "Vamos começar?"
)


def _system_prompt(ctx) -> str:
    return (
        "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está no meio "
        "do processo de boas-vindas (onboarding). O app já foi apresentado e perguntou se "
        "ele quer começar.\n\n"
        f"Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes:\n"
        "- Se ele confirmar que quer começar, chame confirm_start.\n"
        "- Se ele disser que não quer começar agora, chame decline_start.\n"
        "- Se for uma pergunta sobre como o app funciona, responda diretamente em texto, "
        "breve, usando só: registra treinos por mensagem de texto normal; uma IA interpreta "
        "exercício/peso/reps; dá pra criar plano com antecedência ou treinar livre; o "
        "progresso fica salvo automaticamente — e termine perguntando se ele quer começar.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou, nunca invente "
        "fatos sobre o que já aconteceu.\n"
        "- Negrito estilo WhatsApp é um asterisco só de cada lado, colado na palavra "
        "(*assim*), nunca dois.\n"
        "- Se não for nada disso, responda em texto pedindo pra ele confirmar se quer começar."
    )


def handle(db, user, text, button_id=None):
    ctx = build_context(db, user)

    if ctx.step is None:
        user.state_data = {"step": "awaiting_user_onboarding_reply"}
        return TextReply(_WELCOME_MESSAGE), "onboarding"

    if not text:
        return TextReply("Vamos começar? É só me dizer 🙂"), "onboarding"

    client = genai.Client(api_key=settings.gemini_api_key)
    declarations = tools_mod.build_declarations(ctx)
    config = types.GenerateContentConfig(
        system_instruction=_system_prompt(ctx),
        temperature=_TEMPERATURE,
        tools=[types.Tool(function_declarations=declarations)],
    )
    contents = [types.Content(role="user", parts=[types.Part(text=text)])]

    response = client.models.generate_content(model=_MODEL, contents=contents, config=config)
    candidate = response.candidates[0] if response.candidates else None
    parts = candidate.content.parts if (candidate and candidate.content and candidate.content.parts) else []
    function_calls = [p.function_call for p in parts if p.function_call is not None]

    if not function_calls:
        final_text = _fix_whatsapp_bold((response.text or "").strip() or "Vamos começar? É só me dizer 🙂")
        return TextReply(final_text), "onboarding"

    fc = function_calls[0]
    args = dict(fc.args or {})
    logger.info("agent.onboarding tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

    if fc.name == "decline_start":
        result = tools_mod.decline_start(db, user, ctx)
        logger.info("agent.onboarding tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)
        return enter_idle(db, user, TextReply("🙂 Claro, me avise quando quiser continuar!"))

    if fc.name == "confirm_start":
        result = tools_mod.confirm_start(db, user, ctx)
        logger.info("agent.onboarding tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)
        return enter_idle(db, user)

    return TextReply("🤔 Não consegui processar isso, pode repetir?"), "onboarding"
