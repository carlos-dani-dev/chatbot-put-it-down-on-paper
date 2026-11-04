"""idle (menu principal) — ferramentas, o link de resultados e o pedido de assinatura Pro."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from agent import billing
from agent.context.idle import build_context
from agent.models import PixCharge
from agent.signing import verify_result_link
from agent.tools import idle as tools


# ---------------------------------------------------------------- planos

def test_listar_planos(db, user, make_plan):
    assert tools.list_plans(db, user, build_context(db, user))["plans"] == []
    plano = make_plan("Perna")
    assert tools.list_plans(db, user, build_context(db, user))["plans"] == [{"plan_id": f"plan:{plano.id}", "name": "Perna"}]


def test_exercicios_de_um_plano(db, user, make_plan):
    plano = make_plan("Perna", [("Agachamento livre", 3), ("Stiff", 2)])

    r = tools.list_plan_exercises(db, user, build_context(db, user), f"plan:{plano.id}")

    assert r == {"ok": True, "plan_name": "Perna", "exercises": [
        {"name": "Agachamento livre", "target_sets": 3}, {"name": "Stiff", "target_sets": 2},
    ]}


def test_exercicios_de_plano_de_outro_usuario_e_recusado(db, user, make_user, make_plan):
    dele = make_plan("Do outro", owner=make_user())
    r = tools.list_plan_exercises(db, user, build_context(db, user), f"plan:{dele.id}")
    assert r["ok"] is False


# ---------------------------------------------------------------- link de resultados

def test_link_de_resultados_e_assinado_e_valido_so_pro_proprio_usuario(db, user):
    r = tools.mostrar_resultados(db, user, build_context(db, user))

    query = {k: v[0] for k, v in parse_qs(urlparse(r["url"]).query).items()}
    assert urlparse(r["url"]).path == "/webhook/dashboard"
    assert verify_result_link(query["user_id"], query["exp"], query["sig"]) is True
    assert verify_result_link(str(user.id + 1), query["exp"], query["sig"]) is False  # trocar o user_id invalida


def test_sem_chave_de_assinatura_nao_gera_link(db, user, monkeypatch):
    monkeypatch.setattr("agent.signing.settings.dashboard_signing_key", "")

    r = tools.mostrar_resultados(db, user, build_context(db, user))

    assert r["ok"] is False and "url" not in r


# ---------------------------------------------------------------- assinatura Pro (Pix)

def test_assinar_cria_um_pix(db, user, mercadopago):
    r = tools.assinar_pro(db, user, build_context(db, user))

    assert r["ok"] is True and r["reused"] is False
    assert (r["amount"], r["days"], r["qr_code"]) == ("R$ 19,90", 30, "00020126PIXCOPIAECOLA1")
    [pedido] = mercadopago.created
    assert pedido["amount"] == billing.PRO_PRICE
    assert pedido["external_reference"] == f"user:{user.id}"
    assert db.scalar(select(PixCharge.status)) == "pending"


def test_pedir_de_novo_reaproveita_o_pix_ainda_valido(db, user, mercadopago):
    tools.assinar_pro(db, user, build_context(db, user))

    r = tools.assinar_pro(db, user, build_context(db, user))

    assert r["reused"] is True
    assert len(mercadopago.created) == 1  # nenhuma cobrança nova


def test_pix_quase_vencendo_nao_e_reaproveitado(db, user, mercadopago):
    tools.assinar_pro(db, user, build_context(db, user))
    db.scalar(select(PixCharge)).expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
    db.flush()

    r = tools.assinar_pro(db, user, build_context(db, user))

    assert r["reused"] is False and len(mercadopago.created) == 2


def test_quem_ja_e_pro_nao_recebe_cobranca(db, user, make_pro, mercadopago):
    make_pro(user)

    r = tools.assinar_pro(db, user, build_context(db, user))

    assert r["ok"] is False and r["already_pro"] is True
    assert mercadopago.created == []


def test_mercado_pago_fora_do_ar(db, user, mercadopago):
    mercadopago.offline = True

    r = tools.assinar_pro(db, user, build_context(db, user))

    assert r == {"ok": False, "error": "Não foi possível gerar o Pix agora."}
    assert db.scalar(select(PixCharge)) is None


def test_sem_token_configurado_nem_tenta(db, user, mercadopago, monkeypatch):
    monkeypatch.setattr("agent.billing.settings.mercadopago_access_token", "")

    assert tools.assinar_pro(db, user, build_context(db, user))["ok"] is False
    assert mercadopago.created == []


def test_aviso_de_pagamento_so_vai_pra_url_https(db, user, mercadopago, monkeypatch):
    monkeypatch.setattr("agent.billing.settings.public_base_url", "http://localhost:8001")
    tools.assinar_pro(db, user, build_context(db, user))
    assert mercadopago.created[0]["notification_url"] is None  # o Mercado Pago recusa http

    db.scalar(select(PixCharge)).status = "cancelled"
    db.flush()
    monkeypatch.setattr("agent.billing.settings.public_base_url", "https://api.arnaldogymai.com")
    tools.assinar_pro(db, user, build_context(db, user))
    assert mercadopago.created[1]["notification_url"] == "https://api.arnaldogymai.com/webhook/pix"


# ---------------------------------------------------------------- limites e ferramentas oferecidas

def test_limites_do_gratis_e_do_pro(db, user, make_plan, make_pro):
    assert build_context(db, user).limits.plans_full is False
    make_plan("Perna")
    gratis = build_context(db, user).limits
    assert (gratis.is_pro, gratis.plans_full) == (False, True)

    make_pro(user)
    pro = build_context(db, user).limits
    assert (pro.is_pro, pro.plans_full, pro.max_exercises_per_plan) == (True, False, None)


def test_sem_planos_nao_oferece_ferramentas_de_plano_existente(db, user):
    nomes = [d.name for d in tools.build_declarations(build_context(db, user))]
    assert nomes == ["start_planning", "start_session", "list_plans", "mostrar_resultados", "assinar_pro"]


def test_com_planos_oferece_ver_e_gerenciar(db, user, make_plan):
    make_plan("Perna")
    ctx = build_context(db, user)

    decls = {d.name: d for d in tools.build_declarations(ctx)}

    assert {"list_plan_exercises", "manage_plans"} <= decls.keys()
    assert decls["list_plan_exercises"].parameters.properties["plan_id"].enum == ctx.plan_ids()
