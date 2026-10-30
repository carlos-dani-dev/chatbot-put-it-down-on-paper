"""Assinatura HMAC dos links de resultados — usado tanto por quem gera o link
(a ferramenta `mostrar_resultados`) quanto por quem valida (`agent/app.py`).
Garante que `user_id` e `exp` não foram alterados depois de assinados, e que
o link expira sozinho depois de `DEFAULT_TTL_SECONDS`.
"""
import hmac
import hashlib
import time

from .config import settings

DEFAULT_TTL_SECONDS = 15 * 60


class SigningKeyMissing(RuntimeError):
    pass


def _digest(user_id: int, exp: int) -> str:
    key = settings.dashboard_signing_key
    # sem chave, o HMAC seria calculável por qualquer um e qualquer user_id viraria um link válido
    if not key:
        raise SigningKeyMissing("DASHBOARD_SIGNING_KEY não configurada")
    payload = f"{user_id}:{exp}".encode()
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


def sign_result_link(user_id: int, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict:
    exp = int(time.time()) + ttl_seconds
    return {"user_id": user_id, "exp": exp, "sig": _digest(user_id, exp)}


def verify_result_link(user_id: str | None, exp: str | None, sig: str | None) -> bool:
    if not user_id or not exp or not sig:
        return False
    try:
        user_id_int = int(user_id)
        exp_int = int(exp)
    except ValueError:
        return False
    if exp_int < int(time.time()):
        return False
    try:
        expected = _digest(user_id_int, exp_int)
    except SigningKeyMissing:
        return False
    return hmac.compare_digest(expected, sig)
