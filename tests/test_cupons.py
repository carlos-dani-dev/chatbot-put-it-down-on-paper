"""Cupons de Pro grátis: "/pro CODIGO" (agent/commands.py + agent/billing.py).

O cupom entra na mesma conta dos pagamentos (billing.pro_until): cada resgate soma `days` a partir
do fim do Pro atual, se ainda ativo, ou da data do resgate.
"""
from datetime import datetime, timedelta, timezone

import pytest

from agent import billing
from agent.commands import handle_universal_command, parse_pro_command
from agent.models import Coupon, CouponRedemption, PixCharge
from agent.schemas.replies import ButtonsReply, TextReply
from agent.terms import ACCEPT_COUPON_PREFIX, ACCEPT_COUPON_TITLE, TERMS_VERSION, has_accepted, terms_intercept

AGORA = datetime.now(timezone.utc)


@pytest.fixture
def make_coupon(db):
    def _make(code="PARCEIRO-JOAO", days=90, **kwargs):
        coupon = Coupon(code=code, days=days, **kwargs)
        db.add(coupon)
        db.flush()
        return coupon
    return _make


@pytest.fixture
def aceito(make_user):
    """Usuário que já aceitou os termos (o caso comum depois do onboarding)."""
    def _make(state="idle"):
        user = make_user(state=state)
        user.terms_version = TERMS_VERSION
        return user
    return _make


def resgate(db, user, coupon, quando):
    db.add(CouponRedemption(coupon_id=coupon.id, user_id=user.id, redeemed_at=quando))
    db.flush()


def pagamento(db, user, quando):
    db.add(PixCharge(user_id=user.id, provider="mercadopago", provider_payment_id=f"p-{quando.timestamp()}",
                     idempotency_key=f"k-{quando.timestamp()}", amount=billing.PRO_PRICE, status="approved",
                     qr_code="x", expires_at=quando, paid_at=quando))
    db.flush()


# --------------------------------------------------------------------------
# Leitura do comando
# --------------------------------------------------------------------------
@pytest.mark.parametrize("texto,esperado", [
    ("/pro", (True, None)),
    ("/PRO parceiro-joao", (True, "parceiro-joao")),
    ("  /pro   PARCEIRO-JOAO  ", (True, "PARCEIRO-JOAO")),
    ("/pro quero assinar", (False, None)),
    ("pro PARCEIRO-JOAO", (False, None)),
    (None, (False, None)),
])
def test_leitura_do_comando(texto, esperado):
    assert parse_pro_command(texto) == esperado


# --------------------------------------------------------------------------
# A soma no pro_until
# --------------------------------------------------------------------------
def test_cupom_sozinho_libera_o_pro_pelos_dias_dele(db, user, make_coupon):
    resgate(db, user, make_coupon(days=90), AGORA)
    assert billing.pro_until(db, user.id) == AGORA + timedelta(days=90)


def test_cupom_com_pro_ativo_soma_a_partir_do_fim(db, user, make_coupon):
    pago = AGORA - timedelta(days=10)
    pagamento(db, user, pago)                         # Pro até pago + 30
    resgate(db, user, make_coupon(days=90), AGORA)    # ainda ativo: começa no fim do pago

    assert billing.pro_until(db, user.id) == pago + timedelta(days=30 + 90)


def test_cupom_com_pro_vencido_conta_da_data_do_resgate(db, user, make_coupon):
    pagamento(db, user, AGORA - timedelta(days=60))   # venceu há 30 dias
    resgate(db, user, make_coupon(days=90), AGORA)

    assert billing.pro_until(db, user.id) == AGORA + timedelta(days=90)


def test_pagar_com_o_cupom_ativo_tambem_soma(db, user, make_coupon):
    resgatado = AGORA - timedelta(days=5)
    resgate(db, user, make_coupon(days=90), resgatado)
    pagamento(db, user, AGORA)

    assert billing.pro_until(db, user.id) == resgatado + timedelta(days=90 + 30)


def test_cupom_de_outra_pessoa_nao_conta(db, make_user, make_coupon):
    ana, bia = make_user(), make_user()
    resgate(db, ana, make_coupon(), AGORA)
    assert billing.pro_until(db, bia.id) is None


# --------------------------------------------------------------------------
# /pro CODIGO de quem já aceitou os termos
# --------------------------------------------------------------------------
def test_cupom_valido_ativa_o_pro_sem_pix(db, aceito, make_coupon, mercadopago):
    user = aceito(state="session")
    make_coupon("PARCEIRO-JOAO", days=90)

    reply, estado = handle_universal_command(db, user, "/pro parceiro-joao")  # sem diferenciar maiúsculas

    assert "Cupom aplicado" in reply.text
    assert billing.active_pro_until(db, user) is not None
    assert estado == "session"  # quem estava treinando continua no treino
    assert mercadopago.created == []


