"""Assinatura do plano Pro via Pix (Mercado Pago).

Aqui só nasce a cobrança: o status dela (e o Pro do usuário) muda apenas quando o
pagamento for confirmado pelo Mercado Pago, nunca por uma mensagem no chat.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import get_payment_client, settings
from .models import PixCharge, User
from .repos import plan_repo
from .schemas.replies import MultiTextReply, TextReply

logger = logging.getLogger("agent.billing")

PRO_PRICE = Decimal("14.90")
PRO_DAYS = 30
CHARGE_TTL = timedelta(minutes=60)
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


@dataclass
class ChargeRequest:
    status: str  # "created" | "reused" | "already_pro" | "unavailable"
    charge: PixCharge | None = None
    pro_until: datetime | None = None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_brl(value: Decimal) -> str:
    return f"R$ {value:.2f}".replace(".", ",")


def format_local_time(dt: datetime) -> str:
    local = _aware(dt).astimezone(LOCAL_TZ)
    today = datetime.now(LOCAL_TZ).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d/%m às %H:%M")


def pro_until(db: Any, user_id: int) -> datetime | None:
    """Fim do Pro somando todos os pagamentos aprovados: pagar com o Pro ainda ativo
    estende a partir do fim atual, em vez de perder os dias que sobravam."""
    paid = db.scalars(
        select(PixCharge.paid_at)
        .where(PixCharge.user_id == user_id, PixCharge.status == "approved", PixCharge.paid_at.is_not(None))
        .order_by(PixCharge.paid_at)
    ).all()
    until = None
    for paid_at in paid:
        start = max(until, _aware(paid_at)) if until else _aware(paid_at)
        until = start + timedelta(days=PRO_DAYS)
    return until


def active_pro_until(db: Any, user: User) -> datetime | None:
    until = pro_until(db, user.id)
    return until if until is not None and until > datetime.now(timezone.utc) else None


def _open_charge(db: Any, user: User) -> PixCharge | None:
    charge = db.scalar(
        select(PixCharge)
        .where(PixCharge.user_id == user.id, PixCharge.status == "pending")
        .order_by(PixCharge.created_at.desc(), PixCharge.id.desc())
        .limit(1)
    )
    # a margem evita mandar um código que vence enquanto o usuário abre o app do banco
    if charge is not None and _aware(charge.expires_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
        return charge
    return None


def request_pro_charge(db: Any, user: User) -> ChargeRequest:
    pro_until = active_pro_until(db, user)
    if pro_until is not None:
        return ChargeRequest(status="already_pro", pro_until=pro_until)

    open_charge = _open_charge(db, user)
    if open_charge is not None:
        return ChargeRequest(status="reused", charge=open_charge)

    if not settings.mercadopago_access_token:
        logger.error("billing: MERCADOPAGO_ACCESS_TOKEN não configurado")
        return ChargeRequest(status="unavailable")

    idempotency_key = str(uuid.uuid4())
    # grava em UTC: o SQLite descarta o fuso, e datas sem fuso são lidas como UTC no projeto todo
    expires_at = datetime.now(timezone.utc) + CHARGE_TTL
    try:
        payment = get_payment_client().create_pix_payment(
            amount=PRO_PRICE,
            description=f"Arnaldo.gym.ai Pro ({PRO_DAYS} dias)",
            # o Mercado Pago exige e-mail do pagador no Pix; o usuário só tem o número do WhatsApp
            payer_email=f"{user.phone_number}@arnaldo.gym.ai",
            external_reference=f"user:{user.id}",
            expires_at=expires_at.astimezone(LOCAL_TZ),
            idempotency_key=idempotency_key,
            # o Mercado Pago só aceita URL pública com https (ex.: o túnel do ngrok)
            notification_url=(
                f"{settings.public_base_url}/webhook/pix"
                if settings.public_base_url.startswith("https://") else None
            ),
        )
    except Exception:
        logger.exception("billing: falha ao criar Pix no Mercado Pago user=%s", user.phone_number)
        return ChargeRequest(status="unavailable")

    charge = PixCharge(
        user_id=user.id,
        provider="mercadopago",
        provider_payment_id=payment.id,
        idempotency_key=idempotency_key,
        amount=PRO_PRICE,
        status="pending",
        qr_code=payment.qr_code,
        ticket_url=payment.ticket_url,
        expires_at=expires_at,
    )
    db.add(charge)
    db.flush()
    logger.info("billing: Pix criado user=%s payment_id=%s", user.phone_number, payment.id)
    return ChargeRequest(status="created", charge=charge)


# status do Mercado Pago -> status da cobrança; os demais (pending, in_process...) não mudam nada
_FINAL_STATUS = {
    "approved": "approved",
    "rejected": "cancelled",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "charged_back": "refunded",
}


@dataclass
class ProActivated:
    user_id: int
    phone_number: str
    pro_until: datetime


def apply_payment_update(db: Any, payment_id: str) -> ProActivated | None:
    """Processa um aviso de pagamento. O aviso é só um gatilho: o status vem sempre
    de uma consulta à API do Mercado Pago, então um aviso forjado não ativa nada.
    Devolve o Pro recém-ativado (pra avisar o usuário) ou None se não houve ativação.
    """
    charge = db.scalar(
        select(PixCharge).where(PixCharge.provider == "mercadopago", PixCharge.provider_payment_id == payment_id)
    )
    if charge is None:
        logger.warning("billing: aviso de pagamento desconhecido payment_id=%s", payment_id)
        return None

    payment = get_payment_client().get_payment(payment_id)  # se falhar, sobe o erro e o MP reenvia o aviso
    new_status = _FINAL_STATUS.get(payment.status)
    if new_status is None or new_status == charge.status:
        return None  # ainda pendente, ou aviso repetido de algo já processado

    if new_status == "approved":
        if payment.amount != charge.amount or payment.external_reference != f"user:{charge.user_id}":
            logger.error(
                "billing: pagamento não confere com a cobrança payment_id=%s valor=%s ref=%s",
                payment_id, payment.amount, payment.external_reference,
            )
            return None
        charge.status = "approved"
        charge.paid_at = payment.date_approved or datetime.now(timezone.utc)
        db.flush()
        until = pro_until(db, charge.user_id)
        logger.info("billing: Pro ativado user_id=%s payment_id=%s até=%s", charge.user_id, payment_id, until)
        return ProActivated(charge.user_id, charge.user.phone_number, until)

    charge.status = new_status
    logger.info("billing: cobrança %s payment_id=%s", new_status, payment_id)
    return None


# --------------------------------------------------------------------------
# Limites do plano grátis. Quem é Pro sai de `active_pro_until` (calculado a
# partir dos pagamentos aprovados) — não existe flag guardada que possa
# ficar desatualizada num estorno.
# --------------------------------------------------------------------------
FREE_MAX_PLANS = 1
FREE_MAX_EXERCISES_PER_PLAN = 5


@dataclass
class PlanLimits:
    pro_until: datetime | None
    active_plans: int
    max_plans: int | None             # None = sem limite (Pro)
    max_exercises_per_plan: int | None

    @property
    def is_pro(self) -> bool:
        return self.pro_until is not None

    @property
    def plans_full(self) -> bool:
        return self.max_plans is not None and self.active_plans >= self.max_plans

    def describe(self) -> str:
        if self.is_pro:
            return f"Pro (planos e exercícios ilimitados), ativo até {format_local_time(self.pro_until)}."
        return (
            f"Grátis — até {self.max_plans} plano ativo, com até {self.max_exercises_per_plan} "
            f"exercícios por plano (hoje tem {self.active_plans} plano(s) ativo(s)). "
            f"O *Pro* libera planos e exercícios ilimitados por {format_brl(PRO_PRICE)} a cada {PRO_DAYS} dias."
        )


def limits_for(db: Any, user: User) -> PlanLimits:
    active = len(plan_repo.list_active_plan_names(db, user))
    until = active_pro_until(db, user)
    if until is not None:
        return PlanLimits(until, active, None, None)
    return PlanLimits(None, active, FREE_MAX_PLANS, FREE_MAX_EXERCISES_PER_PLAN)


def plans_full_message(plan_names: list[str]) -> str:
    """Resposta fixa quando o usuário grátis tenta criar mais um plano."""
    current = ", ".join(f"*{n}*" for n in plan_names)
    return (
        f"📋 No plano *Grátis* dá pra ter {FREE_MAX_PLANS} plano ativo, e você já tem: {current}.\n\n"
        "Pra criar outro, você pode *Editar ou excluir um plano* ou *Assinar o Pro* "
        "pra ter planos ilimitados. 💪"
    )


# --------------------------------------------------------------------------
# Pedido de assinatura no chat (usado pelos agentes de idle e planning)
# --------------------------------------------------------------------------
def pro_charge_result(db: Any, user: User) -> dict:
    req = request_pro_charge(db, user)
    if req.status == "already_pro":
        return {"ok": False, "already_pro": True, "pro_until": format_local_time(req.pro_until)}
    if req.status == "unavailable":
        return {"ok": False, "error": "Não foi possível gerar o Pix agora."}
    return {
        "ok": True,
        "reused": req.status == "reused",
        "amount": format_brl(req.charge.amount),
        "days": PRO_DAYS,
        "expires": format_local_time(req.charge.expires_at),
        "qr_code": req.charge.qr_code,
    }


def pro_charge_reply(result: dict) -> TextReply | MultiTextReply:
    """Resposta da assinatura montada em código — o copia e cola nunca passa pela LLM."""
    if result.get("already_pro"):
        return TextReply(f"✅ Você já é *Pro*! Seu plano está ativo até {result['pro_until']}.")
    if not result.get("ok"):
        return TextReply("😕 Não consegui gerar o Pix agora. Tenta de novo daqui a pouco?")

    intro = "Seu Pix ainda está valendo! 👇" if result["reused"] else "Bora de *Pro*! 💪"
    return MultiTextReply([
        f"{intro}\n\n"
        f"*Plano Pro*: {result['amount']} por {result['days']} dias.\n"
        "Copie o código da próxima mensagem e cole no app do seu banco, em *Pix Copia e Cola*.\n"
        f"Ele vale até {result['expires']}. Assim que o pagamento for confirmado, seu Pro é ativado.",
        result["qr_code"],
    ])
