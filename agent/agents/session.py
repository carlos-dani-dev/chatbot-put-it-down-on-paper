"""Agente de tool-calling pro estado `session` — um agente Gemini com ferramentas
restritas ao contexto real (ver `agent/tools/session.py` e `agent/context/session.py`).
"""
import logging

from google import genai
from google.genai import types

from agent.config import settings
from agent.llm_usage import record_gemini
from agent.shared import (
    ARNALDO_PERSONA, ARNALDO_STYLE_RULES, _fix_whatsapp_bold, arnaldo_features, enter_idle, idle_menu,
)
from agent.repos import session_repo
from agent.schemas.replies import TextReply

from ..tools import session as tools_mod
from ..context.session import build_context

logger = logging.getLogger("agent.session")

_MODEL = "gemini-flash-lite-latest"
_MAX_TOOL_ROUNDS = 4
_TEMPERATURE = 0.1


# A parte FIXA do prompt vem primeiro e é idêntica em toda chamada: o Gemini guarda sozinho o começo
# repetido do prompt em cache e cobra essa parte ~10× mais barato. O que muda a cada mensagem
# (menu, séries, histórico) fica no fim — se viesse antes, o começo nunca se repetiria.
_SESSION_RULES = (
    ARNALDO_PERSONA
    + "Agora você acompanha um treino em andamento, registrando as séries. O estado atual do treino "
    "(exercícios, séries, histórico) vem no fim destas instruções.\n\n"
    "Regras importantes, sem exceção:\n"
    "- Para se referir a um exercício ou série, use SEMPRE um dos IDs exatos listados no estado do "
    "treino (campo exercise_id / set_ids) — nunca invente um ID, nunca use o nome do exercício como "
    "se fosse o ID.\n"
    "- Se o usuário descrever um exercício que não está na lista, use exercise_id='new' "
    "e preencha new_exercise_name com o nome EXATAMENTE como o usuário escreveu (copie as "
    "palavras literais da mensagem dele) — nunca troque por um sinônimo ou variação parecida "
    "(ex.: se ele disse 'barra', nunca escreva 'halteres'; se disse 'halteres', nunca escreva "
    "'barra' ou 'máquina').\n"
    "- Excluir séries é uma ação destrutiva: chame request_delete_sets ou undo_last_set pra só "
    "PROPOR a exclusão. Só chame confirm_pending_action depois que o usuário confirmar "
    "explicitamente, numa mensagem separada, que quer mesmo excluir — nunca confirme sozinho "
    "na mesma mensagem em que a exclusão foi só proposta.\n"
    "- O estado do treino lista só as séries mais recentes. Pra apagar a última série de um "
    "exercício que não aparece ali, use undo_last_set com o exercise_id dele.\n"
    "- Sua resposta final em texto só pode afirmar o que o resultado de uma ferramenta "
    "confirmou de verdade — nunca invente números, nomes ou quantidades que não vieram do "
    "resultado de uma chamada de ferramenta.\n"
    "- Se o usuário perguntar sobre resultados/progresso/histórico de treinos (fora do "
    "desempenho anterior de um exercício específico), não existe ferramenta pra isso aqui — "
    "responda em texto explicando que pra ver os resultados ele precisa finalizar o treino "
    "de hoje e voltar pro menu principal.\n"
    "- NUNCA mencione o identificador técnico (como 'ex:3' ou 'set:5') na resposta final — "
    "fale sempre só pelo nome do exercício.\n"
    "- Se a mensagem não corresponder a nenhuma ferramenta (ex.: uma dúvida genérica), responda "
    "direto em texto, sem chamar ferramenta nenhuma.\n"
    "- Meta de séries: NUNCA compare números por conta própria pra decidir se a meta foi batida. "
    "Os resultados das ferramentas já trazem a situação calculada — use só esses campos:\n"
    "  • target_reached=false → a meta NÃO foi batida: incentive a continuar no mesmo exercício. "
    "Nunca diga que bateu a meta nem sugira trocar de exercício.\n"
    "  • target_reached=true → aí sim comemore a meta e sugira o exercício de "
    "next_pending_exercise (se vier vazio, não invente outro).\n"
    "  • all_targets_reached=true → lembre que ele pode *Finalizar treino*.\n"
    "  • Sem target_reached no resultado (exercício fora do plano ou treino livre) → não fale em meta.\n"
    + ARNALDO_STYLE_RULES
    + "\n=== Estado do treino agora ===\n"
)