def test_mesma_pessoa_nao_usa_o_cupom_duas_vezes(db, aceito, make_coupon):
    user = aceito()
    make_coupon()
    handle_universal_command(db, user, "/pro PARCEIRO-JOAO")
    fim = billing.pro_until(db, user.id)

    reply, _ = handle_universal_command(db, user, "/pro PARCEIRO-JOAO")

    assert "já usou esse cupom" in reply.text
    assert billing.pro_until(db, user.id) == fim


@pytest.mark.parametrize("cupom", [
    {"active": False},
    {"expires_at": AGORA - timedelta(minutes=1)},
])
def test_cupom_desativado_ou_vencido(db, aceito, make_coupon, cupom):
    user = aceito()
    make_coupon(**cupom)

    reply, _ = handle_universal_command(db, user, "/pro PARCEIRO-JOAO")

    assert "não é válido ou já expirou" in reply.text and "*/pro*" in reply.text
    assert billing.active_pro_until(db, user) is None


def test_cupom_inexistente(db, aceito):
    reply, _ = handle_universal_command(db, aceito(), "/pro NAOEXISTE")
    assert "não é válido" in reply.text


def test_cupom_que_ainda_nao_venceu_vale(db, aceito, make_coupon):
    user = aceito()
    make_coupon(expires_at=AGORA + timedelta(days=1))
    assert "Cupom aplicado" in handle_universal_command(db, user, "/pro PARCEIRO-JOAO")[0].text


def test_limite_de_usos(db, aceito, make_coupon):
    make_coupon("LANCAMENTO", days=30, max_redemptions=2)
    primeiro, segundo, terceiro = aceito(), aceito(), aceito()

    assert "Cupom aplicado" in handle_universal_command(db, primeiro, "/pro LANCAMENTO")[0].text
    assert "Cupom aplicado" in handle_universal_command(db, segundo, "/pro LANCAMENTO")[0].text
    reply, _ = handle_universal_command(db, terceiro, "/pro LANCAMENTO")

    assert "limite de usos" in reply.text
    assert billing.active_pro_until(db, terceiro) is None


def test_quem_ja_e_pro_ganha_os_dias_somados(db, aceito, make_pro, make_coupon):
    user = make_pro(aceito())
    fim_antes = billing.pro_until(db, user.id)
    make_coupon(days=90)

    handle_universal_command(db, user, "/pro PARCEIRO-JOAO")

    assert billing.pro_until(db, user.id) == fim_antes + timedelta(days=90)


# --------------------------------------------------------------------------
# /pro CODIGO antes do aceite dos termos
# --------------------------------------------------------------------------
def test_sem_aceite_pede_o_aceite_com_o_cupom_no_botao(db, make_user, make_coupon):
    novo = make_user(state="onboarding")  # mandou o cupom como 1ª mensagem
    make_coupon("PARCEIRO-JOAO", days=90)

    reply, estado = terms_intercept(db, novo, "/pro parceiro-joao", None)

    assert isinstance(reply, ButtonsReply)
    assert reply.buttons == [(f"{ACCEPT_COUPON_PREFIX}PARCEIRO-JOAO", ACCEPT_COUPON_TITLE)]
    assert "90 dias" in reply.text
    assert estado == "onboarding"
    assert billing.active_pro_until(db, novo) is None  # ainda não aplicou


def test_tocar_no_botao_aceita_e_aplica_o_cupom(db, make_user, make_coupon):
    novo = make_user(state="onboarding")
    make_coupon()

    reply, estado = terms_intercept(db, novo, ACCEPT_COUPON_TITLE, f"{ACCEPT_COUPON_PREFIX}PARCEIRO-JOAO")

    assert has_accepted(novo)
    assert "Cupom aplicado" in reply.text
    assert billing.active_pro_until(db, novo) is not None
    assert estado == "onboarding" and novo.state_data == {}  # as boas-vindas vêm na próxima mensagem


def test_cupom_invalido_sem_aceite_avisa_sem_pedir_aceite(db, make_user):
    novo = make_user(state="onboarding")

    reply, _ = terms_intercept(db, novo, "/pro NAOEXISTE", None)

    assert isinstance(reply, TextReply) and "não é válido" in reply.text
    assert not has_accepted(novo)


def test_titulo_do_botao_cabe_no_whatsapp():
    assert len(ACCEPT_COUPON_TITLE) <= 20
