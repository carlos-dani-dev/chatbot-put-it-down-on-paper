"""Sentry com filtro de dados pessoais (LGPD).

O que vai pro Sentry sai pseudonimizado; os logs do servidor (journalctl) continuam completos.
Dá pra cruzar os dois: o erro no Sentry traz o user_id e o pseudônimo do telefone, e com eles
você acha o resto no banco ou no log do servidor.

- Telefone  -> pseudônimo estável "tel#xxxxxxxx" (HMAC com chave secreta: mesmo número, mesmo
               código; sem a chave, não dá pra reverter testando números).
- Texto que o usuário escreveu -> só o tamanho ("<34 caracteres>").
- Respostas do bot, nomes de exercício, cargas e argumentos de ferramenta ficam como estão:
  não são dado pessoal sensível e são o que se usa pra investigar erro do agente.

Pra descobrir o pseudônimo de um número (ex.: achar os erros de um usuário no Sentry):
    poetry run python -m agent.observability 5586999990000
"""
import hashlib
import hmac
import re
import sys
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from .config import settings

# celular/fixo brasileiro com DDI, no formato que o WhatsApp manda (55 + DDD + 8 ou 9 dígitos)
_PHONE = re.compile(r"(?<!\d)55[1-9][1-9]\d{8,9}(?!\d)")
# log "[agent] Mensagem recebida | de=... | tipo=... | texto=<o que o usuário escreveu>"
_INCOMING_TEXT = re.compile(r"(texto=)(.*)$", re.S)
# repr de objeto com o texto do usuário, ex.: IncomingMessage(..., text='supino 80kg')
_REPR_TEXT = re.compile(r"""(\btext=)(['"])(.*?)(?<!\\)\2""", re.S)
# variáveis locais que guardam o texto do usuário (parâmetro `text` dos handlers, conteúdo de mensagem)
_USER_TEXT_VARS = {"text", "raw_text", "content", "texto"}


def pseudonym(phone: str) -> str:
    key = settings.dashboard_signing_key
    if not key:
        return "tel#oculto"  # sem chave o HMAC seria reversível por força bruta: melhor esconder de vez
    return "tel#" + hmac.new(key.encode(), phone.encode(), hashlib.sha256).hexdigest()[:8]


def _hide_phones(text: str) -> str:
    return _PHONE.sub(lambda m: pseudonym(m[0]), text)


_MASKED = re.compile(r"<\d+ caracteres>")


def _size(text: str) -> str:
    # o breadcrumb passa pelo filtro 2x (ao ser registrado e ao ir junto com o erro): não remedir o que já foi mascarado
    if _MASKED.fullmatch(text):
        return text
    return f"<{len(text)} caracteres>"


def _scrub(value: Any, key: str | None = None, frame_vars: bool = False) -> Any:
    """Percorre o evento inteiro. `frame_vars` liga as regras de variáveis locais do traceback,
    que não valem pros logs (lá, `text=` é a resposta do bot e precisa continuar legível)."""
    if isinstance(value, dict):
        return {k: _scrub(v, k, frame_vars or k == "vars") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, key, frame_vars) for v in value]
    if not isinstance(value, str):
        return value
    if frame_vars:
        if key in _USER_TEXT_VARS:
            # o Sentry guarda variável local como repr ('texto'): as aspas não contam no tamanho
            quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
            return _size(value[1:-1] if quoted else value)
        value = _REPR_TEXT.sub(lambda m: f"{m[1]}{_size(m[3])}", value)
    value = _INCOMING_TEXT.sub(lambda m: f"{m[1]}{_size(m[2])}", value)
    return _hide_phones(value)


def scrub_event(event: dict, hint: dict | None = None) -> dict:
    return _scrub(event)


def init_sentry() -> None:
    if not settings.sentry_dsn:  # sem DSN (ex.: na máquina de desenvolvimento) o Sentry fica desligado
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        integrations=[LoggingIntegration(capture_sentry_logs=True)],
        send_default_pii=False,          # sem IP, cookies e headers de quem chama
        max_request_body_size="never",   # o corpo do webhook do WhatsApp traz telefone, texto e nome do perfil
        traces_sample_rate=0.0,          # só erros; desempenho fica de fora (poupa a cota grátis)
        before_send=scrub_event,         # erros
        before_breadcrumb=scrub_event,   # logs anteriores que vão junto com cada erro
        before_send_log=scrub_event,     # Sentry Logs, se ligado
    )


if __name__ == "__main__":
    for number in sys.argv[1:]:
        print(number, "->", pseudonym(number))
