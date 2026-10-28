"""Agente de tool-calling pro estado `session` — substitui o decider+criteria
de `webhook/handlers/session.py` por um agente Gemini com ferramentas restritas
ao contexto real (ver `agent/tools.py` e `agent/context.py`).
"""
import logging

from google import genai
from google.genai import types

from webhook.config import settings
from webhook.handlers._shared import enter_idle, idle_menu
from webhook.repos import session_repo
from webhook.schemas.replies import TextReply

from . import tools as tools_mod
from .context import build_context

logger = logging.getLogger("agent.session")

_MODEL = "gemini-flash-lite-latest"
_MAX_TOOL_ROUNDS = 4
_TEMPERATURE = 0.1


def _system_prompt(ctx) -> str:
    plan_line = f"Plano de hoje: {ctx.plan_name}." if ctx.plan_name else "Treino livre, sem plano definido."

    exercises_line = "; ".join(
        f"[{'ex:' + str(e.id)}] {e.name} ({e.done_sets}/{e.target_sets if e.target_sets is not None else '?'}x)"
        for e in ctx.exercises
    ) or "nenhum ainda"

    sets_line = "; ".join(
        f"[{'set:' + str(s.id)}] {s.exercise_name} série {s.set_number}: {s.weight_kg:g}kg x {s.reps}"
        for s in ctx.sets
    ) or "nenhuma ainda"

    pending_line = (
        f"AÇÃO PENDENTE DE CONFIRMAÇÃO (ainda não executada): {ctx.pending_action}"
        if ctx.pending_action else "Nenhuma ação pendente de confirmação agora."
    )

    return (
        "Você é o assistente de um chatbot de treino no WhatsApp, numa sessão de treino ativa.\n\n"
        f"{plan_line}\n"
        f"Exercícios desta sessão, com os IDs entre colchetes: {exercises_line}\n"
        f"Séries já registradas, com os IDs entre colchetes: {sets_line}\n"
        f"{pending_line}\n\n"
        f"Últimas mensagens trocadas com o usuário, para contexto (pode estar vazio):\n{ctx.history}\n\n"
        "Regras importantes, sem exceção:\n"
        "- Para se referir a um exercício ou série, use SEMPRE um dos IDs exatos listados acima "
        "(campo exercise_id / set_ids) — nunca invente um ID, nunca use o nome do exercício como "
        "se fosse o ID.\n"
        "- Se o usuário descrever um exercício que não está na lista acima, use exercise_id='new' "
        "e preencha new_exercise_name com o nome.\n"
        "- Excluir séries é uma ação destrutiva: chame request_delete_sets ou undo_last_set pra só "
        "PROPOR a exclusão. Só chame confirm_pending_action depois que o usuário confirmar "
        "explicitamente, numa mensagem separada, que quer mesmo excluir — nunca confirme sozinho "
        "na mesma mensagem em que a exclusão foi só proposta.\n"
        "- Sua resposta final em texto só pode afirmar o que o resultado de uma ferramenta "
        "confirmou de verdade — nunca invente números, nomes ou quantidades que não vieram do "
        "resultado de uma chamada de ferramenta.\n"
        "- Seja sucinto e direto. Negrito no estilo WhatsApp é um asterisco só de cada lado, colado "
        "na palavra (*assim*), nunca dois.\n"
        "- Se a mensagem não corresponder a nenhuma ferramenta (ex.: uma dúvida genérica), responda "
        "direto em texto, sem chamar ferramenta nenhuma."
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

    for _round in range(_MAX_TOOL_ROUNDS):
        ctx = build_context(db, user, ws)
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
            final_text = (response.text or "").strip() or "🤔 Não consegui processar isso, pode repetir?"
            return TextReply(final_text), next_state

        contents.append(candidate.content)

        response_parts = []
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

            if isinstance(result, dict) and result.pop("next_state", None) == "idle":
                next_state = "idle"

            response_parts.append(types.Part(function_response=types.FunctionResponse(name=fc.name, response=result)))

        contents.append(types.Content(role="user", parts=response_parts))

        if next_state == "idle":
            closing = client.models.generate_content(
                model=_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=_system_prompt(ctx), temperature=_TEMPERATURE),
            )
            final_text = (closing.text or "").strip() or "🎉 Treino finalizado!"
            return TextReply(final_text), next_state

    return TextReply("🤔 Tive dificuldade pra processar isso, pode tentar de novo?"), next_state
