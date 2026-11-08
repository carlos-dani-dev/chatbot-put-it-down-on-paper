"""Modelos da OpenAI (ex.: GPT Luna) falando o "idioma" do Gemini.

Os agentes montam tudo com os tipos do Gemini (Content, Part, FunctionDeclaration) e leem a resposta
como um GenerateContentResponse. Este módulo traduz o pedido pro Chat Completions da OpenAI e a
resposta de volta, então trocar de modelo é só mudar LLM_MODEL / LLM_FALLBACK_MODEL no .env: os
agentes, o registro de consumo e o painel não mudam. Quem decide qual modelo chamar é o llm_call.py.

Fala direto com a API por HTTP (sem SDK da OpenAI). OPENAI_BASE_URL permite apontar pra outro
endpoint compatível (ex.: OpenRouter, com o modelo "openai/...").
"""
import base64
import json
import logging

import httpx
from google.genai import types

from .config import settings

logger = logging.getLogger("agent.llm_openai")

# Gemini 3 exige a "assinatura de pensamento" nas chamadas de ferramenta que vão no histórico. Uma
# chamada feita pela OpenAI não tem assinatura; se a mesma mensagem cair pro Gemini, este valor
# (documentado pelo Google pra histórico vindo de outro modelo) faz ele aceitar. O SDK manda os bytes
# em base64 urlsafe, então decodificar aqui faz a API receber exatamente o texto documentado.
GEMINI_SKIP_SIGNATURE = base64.urlsafe_b64decode("skip_thought_signature_validator")


class OpenAIError(Exception):
    """Resposta de erro da API da OpenAI. `code` é o status HTTP (o llm_call decide se tenta de novo)."""

    def __init__(self, code: int, message: str):
        super().__init__(f"OpenAI {code}: {message}")
        self.code = code


def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "openai/"))


def configured() -> bool:
    return bool(settings.openai_api_key)


def supports(contents) -> bool:
    """Só texto: áudio (transcrição) e outros arquivos continuam no Gemini."""
    if isinstance(contents, str):
        return True
    for item in contents if isinstance(contents, list) else [contents]:
        if isinstance(item, str):
            continue
        parts = item.parts if isinstance(item, types.Content) else [item]
        if any(getattr(p, "inline_data", None) or getattr(p, "file_data", None) for p in parts or []):
            return False
    return True


# ---------------------------------------------------------------- pedido: Gemini -> OpenAI

def _schema(schema: types.Schema | dict) -> dict:
    """Schema do Gemini (type="OBJECT", nullable=True...) -> JSON Schema da OpenAI."""
    s = schema.model_dump(exclude_none=True) if isinstance(schema, types.Schema) else dict(schema)
    out: dict = {}
    kind = s.get("type")
    if kind:
        kind = str(getattr(kind, "value", kind)).lower()
        out["type"] = [kind, "null"] if s.get("nullable") else kind
    for key in ("description", "enum", "minimum", "maximum", "required"):
        if key in s:
            out[key] = s[key]
    if "properties" in s:
        out["properties"] = {name: _schema(sub) for name, sub in s["properties"].items()}
    if "items" in s:
        out["items"] = _schema(s["items"])
    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


def _tools(config) -> list[dict]:
    tools = []
    for tool in (getattr(config, "tools", None) or []):
        for decl in tool.function_declarations or []:
            params = _schema(decl.parameters) if decl.parameters else {"type": "object", "properties": {}}
            tools.append({"type": "function", "function": {
                "name": decl.name, "description": decl.description or "", "parameters": params,
            }})
    return tools


def _text(parts) -> str:
    return "".join(p.text for p in parts or [] if p.text)


def _messages(contents, config) -> list[dict]:
    messages: list[dict] = []
    system = getattr(config, "system_instruction", None)
    if system:
        messages.append({"role": "system", "content": system if isinstance(system, str) else _text(system.parts)})

    if isinstance(contents, str):
        return messages + [{"role": "user", "content": contents}]

    pending_ids: list[tuple[str, str]] = []  # (id, nome) das chamadas de ferramenta ainda sem resultado
    for n, content in enumerate(contents):
        if isinstance(content, str):
            messages.append({"role": "user", "content": content})
            continue
        parts = content.parts or []
        if content.role == "model":
            calls = [p.function_call for p in parts if p.function_call]
            msg: dict = {"role": "assistant", "content": _text(parts) or None}
            if calls:
                msg["tool_calls"] = []
                for i, fc in enumerate(calls):
                    call_id = fc.id or f"call_{n}_{i}"
                    msg["tool_calls"].append({"id": call_id, "type": "function", "function": {
                        "name": fc.name, "arguments": json.dumps(fc.args or {}, ensure_ascii=False)}})
                    pending_ids.append((call_id, fc.name))
            messages.append(msg)
            continue

        responses = [p.function_response for p in parts if p.function_response]
        if responses:
            # os agentes devolvem os resultados na ordem das chamadas (e sem id): casa por ordem
            for fr in responses:
                call_id = pending_ids.pop(0)[0] if pending_ids else (fr.id or f"call_{n}")
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(fr.response or {}, ensure_ascii=False, default=str)})
            # a OpenAI exige um resultado pra cada chamada; os agentes de um estado só executam a 1ª
            for call_id, name in pending_ids:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(
                    {"ok": False, "error": f"{name} não foi executada: chame de novo, uma ferramenta por vez."},
                    ensure_ascii=False)})
            pending_ids = []
        text = _text(parts)
        if text:
            messages.append({"role": "user", "content": text})
    return messages


def _request(model: str, contents, config) -> dict:
    body: dict = {"model": model, "messages": _messages(contents, config)}
    tools = _tools(config)
    if tools:
        body["tools"] = tools
    # a temperatura do Gemini (0.1) não vai: os GPT mais novos são de raciocínio e recusam temperatura
    # diferente da padrão. O esforço de raciocínio é opcional (ex.: "minimal"), pelo .env
    if settings.openai_reasoning_effort:
        body["reasoning_effort"] = settings.openai_reasoning_effort
    return body


# ---------------------------------------------------------------- resposta: OpenAI -> Gemini

def _response(data: dict) -> types.GenerateContentResponse:
    message = (data.get("choices") or [{}])[0].get("message") or {}
    parts: list[types.Part] = []
    if message.get("content"):
        parts.append(types.Part(text=message["content"]))
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        parts.append(types.Part(
            function_call=types.FunctionCall(id=call.get("id"), name=fn.get("name"), args=args),
            # o Gemini só pede a assinatura na 1ª chamada de uma resposta com várias
            thought_signature=GEMINI_SKIP_SIGNATURE if i == 0 else None,
        ))

    usage = data.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        model_version=data.get("model"),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage.get("prompt_tokens") or 0,
            # completion_tokens já inclui o raciocínio: não soma de novo em "thoughts"
            candidates_token_count=usage.get("completion_tokens") or 0,
            cached_content_token_count=cached,
            total_token_count=usage.get("total_tokens"),
        ),
    )


# ---------------------------------------------------------------- a chamada

def _post(url: str, headers: dict, body: dict, timeout: float) -> httpx.Response:
    """O único ponto que sai pra rede (os testes trocam esta função)."""
    return httpx.post(url, headers=headers, json=body, timeout=timeout)


def generate(model: str, contents, config, timeout: float) -> types.GenerateContentResponse:
    response = _post(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {settings.openai_api_key}"},
        _request(model, contents, config),
        timeout,
    )
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = response.text[:300]
        raise OpenAIError(response.status_code, message)
    return _response(response.json())
