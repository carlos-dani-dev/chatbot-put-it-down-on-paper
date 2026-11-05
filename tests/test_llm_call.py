"""Tentativas, modelo reserva e disjuntor de toda chamada ao Gemini (agent/llm_call.py).

O Gemini é o falso do conftest: `gemini.error(503)` no roteiro faz aquela chamada falhar como a API
falharia. Nenhum teste espera de verdade (o conftest troca o `_sleep`) nem chama o Gemini real.
"""
import httpx
import pytest
from google.genai import errors, types
from sqlalchemy import select

from agent import llm_call, llm_usage
from agent.agents import session as session_agent
from agent.context.session import build_context
from agent.models import LlmCall

P, F = llm_call.PRIMARY_MODEL, llm_call.FALLBACK_MODEL


def chamar(gemini_fake, label="session"):
    client = llm_call.make_client()
    return llm_call.generate(client, contents=["oi"], config=None, label=label)


# ---------------------------------------------------------------- uma chamada

def test_principal_respondendo_e_uma_chamada_so(gemini):
    fake = gemini.script(gemini.text("ok"))
    assert chamar(fake).text == "ok"
    assert fake.models_used() == [P]


def test_erro_passageiro_tenta_o_principal_de_novo(gemini):
    fake = gemini.script(gemini.error(503, "high demand"), gemini.text("ok"))
    assert chamar(fake).text == "ok"
    assert fake.models_used() == [P, P]


@pytest.mark.parametrize("erro", [503, 500, 429])
def test_principal_falhando_duas_vezes_vai_pro_reserva(gemini, erro):
    fake = gemini.script(gemini.error(erro), gemini.error(erro), gemini.text("do reserva"))
    assert chamar(fake).text == "do reserva"
    assert fake.models_used() == [P, P, F]


@pytest.mark.parametrize("excecao", [httpx.ReadTimeout("lento"), httpx.ConnectError("sem rede"), TimeoutError()])
def test_timeout_e_falha_de_conexao_tambem_contam_como_passageiros(gemini, excecao):
    fake = gemini.script(excecao, excecao, gemini.text("do reserva"))
    assert chamar(fake).text == "do reserva"
    assert fake.models_used() == [P, P, F]


@pytest.mark.parametrize("erro", [400, 401, 403])
def test_erro_que_tentar_de_novo_nao_resolve_sobe_na_hora(gemini, erro):
    """400 (pedido inválido) ou chave errada: é bug ou configuração, não instabilidade."""
    fake = gemini.script(gemini.error(erro), gemini.text("nunca chega aqui"))
    with pytest.raises(errors.ClientError):
        chamar(fake)
    assert fake.models_used() == [P]


def test_principal_e_reserva_fora_do_ar(gemini):
    fake = gemini.script(*[gemini.error(503)] * 4)
    with pytest.raises(llm_call.LLMUnavailable):
        chamar(fake)
    assert fake.models_used() == [P, P, F, F]


def test_espera_entre_tentativas_do_mesmo_modelo(gemini, monkeypatch):
    esperas = []
    monkeypatch.setattr("agent.llm_call._sleep", esperas.append)
    gemini.script(*[gemini.error(503)] * 4)

    with pytest.raises(llm_call.LLMUnavailable):
        chamar(None)

    assert esperas == [llm_call.RETRY_DELAY, llm_call.RETRY_DELAY]  # uma antes da 2ª de cada modelo


def test_o_consumo_e_gravado_com_o_modelo_que_respondeu(gemini, llm_calls_db, user):
    resposta = gemini.text("do reserva")
    resposta.model_version = "gemini-3.1-flash-lite"
    resposta.usage_metadata = types.GenerateContentResponseUsageMetadata(prompt_token_count=900, candidates_token_count=12)
    gemini.script(gemini.error(503), gemini.error(503), resposta)

    llm_usage.start_turn(user.phone_number)
    chamar(None, label="idle")
    llm_usage.end_turn()

    [linha] = llm_calls_db.scalars(select(LlmCall)).all()
    assert (linha.label, linha.model, linha.prompt_tokens) == ("idle", "gemini-3.1-flash-lite", 900)  # as que falharam não gastaram token


