"""Aceite dos Termos de Uso e da Política de Privacidade (agent/terms.py).

O app.py chama terms_intercept antes de tudo: sem o aceite, nenhuma mensagem chega na LLM nem
gera Pix — o bot só responde com textos fixos e o botão de aceite.
"""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from agent import billing
from agent.models import PixCharge
from agent.schemas.replies import ButtonsReply, MultiTextReply, TextReply
from agent.terms import (
    ACCEPT_BUTTON, ACCEPT_BUTTONS, ACCEPT_PRO_BUTTON, ACCEPT_PRO_BUTTONS, PRIVACY_URL, TERMS_URL,
    TERMS_VERSION, has_accepted, terms_intercept,
)


def aceitou(user, versao=TERMS_VERSION):
    user.terms_version = versao
    return user


@pytest.fixture
def recem_chegado(make_user):
    """Já recebeu as boas-vindas (com o botão) e ainda não tocou nele."""
    novo = make_user(state="onboarding")
    novo.state_data = {"step": "awaiting_user_onboarding_reply"}
    return novo


# --------------------------------------------------------------------------
# Antes do aceite
# --------------------------------------------------------------------------
def test_primeira_mensagem_segue_pras_boas_vindas(db, make_user):
    """As boas-vindas (agents/onboarding.py) já trazem o botão — o intercept não responde por cima."""
    novo = make_user(state="onboarding")
    assert terms_intercept(db, novo, "oi", None) is None


def test_quem_escreve_em_vez_de_tocar_recebe_o_lembrete(db, recem_chegado):
    reply, estado = terms_intercept(db, recem_chegado, "como funciona?", None)  # sem LLM: o autouse barraria

    assert isinstance(reply, ButtonsReply) and reply.buttons == ACCEPT_BUTTONS
    assert reply.text.startswith("Pra eu te ajudar")
    assert TERMS_URL in reply.text and PRIVACY_URL in reply.text
    assert estado == "onboarding" and not has_accepted(recem_chegado)


def test_quem_ja_usava_o_bot_recebe_o_aviso_de_atualizacao_e_continua_onde_estava(db, make_user):
    antigo = make_user(state="session")
    antigo.onboarding_completed = True

    reply, estado = terms_intercept(db, antigo, "supino 80kg 8", None)

    assert "Atualizamos" in reply.text and reply.buttons == ACCEPT_BUTTONS
    assert estado == "session"


def test_versao_antiga_dos_termos_pede_o_aceite_de_novo(db, make_user):
    antigo = aceitou(make_user(), versao="2020-01-01")
    antigo.onboarding_completed = True

    reply, _ = terms_intercept(db, antigo, "oi", None)

    assert "Atualizamos" in reply.text


def test_botoes_dos_agentes_tambem_sao_barrados(db, make_user):
    """Um botão antigo (ex.: do menu) não pode furar o aceite e chegar no agente."""
    antigo = make_user(state="idle")
    antigo.onboarding_completed = True

    reply, _ = terms_intercept(db, antigo, "Treino livre", "free")

    assert reply.buttons == ACCEPT_BUTTONS


# --------------------------------------------------------------------------
# O toque no botão
# --------------------------------------------------------------------------
def test_aceitar_nas_boas_vindas_grava_o_aceite_e_abre_o_menu(db, recem_chegado):
    reply, estado = terms_intercept(db, recem_chegado, "Aceito e começar", ACCEPT_BUTTON)

    assert has_accepted(recem_chegado) and recem_chegado.terms_accepted_at is not None
    assert recem_chegado.onboarding_completed is True  # faz o papel do confirm_start
    assert estado == "idle" and recem_chegado.state_data == {}
    assert reply.text == "[menu gerado pela LLM]"


def test_quem_ja_usava_aceita_e_segue_de_onde_parou(db, make_user):
    antigo = make_user(state="session")
    antigo.onboarding_completed = True

    reply, estado = terms_intercept(db, antigo, "Aceito e começar", ACCEPT_BUTTON)

    assert has_accepted(antigo)
    assert isinstance(reply, TextReply) and "seguir de onde parou" in reply.text
    assert estado == "session"


def test_depois_do_aceite_tudo_segue_normal(db, make_user):
    user = aceitou(make_user())

    assert terms_intercept(db, user, "supino 80kg 8", None) is None
    assert terms_intercept(db, user, "Treino livre", "free") is None
    assert terms_intercept(db, user, "/pro", None) is None  # o commands.py gera o Pix


def test_titulos_dos_botoes_cabem_no_whatsapp():
    assert all(len(title) <= 20 for _, title in ACCEPT_BUTTONS + ACCEPT_PRO_BUTTONS)


