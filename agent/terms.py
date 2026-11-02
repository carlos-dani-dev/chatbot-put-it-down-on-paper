"""Aceite dos Termos de Uso e da Política de Privacidade — checado antes de tudo no app.py.

Sem o aceite o bot não usa a LLM nem gera Pix: responde só com textos fixos e o botão de aceite.
O aceite é sempre um toque num botão (id fixo), nunca interpretado pela LLM, e fica gravado no
usuário com a data e a versão — se o texto mudar, suba TERMS_VERSION e todo mundo aceita de novo.
"""
from datetime import datetime, timezone
from typing import Any

from . import billing
from .commands import PRO_COMMAND
from .models import User
from .schemas.replies import ButtonsReply, MultiTextReply, TextReply
from .shared import enter_idle

TERMS_VERSION = "2026-11-02"
TERMS_URL = "https://arnaldogymai.com/termos"
PRIVACY_URL = "https://arnaldogymai.com/privacidade"

ACCEPT_BUTTON = "accept_terms"
ACCEPT_PRO_BUTTON = "accept_terms_pro"  # o aceite que já gera o Pix, pra quem chegou mandando /pro

TERMS_LINKS = f"📄 Termos de Uso: {TERMS_URL}\n🔒 Política de Privacidade: {PRIVACY_URL}"

# títulos de botão do WhatsApp: no máximo 20 caracteres
ACCEPT_BUTTONS = [(ACCEPT_BUTTON, "Aceito e começar")]
ACCEPT_PRO_BUTTONS = [(ACCEPT_PRO_BUTTON, "Aceito e gerar Pix")]


def has_accepted(user: User) -> bool:
    return user.terms_version == TERMS_VERSION


def _accept(user: User) -> None:
    user.terms_accepted_at = datetime.now(timezone.utc)
    user.terms_version = TERMS_VERSION


def _reminder(user: User) -> ButtonsReply:
    if user.onboarding_completed:  # já usava o bot antes destes termos (ou de uma versão nova deles)
        intro = "📄 Atualizamos nossos *Termos de Uso* e a *Política de Privacidade*. Pra continuar usando o Arnaldo, é só aceitar:"
    else:
        intro = "Pra eu te ajudar, preciso que você aceite os *Termos de Uso* e a *Política de Privacidade* 🙂"
    return ButtonsReply(f"{intro}\n\n{TERMS_LINKS}", ACCEPT_BUTTONS)


def _pro_terms() -> ButtonsReply:
    return ButtonsReply(
        "Antes de gerar seu Pix, dá uma olhada nos *Termos de Uso* e na *Política de Privacidade*. "
        f"O *Pro* vale por {billing.PRO_DAYS} dias, sem renovação automática.\n\n{TERMS_LINKS}",
        ACCEPT_PRO_BUTTONS,
    )


def _after_accept(db: Any, user: User) -> tuple[TextReply | ButtonsReply, str]:
    if user.state == "onboarding":  # o botão das boas-vindas faz o papel do confirm_start
        user.onboarding_completed = True
        return enter_idle(db, user)
    return TextReply("✅ Valeu! Pode seguir de onde parou."), user.state


def terms_intercept(
    db: Any, user: User, text: str | None, button_id: str | None
) -> tuple[TextReply | ButtonsReply | MultiTextReply, str] | None:
    # os botões de aceite valem sempre — inclusive um toque repetido numa mensagem antiga
    if button_id == ACCEPT_PRO_BUTTON:
        if not has_accepted(user):
            _accept(user)
        return billing.pro_charge_reply(billing.pro_charge_result(db, user)), user.state
    if button_id == ACCEPT_BUTTON:
        if not has_accepted(user):
            _accept(user)
        return _after_accept(db, user)

    if has_accepted(user):
        return None

    if text is not None and text.strip().lower() == PRO_COMMAND:
        if billing.active_pro_until(db, user) is not None:
            return None  # nada a cobrar: o /pro só responde que ele já é Pro
        return _pro_terms(), user.state

    if user.state == "onboarding" and not (user.state_data or {}).get("step"):
        return None  # primeira mensagem: as boas-vindas (agents/onboarding.py) já trazem o botão de aceite

    return _reminder(user), user.state