# ---------------------------------------------------------------- dentro de uma mensagem

def test_depois_de_cair_pro_reserva_a_mensagem_continua_nele(gemini):
    fake = gemini.script(gemini.error(503), gemini.error(503), gemini.text("1ª"), gemini.text("2ª"))

    chamar(fake)
    chamar(fake)  # ex.: a chamada de encerramento da mesma mensagem

    assert fake.models_used() == [P, P, F, F]


def test_mensagem_nova_volta_a_tentar_o_principal(gemini):
    fake = gemini.script(gemini.error(503), gemini.error(503), gemini.text("1ª"), gemini.text("2ª"))

    chamar(fake)
    llm_call.start_message()  # o app.py faz isso a cada mensagem recebida
    chamar(fake)

    assert fake.models_used() == [P, P, F, P]


# ---------------------------------------------------------------- disjuntor

@pytest.fixture
def relogio(monkeypatch):
    agora = {"t": 1000.0}
    monkeypatch.setattr("agent.llm_call._now", lambda: agora["t"])
    return agora


def test_principal_falhando_seguido_fica_um_tempo_fora(gemini, relogio):
    # duas mensagens com o principal fora: 2 + 2 falhas seguidas, passa do limite
    fake = gemini.script(gemini.error(503), gemini.error(503), gemini.text("a"),
                         gemini.error(503), gemini.error(503), gemini.text("b"),
                         gemini.text("c"))
    chamar(fake)
    llm_call.start_message()
    chamar(fake)
    llm_call.start_message()
    chamar(fake)  # disjuntor aberto: vai direto pro reserva, sem tentar o principal

    assert fake.models_used() == [P, P, F, P, P, F, F]


def test_depois_do_intervalo_o_principal_volta(gemini, relogio):
    fake = gemini.script(gemini.text("a"), gemini.text("b"))
    for _ in range(llm_call.BREAKER_THRESHOLD):
        llm_call._primary_failed()

    chamar(fake)                                   # ainda no intervalo: reserva
    relogio["t"] += llm_call.BREAKER_COOLDOWN
    llm_call.start_message()
    chamar(fake)                                   # passou o intervalo: principal de novo

    assert fake.models_used() == [F, P]


def test_sucesso_do_principal_zera_as_falhas(gemini, relogio):
    fake = gemini.script(gemini.error(503), gemini.text("a"),   # 1 falha, depois sucesso
                         gemini.error(503), gemini.text("b"),   # mais 1 falha, depois sucesso
                         gemini.text("c"))
    chamar(fake)
    chamar(fake)
    chamar(fake)

    assert fake.models_used() == [P, P, P, P, P]  # nunca chegou a 3 falhas seguidas


# ---------------------------------------------------------------- agentes de verdade

def test_registro_de_serie_funciona_com_o_principal_fora(db, user, start_workout, make_plan, gemini, exercise_id):
    ws = start_workout(make_plan("Peito", [("Supino", 3)]))
    ex = exercise_id(build_context(db, user, ws), "Supino")
    fake = gemini.script(gemini.error(503), gemini.error(503),
                         gemini.call("log_sets", exercise_id=ex, sets=[{"weight_kg": 80, "reps": 8}]))

    resposta, estado = session_agent.handle(db, user, "supino 80kg 8")

    assert resposta.text.startswith("✅ *Supino*") and estado == "session"
    assert fake.models_used() == [P, P, F]


def test_agente_deixa_a_falha_total_subir_pro_app_responder(db, user, start_workout, gemini):
    start_workout()
    gemini.script(*[gemini.error(503)] * 4)

    with pytest.raises(llm_call.LLMUnavailable):
        session_agent.handle(db, user, "supino 80kg 8")


def test_modelos_vem_da_configuracao():
    assert (P, F) == ("gemini-flash-lite-latest", "gemini-3.1-flash-lite")