# --------------------------------------------------------------------------
# /pro antes do aceite
# --------------------------------------------------------------------------
def test_pro_sem_aceite_pede_o_aceite_antes_do_pix(db, make_user, mercadopago):
    novo = make_user(state="onboarding")  # veio do botão do site: /pro é a 1ª mensagem

    reply, estado = terms_intercept(db, novo, "/pro", None)

    assert reply.buttons == ACCEPT_PRO_BUTTONS
    assert TERMS_URL in reply.text and "sem renovação automática" in reply.text
    assert estado == "onboarding"
    assert mercadopago.created == []  # nada de Pix ainda


def test_aceitar_pelo_pro_ja_gera_o_pix(db, make_user, mercadopago):
    novo = make_user(state="onboarding")

    reply, estado = terms_intercept(db, novo, "Aceito e gerar Pix", ACCEPT_PRO_BUTTON)

    assert has_accepted(novo)
    assert isinstance(reply, MultiTextReply) and "*Plano Pro*" in reply.texts[0]
    assert db.query(PixCharge).filter_by(user_id=novo.id, status="pending").count() == 1
    assert estado == "onboarding" and novo.state_data == {}  # as boas-vindas vêm na próxima mensagem


def test_tocar_de_novo_no_aceite_do_pro_reaproveita_o_pix(db, make_user, mercadopago):
    novo = make_user(state="onboarding")
    terms_intercept(db, novo, "Aceito e gerar Pix", ACCEPT_PRO_BUTTON)

    reply, _ = terms_intercept(db, novo, "Aceito e gerar Pix", ACCEPT_PRO_BUTTON)

    assert "ainda está valendo" in reply.texts[0]
    assert len(mercadopago.created) == 1


def test_quem_ja_e_pro_nao_precisa_aceitar_pra_ouvir_que_ja_e_pro(db, make_user, make_pro):
    pro = make_pro(make_user())
    assert terms_intercept(db, pro, "/pro", None) is None


# --------------------------------------------------------------------------
# Páginas no site
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def site():
    spec = importlib.util.spec_from_file_location("site_app", "site/app.py")
    site_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site_app)
    return site_app


@pytest.mark.parametrize("pagina,titulo", [("/termos", "Termos de Uso"), ("/privacidade", "Política de Privacidade")])
def test_paginas_legais_no_ar_com_a_versao_do_bot(site, pagina, titulo):
    resposta = TestClient(site.app).get(pagina)

    assert resposta.status_code == 200
    assert f"<h1" in resposta.text and titulo in resposta.text
    assert f"Versão {TERMS_VERSION}" in resposta.text  # a mesma que o bot pede pra aceitar


def test_links_do_bot_apontam_pras_paginas_do_site(site):
    paths = {route.path for route in site.app.routes}
    assert {TERMS_URL.removeprefix("https://arnaldogymai.com"), PRIVACY_URL.removeprefix("https://arnaldogymai.com")} <= paths


def test_termos_mostram_os_limites_reais_dos_planos(site):
    assert site.LEGAL_CONTEXT["free"] == {
        "max_plans": billing.FREE_MAX_PLANS,
        "max_exercises": billing.FREE_MAX_EXERCISES_PER_PLAN,
        "daily_messages": billing.FREE_DAILY_MESSAGES,
    }
    assert site.LEGAL_CONTEXT["pro"] == {
        "price": billing.format_brl(billing.PRO_PRICE), "days": billing.PRO_DAYS,
        "daily_messages": billing.PRO_DAILY_MESSAGES,
    }


def test_cards_de_planos_do_site_mostram_o_preco_e_os_limites_reais(site):
    gratis, pro = site.CONTENT["plans"]
    assert pro["price"] == billing.format_brl(billing.PRO_PRICE)
    assert f"Até {billing.FREE_DAILY_MESSAGES} mensagens por dia" in gratis["items"]
    assert f"Até {billing.PRO_DAILY_MESSAGES} mensagens por dia" in pro["items"]
    assert "uso justo" in site.CONTENT["plans_note"]


def test_termos_explicam_o_uso_justo(site):
    texto = TestClient(site.app).get("/termos").text
    assert "limite de uso justo" in texto
    assert f"até {billing.PRO_DAILY_MESSAGES} mensagens por dia" in texto


def test_campos_nao_preenchidos_aparecem_destacados(site, monkeypatch):
    monkeypatch.setitem(site.LEGAL, "contact_email", "")
    assert "[preencher: e-mail de contato]" in TestClient(site.app).get("/termos").text

    monkeypatch.setitem(site.LEGAL, "contact_email", "contato@exemplo.com")
    texto = TestClient(site.app).get("/termos").text
    assert "contato@exemplo.com" in texto and "[preencher: e-mail de contato]" not in texto


def test_rodape_do_site_tem_os_links(site):
    texto = TestClient(site.app).get("/").text
    assert 'href="/termos"' in texto and 'href="/privacidade"' in texto
