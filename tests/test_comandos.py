"""Comandos universais (agent/commands.py) — respondidos em código, antes de qualquer agente/LLM.

Hoje só o /pro: é a mensagem que o botão do Pro no site deixa pronta, então precisa gerar o Pix em
QUALQUER estado — inclusive na primeira mensagem de alguém que nunca falou com o bot.
"""
import pytest

from agent.agents import onboarding as onboarding_agent
from agent.commands import handle_universal_command
from agent.models import PixCharge
from agent.schemas.replies import MultiTextReply, TextReply


@pytest.mark.parametrize("texto", ["/pro", "/PRO", "  /pro  ", "/Pro\n"])
def test_variacoes_de_caixa_e_espaco_sao_aceitas(db, user, mercadopago, texto):
    reply, _ = handle_universal_command(db, user, texto)

    assert isinstance(reply, MultiTextReply)
    assert len(mercadopago.created) == 1


@pytest.mark.parametrize("texto", [
    "quero ser pro",     # sem a barra: segue pro agente, que entende pedidos em texto livre
    "/pro agora",
    "pro",
    "/ pro",
    "/resultados",       # comando removido: vira mensagem comum
    "supino 80kg 8",
])
def test_outras_mensagens_nao_sao_o_comando(db, user, mercadopago, texto):
    assert handle_universal_command(db, user, texto) is None
    assert mercadopago.created == []


def test_mensagem_sem_texto_nao_e_comando(db, user):
    assert handle_universal_command(db, user, None) is None


@pytest.mark.parametrize("estado", ["onboarding", "idle", "planning", "session_setup", "session"])
def test_gera_o_pix_em_qualquer_estado_sem_mudar_o_estado(db, make_user, mercadopago, estado):
    user = make_user(state=estado)

    reply, proximo_estado = handle_universal_command(db, user, "/pro")

    assert proximo_estado == estado  # quem estava no meio do treino continua no treino
    assert "*Plano Pro*" in reply.texts[0]
    assert reply.texts[1] == "00020126PIXCOPIAECOLA1"  # o copia e cola sozinho na 2ª mensagem
    assert db.query(PixCharge).filter_by(user_id=user.id, status="pending").count() == 1


def test_primeira_mensagem_de_quem_veio_do_site(db, make_user, mercadopago):
    """Usuário novo (onboarding sem passo nenhum) mandando o texto pronto do botão do site."""
    novo = make_user(state="onboarding")
    assert novo.state_data == {}

    reply, proximo_estado = handle_universal_command(db, novo, "/pro")

    assert isinstance(reply, MultiTextReply) and proximo_estado == "onboarding"
    assert novo.state_data == {}  # as boas-vindas continuam na próxima mensagem


def test_quem_ja_e_pro_nao_recebe_outro_pix(db, user, make_pro, mercadopago):
    make_pro(user)

    reply, _ = handle_universal_command(db, user, "/pro")

    assert isinstance(reply, TextReply) and "já é *Pro*" in reply.text
    assert mercadopago.created == []


def test_mercado_pago_fora_do_ar(db, user, mercadopago):
    mercadopago.offline = True

    reply, _ = handle_universal_command(db, user, "/pro")

    assert "Não consegui gerar o Pix" in reply.text


def test_boas_vindas_ensinam_o_comando(db, make_user):
    novo = make_user(state="onboarding")

    reply, _ = onboarding_agent.handle(db, novo, "oi")  # 1ª mensagem: boas-vindas fixas, sem LLM

    assert "*/pro*" in reply.text


def test_site_deixa_o_comando_pronto_no_botao_do_pro():
    import importlib.util
    spec = importlib.util.spec_from_file_location("site_app", "site/app.py")
    site_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site_app)

    pro = next(p for p in site_app.CONTENT["plans"] if p["name"] == "Pro")
    assert pro["cta_url"].endswith("?text=/pro")
