"""Cliente mínimo da API de pagamentos do Mercado Pago — só o que o Pix do plano Pro usa."""
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import requests

BASE_URL = "https://api.mercadopago.com"


@dataclass
class PixPayment:
    id: str
    status: str
    qr_code: str
    ticket_url: str | None


@dataclass
class PaymentStatus:
    id: str
    status: str  # pending | approved | rejected | cancelled | refunded | charged_back | ...
    amount: Decimal
    external_reference: str | None
    date_approved: datetime | None


def verify_webhook_signature(secret: str, x_signature: str, x_request_id: str, data_id: str) -> bool:
    """Confere o header x-signature ("ts=...,v1=...") que o Mercado Pago manda nos webhooks."""
    parts = dict(p.strip().split("=", 1) for p in (x_signature or "").split(",") if "=" in p)
    ts, received = parts.get("ts"), parts.get("v1")
    if not ts or not received:
        return False
    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


class MercadoPagoClient:

    def __init__(self, access_token: str):
        self._access_token = access_token

    def create_pix_payment(
        self,
        amount: Decimal,
        description: str,
        payer_email: str,
        external_reference: str,
        expires_at: datetime,
        idempotency_key: str,
        notification_url: str | None = None,
    ) -> PixPayment:
        payload = {
            "transaction_amount": float(amount),
            "description": description,
            "payment_method_id": "pix",
            "payer": {"email": payer_email},
            "external_reference": external_reference,
            "date_of_expiration": expires_at.isoformat(timespec="milliseconds"),
        }
        if notification_url:
            payload["notification_url"] = notification_url
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            # mesma chave = mesma cobrança: repetir a requisição nunca gera dois Pix
            "X-Idempotency-Key": idempotency_key,
        }
        response = requests.post(f"{BASE_URL}/v1/payments", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        transaction = data["point_of_interaction"]["transaction_data"]
        return PixPayment(
            id=str(data["id"]),
            status=data["status"],
            qr_code=transaction["qr_code"],
            ticket_url=transaction.get("ticket_url"),
        )

    def get_payment(self, payment_id: str) -> PaymentStatus:
        response = requests.get(
            f"{BASE_URL}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        approved = data.get("date_approved")
        return PaymentStatus(
            id=str(data["id"]),
            status=data["status"],
            amount=Decimal(str(data["transaction_amount"])),
            external_reference=data.get("external_reference"),
            date_approved=datetime.fromisoformat(approved).astimezone(timezone.utc) if approved else None,
        )