def _system_prompt(ctx, facts_confirmed: bool = False) -> str:
    plan_line = f"Plano de hoje: {ctx.plan_name}." if ctx.plan_name else "Treino livre, sem plano definido."

    exercises_line = "; ".join(
        f"[{'ex:' + str(e.id)}] {e.name} ({e.done_sets}/{e.target_sets if e.target_sets is not None else '?'}x)"
        for e in ctx.exercises
    ) or "nenhum ainda"

    recent = ctx.recent_sets()
    sets_line = "; ".join(
        f"[{'set:' + str(s.id)}] {s.exercise_name} série {s.set_number}: {s.weight_kg:g}kg x {s.reps}"
        for s in recent
    ) or "nenhuma ainda"
    sets_label = (f"Últimas {len(recent)} de {len(ctx.sets)} séries registradas"
                  if len(recent) < len(ctx.sets) else "Séries já registradas")

    pending_line = (
        f"AÇÃO PENDENTE DE CONFIRMAÇÃO (ainda não executada): {ctx.pending_action}"
        if ctx.pending_action else "Nenhuma ação pendente de confirmação agora."
    )

    facts_note = (
        "\nAtenção nesta resposta: uma confirmação com o nome do exercício, as séries, o progresso e a "
        "situação da meta (\"falta N séries\" ou \"meta batida\") já foi (ou vai ser) exibida "
        "automaticamente pelo sistema antes da sua resposta — NÃO repita o nome do exercício registrado "
        "nem nenhum número nessa resposta, só reaja numa frase curta coerente com target_reached ou "
        "pergunte o que vem a seguir (pode citar o exercício de next_pending_exercise).\n"
        if facts_confirmed else ""
    )

    # só lista o que as ferramentas deste turno permitem (ver tools.session.build_declarations)
    features = [("Registrar séries", "mandar exercício, carga e repetições, uma ou várias séries de uma vez (ex.: supino 80kg 8 reps)")]
    if ctx.sets:
        features.append(("Corrigir uma série", "apagar uma série registrada errada (eu peço confirmação antes)"))
    features.append(("Ver exercícios do treino", "mostrar os exercícios de hoje e quantas séries faltam"))
    if ctx.exercises:
        features.append(("Ver desempenho anterior", "mostrar a carga e as repetições da última vez num exercício"))
    features.append(("Finalizar treino", "encerrar o treino de hoje e salvar tudo"))

    return (
        _SESSION_RULES
        + arnaldo_features("no meio de um treino, registrando as séries.", features)
        + f"{plan_line}\n"
        f"Exercícios desta sessão, com os IDs entre colchetes: {exercises_line}\n"
        f"{sets_label}, com os IDs entre colchetes: {sets_line}\n"
        f"{pending_line}\n\n"
        f"Últimas mensagens trocadas com o usuário, para contexto (pode estar vazio):\n{ctx.history}\n"
        + facts_note
    )


def handle(db, user, text, button_id=None):
    ws = session_repo.get_open_session(db, user)
    if ws is None:
        return enter_idle(db, user, idle_menu(db, user, "🤔 Não achei um treino em andamento."))

    if not text:
        return TextReply("📝 Me manda as séries, ex.: *supino 80kg 8 reps*"), "session"

    client = genai.Client(api_key=settings.gemini_api_key)

    next_state = "session"
    contents = [types.Content(role="user", parts=[types.Part(text=text)])]
    fact_lines: list[str] = []

    def _compose(llm_text: str, fallback: str) -> str:
        llm_text = llm_text.strip()
        parts = fact_lines + ([llm_text] if llm_text else [])
        return "\n\n".join(parts) or fallback

    for _round in range(_MAX_TOOL_ROUNDS):
        ctx = build_context(db, user, ws)
        declarations = tools_mod.build_declarations(ctx)
        config = types.GenerateContentConfig(
            system_instruction=_system_prompt(ctx, facts_confirmed=bool(fact_lines)),
            temperature=_TEMPERATURE,
            tools=[types.Tool(function_declarations=declarations)],
        )

        response = client.models.generate_content(model=_MODEL, contents=contents, config=config)

        record_gemini("session", response)

        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if (candidate and candidate.content and candidate.content.parts) else []
        function_calls = [p.function_call for p in parts if p.function_call is not None]

        if not function_calls:
            final_text = _fix_whatsapp_bold(
                _compose((response.text or ""), "🤔 Não consegui processar isso, pode repetir?")
            )
            logger.info("agent.session final_text user=%s text=%r", user.phone_number, final_text)
            return TextReply(final_text), next_state

        contents.append(candidate.content)

        response_parts = []
        logged_results = []
        for fc in function_calls:
            impl = tools_mod.TOOL_IMPLS.get(fc.name)
            args = dict(fc.args or {})
            logger.info("agent.session tool_call user=%s name=%s args=%s", user.phone_number, fc.name, args)

            if impl is None:
                result = {"ok": False, "error": f"Ferramenta desconhecida: {fc.name}"}
            else:
                try:
                    result = impl(db, user, ws, ctx, **args)
                except TypeError as exc:
                    result = {"ok": False, "error": f"Argumentos inválidos para {fc.name}: {exc}"}

            logger.info("agent.session tool_result user=%s name=%s result=%s", user.phone_number, fc.name, result)

            if fc.name == "log_sets" and isinstance(result, dict) and result.get("ok"):
                fact_lines.append(tools_mod.format_log_sets_fact(result))
                logged_results.append(result)

            if isinstance(result, dict) and result.pop("next_state", None) == "idle":
                next_state = "idle"

            response_parts.append(types.Part(function_response=types.FunctionResponse(name=fc.name, response=result)))

        contents.append(types.Content(role="user", parts=response_parts))

        # a rodada só registrou séries, e todas deram certo: a confirmação e o comentário saem do
        # código, sem outra chamada à LLM (o caso mais comum do treino, e o mais repetido)
        if logged_results and len(logged_results) == len(function_calls):
            final_text = _fix_whatsapp_bold(_compose(tools_mod.log_sets_reaction(logged_results[-1]), ""))
            logger.info("agent.session final_text(sem 2ª chamada) user=%s text=%r", user.phone_number, final_text)
            return TextReply(final_text), next_state

        if next_state == "idle":
            closing = client.models.generate_content(
                model=_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_system_prompt(ctx, facts_confirmed=bool(fact_lines)),
                    temperature=_TEMPERATURE,
                ),
            )
            record_gemini("session.closing", closing)
            final_text = _fix_whatsapp_bold(_compose((closing.text or ""), "🎉 Treino finalizado!"))
            logger.info("agent.session final_text(closing) user=%s text=%r", user.phone_number, final_text)
            return TextReply(final_text), next_state

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), next_state
