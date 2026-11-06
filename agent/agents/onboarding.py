"""Agente de tool-calling pro estado `onboarding`."""
import logging

from google.genai import types

from agent import llm_call
from agent.shared import ARNALDO_STYLE_RULES, ONBOARDING_FEATURES, _fix_whatsapp_bold, arnaldo_header, enter_idle
from agent.schemas.replies import ButtonsReply, TextReply
from agent.terms import ACCEPT_BUTTONS, TERMS_LINKS, has_accepted

from ..tools import onboarding as tools_mod
from ..context.onboarding import build_context

logger = logging.getLogger("agent.onboarding")

_TEMPERATURE = 0.1

_WELCOME_INTRO = (
    "💪 E aí! Eu sou o *Arnaldo*, seu parceiro pra controlar carga e registrar treinos.\n\n"
    "*Registre treinos*, séries e cargas em linguagem natural\n"
    "*Crie planos de treino* ou treine livre\n"
    "*Acompanhe sua evolução* com recordes e gráficos\n\n"
    "Quer planos e exercícios ilimitados? Pra ser *Pro*, é só me mandar */pro* a qualquer momento.\n\n"
)
_WELCOME_MESSAGE = _WELCOME_INTRO + "Vamos começar?"
# quem ainda não aceitou os termos (quase sempre): o botão é o aceite e já leva pro menu (agent/terms.py)
_WELCOME_WITH_TERMS = (
    _WELCOME_INTRO + "Pra começar, é só aceitar os *Termos de Uso* e a *Política de Privacidade*:\n\n" + TERMS_LINKS
)


def _system_prompt(ctx) -> str:
    return (
        arnaldo_header(
            "nas boas-vindas (onboarding). Você já se apresentou e perguntou se a pessoa quer começar.",
            ONBOARDING_FEATURES,
        )
        + f"Últimas mensagens trocadas com a pessoa, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes:\n"
        "- Se a pessoa confirmar que quer começar, chame confirm_start.\n"
        "- Se a pessoa disser que não quer começar agora, chame decline_start.\n"
        "- Se for uma pergunta sobre como você funciona, responda direto em texto, breve, usando "
        "só as funcionalidades da lista acima (em negrito), e termine perguntando se a pessoa quer "
        "*Começar agora*.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou, nunca invente "
        "fatos sobre o que já aconteceu.\n"
        "- Se não for nada disso, responda em texto pedindo pra pessoa confirmar se quer começar.\n"
        + ARNALDO_STYLE_RULES
    )


def handle(db, user, text, button_id=None):
    ctx = build_context(db, user)

    if ctx.step is None:
        user.state_data = {"step": "awaiting_user_onboarding_reply"}
        if not has_accepted(user):
            return ButtonsReply(_WELCOME_WITH_TERMS, ACCEPT_BUTTONS), "onboarding"
        return TextReply(_WELCOME_MESSAGE), "onboarding"

    if not text:
        return TextReply("Vamos começar? É só me dizer 🙂"), "onboarding"

    client = llm_call.make_client()
    declarations = tools_mod.build_declarations(ctx)
    config = types.GenerateContentConfig(
        system_instruction=_system_prompt(ctx),
        temperature=_TEMPERATURE,
        tools=[types.Tool(function_declarations=declarations)],
    )
    contents = [types.Content(role="user", parts=[types.Part(text=text)])]

    response = llm_call.generate(client, contents=contents, config=config, label="onboarding")
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
