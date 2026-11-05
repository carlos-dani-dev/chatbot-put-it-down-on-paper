"""Toda chamada ao Gemini passa por aqui: tempo máximo, nova tentativa e modelo reserva.

Sequência de uma chamada:
  1. modelo principal; se der erro temporário (5xx, 429, timeout, conexão), tenta de novo;
  2. se falhar de novo, vai pro modelo reserva, com as mesmas tentativas;
  3. se o reserva também falhar, levanta LLMUnavailable (o app.py responde uma mensagem fixa).
Erro que tentar de novo não resolve (400, chave inválida) sobe na hora: seria bug, não instabilidade.

Duas memórias:
- Por mensagem: depois de cair pro reserva, as próximas chamadas da MESMA mensagem já vão direto
  pra ele (não perde tempo com o principal que acabou de falhar). Zerada em `start_message()`.
- Disjuntor: depois de BREAKER_THRESHOLD falhas seguidas do principal, ele fica BREAKER_COOLDOWN
  segundos fora — numa queda do Gemini, cada mensagem não paga as tentativas antes do reserva.
"""
import logging
import threading
import time
from contextvars import ContextVar

import httpx
from google import genai
from google.genai import errors, types

from .config import settings
from .llm_usage import record_gemini

logger = logging.getLogger("agent.llm_call")

PRIMARY_MODEL = settings.llm_model
FALLBACK_MODEL = settings.llm_fallback_model
ATTEMPTS_PER_MODEL = 2
RETRY_DELAY = 1.0           # segundos antes de tentar de novo o mesmo modelo
TIMEOUT_MS = 20_000         # tempo máximo de cada chamada
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = 60.0

# trocáveis nos testes (sem esperar de verdade, com relógio controlado)
_sleep = time.sleep
_now = time.monotonic


class LLMUnavailable(Exception):
    """Principal e reserva falharam: não deu pra responder esta mensagem com a LLM."""


def make_client() -> genai.Client:
    # attempts=1: o SDK não tenta de novo por conta própria — quem decide as tentativas é o generate()
    return genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(
        timeout=TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))


# ---------------------------------------------------------------- memória por mensagem

_message_on_fallback: ContextVar[bool] = ContextVar("llm_message_on_fallback", default=False)


def start_message() -> None:
    """Começo de cada mensagem do usuário (app.py): a mensagem nova volta a tentar o principal."""
    _message_on_fallback.set(False)


# ---------------------------------------------------------------- disjuntor

_lock = threading.Lock()
_primary_failures = 0
_primary_down_until = 0.0


def _primary_available() -> bool:
    with _lock:
        return _now() >= _primary_down_until


def _primary_failed() -> None:
    global _primary_failures, _primary_down_until
    with _lock:
        _primary_failures += 1
        if _primary_failures == BREAKER_THRESHOLD:
            _primary_down_until = _now() + BREAKER_COOLDOWN
            logger.error("llm: %s falhou %d vezes seguidas; usando só %s pelos próximos %.0fs",
                         PRIMARY_MODEL, _primary_failures, FALLBACK_MODEL, BREAKER_COOLDOWN)


def _primary_ok() -> None:
    global _primary_failures, _primary_down_until
    with _lock:
        _primary_failures = 0
        _primary_down_until = 0.0


def reset() -> None:
    """Volta tudo ao estado inicial (usado pelos testes)."""
    _primary_ok()
    start_message()


# ---------------------------------------------------------------- a chamada

def _transient(exc: Exception) -> bool:
    if isinstance(exc, errors.ServerError):
        return True
    if isinstance(exc, errors.ClientError):
        return exc.code == 429  # limite de uso: passa sozinho, e o reserva tem cota própria
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError))


def generate(client, *, contents, config, label: str):
    """generate_content com as tentativas e o reserva. Grava o consumo (llm_calls) com o modelo que respondeu."""
    use_primary = not _message_on_fallback.get() and _primary_available()
    plan = ([PRIMARY_MODEL] if use_primary else []) + [FALLBACK_MODEL]

    last_error = None
    for model in plan:
        for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as exc:
                if not _transient(exc):
                    raise
                last_error = exc
                logger.warning("llm %s: %s falhou (tentativa %d/%d): %s",
                               label, model, attempt, ATTEMPTS_PER_MODEL, exc)
                if model == PRIMARY_MODEL:
                    _primary_failed()
                if attempt < ATTEMPTS_PER_MODEL:
                    _sleep(RETRY_DELAY)
                continue

            if model == PRIMARY_MODEL:
                _primary_ok()
            else:
                if not _message_on_fallback.get():
                    logger.warning("llm %s: respondendo com o modelo reserva %s", label, model)
                _message_on_fallback.set(True)
            record_gemini(label, response)
            return response

    raise LLMUnavailable(f"{PRIMARY_MODEL} e {FALLBACK_MODEL} falharam") from last_error
