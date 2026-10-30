"""Agente de tool-calling pro estado `planning`."""
import logging

from google import genai
from google.genai import types

from agent import billing
from agent.config import settings
from agent.llm_usage import record_gemini
from agent.shared import ARNALDO_STYLE_RULES, _fix_whatsapp_bold, arnaldo_header, enter_idle, enter_session_setup
from agent.schemas.replies import TextReply

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

    if ctx.draft:
        features = [
            ("Adicionar exercícios", "mandar mais exercícios com a quantidade de séries pra entrar no plano"),
            ("Salvar o plano", "guardar o plano de vez pra usar nos treinos"),
            ("Descartar o plano", "jogar fora o rascunho sem salvar"),
        ]
    else:
        features = [
            ("Criar um plano", "mandar o nome do plano e os exercícios com a quantidade de séries (ex.: Peito: supino 3 séries)"),
        ]
    if ctx.plans:
        features += [
            ("Ver meus planos", "listar os planos salvos"),
            ("Ver exercícios de um plano", "mostrar o que tem em um plano salvo"),
            ("Excluir um plano", "apagar um plano salvo (eu peço confirmação antes)"),
        ]
    features.append(("Começar um treino", "parar de planejar e ir treinar agora"))
    if not ctx.limits.is_pro:
        features.append(("Assinar o Pro", "planos e exercícios ilimitados"))

    return (
        arnaldo_header("planejando planos de treino.", features)
        + f"Plano de assinatura do usuário: {ctx.limits.describe()}\n"
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
        "- O nome de cada exercício em add_to_draft deve ser EXATAMENTE o que o usuário "
        "escreveu (copie as palavras literais da mensagem dele) — nunca troque por um sinônimo "
        "ou variação parecida (ex.: se ele disse 'barra', nunca escreva 'halteres'; se disse "
        "'halteres', nunca escreva 'barra' ou 'máquina').\n"
        "- Excluir um plano é uma ação destrutiva: chame request_delete_plan pra só PROPOR. "
        "Só chame confirm_pending_action depois que o usuário confirmar explicitamente, numa "
        "mensagem separada — nunca confirme sozinho na mesma mensagem em que foi só proposto.\n"
        "- Se o usuário claramente quiser treinar agora em vez de planejar, chame go_to_session.\n"
        "- Se ele perguntar sobre resultados/progresso/histórico de treinos, não existe ferramenta "
        "pra isso aqui — responda em texto explicando que pra ver os resultados ele precisa "
        "terminar o que está fazendo agora (salvar ou descartar o plano) e voltar pro menu "
        "principal.\n"
        "- Sua resposta final só pode afirmar o que uma ferramenta confirmou de verdade.\n"
        "- NUNCA mencione o identificador técnico (como 'plan:1') na resposta final — fale sempre "
        "só pelo nome.\n"
        "- Depois de adicionar exercícios, mostre como o plano está e lembre que ele pode "
        "*Adicionar exercícios* ou *Salvar o plano*. Depois de salvar, lembre que ele pode "
        "*Começar um treino* com ele.\n"
        "- Se uma ferramenta recusar com limit_reached (limite do plano Grátis), explique o limite com os números que vieram no erro, deixe claro que nada foi adicionado/criado, e ofereça as saídas: ajustar o pedido pra caber, *Excluir um plano* (só se o limite for de planos e ele já tiver plano salvo) ou *Assinar o Pro*. Se ele quiser assinar, chame assinar_pro.\n"
        "- Se não for nada disso (ex.: dúvida genérica), responda direto em texto.\n"
        + ARNALDO_STYLE_RULES
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
        record_gemini("planning", response)
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if (candidate and candidate.content and candidate.content.parts) else []
        function_calls = [p.function_call for p in parts if p.function_call is not None]

        if not function_calls:
            final_text = _fix_whatsapp_bold((response.text or "").strip() or "🤔 Não consegui entender, pode repetir?")
            logger.info("agent.planning final_text user=%s text=%r", user.phone_number, final_text)
            return TextReply(final_text), next_state

        fc = function_calls[0]
        args = dict(fc.args or {})
        logger.info("agent.planning tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

        if fc.name == "assinar_pro":
            result = tools_mod.assinar_pro(db, user, ctx)
            logger.info("agent.planning tool_result user=%s name=%s result=%s", user.phone_number, fc.name,
                        {k: v for k, v in result.items() if k != "qr_code"})
            return billing.pro_charge_reply(result), next_state

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
            record_gemini("planning.closing", closing)
            final_text = _fix_whatsapp_bold((closing.text or "").strip() or "✅ Pronto!")
            logger.info("agent.planning final_text(closing) user=%s text=%r", user.phone_number, final_text)
            return enter_idle(db, user, TextReply(final_text))

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), next_state
