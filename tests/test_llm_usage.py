"""Contagem de tokens das chamadas à LLM (agent/llm_usage.py).

Como funciona: `start_turn` abre um acumulador quando chega uma mensagem do usuário; cada chamada
à LLM (`record_gemini` / `record_openrouter`) loga os próprios tokens e soma no acumulador;
`end_turn` loga o total da mensagem e grava uma linha por chamada na tabela llm_calls.

O log é lido com o `caplog`, uma fixture do próprio pytest que captura o que foi logado durante o
teste. A tabela, com a fixture `llm_calls_db` (conftest.py), que faz a gravação cair no banco em
memória do teste — nunca no banco do .env.
"""
import contextvars
import logging
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from google.genai import types
from sqlalchemy import select

from agent import llm_usage
from agent.agents import session as session_agent
from agent.context.session import build_context
from agent.models import LlmCall

PHONE = "5586999990000"


@pytest.fixture(autouse=True)
def _capturar_log(caplog, llm_calls_db):
    caplog.set_level(logging.INFO, logger="agent.llm_usage")
    yield
    llm_usage._turn.set(None)  # nenhum teste deixa um acumulador aberto pro próximo


def gemini_response(prompt=None, output=None, thoughts=None, total=None, model="gemini-flash-lite-latest", cached=None):
    """Uma resposta do Gemini só com a contagem de tokens (é só o que o llm_usage lê)."""
    return types.GenerateContentResponse(
        candidates=[],
        model_version=model,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=output,
            thoughts_token_count=thoughts, total_token_count=total, cached_content_token_count=cached,
        ),
    )


def linhas(caplog, tipo: str) -> list[dict]:
    """Transforma as linhas 'llm_usage call=...' (ou 'TURN') do log em dicts: {'call': 'session', 'prompt': '100', ...}."""
    out = []
    for record in caplog.records:
        msg = record.getMessage()
        if msg.startswith(f"llm_usage {tipo}"):
            out.append(dict(re.findall(r"(\w+)=(\S+)", msg)))
    return out


def total_da_mensagem(caplog) -> dict:
    [turn] = linhas(caplog, "TURN")
    return {k: int(v) for k, v in turn.items() if k != "user"}


def gravadas(db) -> list[tuple]:
    """As linhas da tabela llm_calls, na ordem em que foram gravadas."""
    calls = db.scalars(select(LlmCall).order_by(LlmCall.id)).all()
    return [(c.user_id, c.label, c.model, c.prompt_tokens, c.output_tokens, c.cost_usd) for c in calls]


# ---------------------------------------------------------------- Gemini: o cálculo de cada chamada

def test_chamada_do_gemini_e_logada_com_os_tokens(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=1193, output=11, total=1204))

    [call] = linhas(caplog, "call")
    assert call == {"call": "session", "user": PHONE, "model": "gemini-flash-lite-latest",
                    "prompt": "1193", "output": "11", "total": "1204"}


def test_tokens_de_raciocinio_contam_como_saida(caplog):
    """O Gemini cobra o 'raciocínio' (thoughts) como saída: o output soma os dois."""
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=100, output=20, thoughts=5, total=125))

    [call] = linhas(caplog, "call")
    assert (call["output"], call["total"], call["thoughts"]) == ("25", "125", "5")


@pytest.mark.parametrize(
    ("prompt", "output", "thoughts", "total", "esperado"),
    [
        (100, 20, None, None, 120),   # API não mandou o total: soma as partes
        (100, 20, 5, 0, 125),         # total zerado também é tratado como ausente
        (100, None, None, None, 100), # campo ausente vale 0
        (None, None, None, None, 0),
    ],
)
def test_total_ausente_e_calculado_pelas_partes(caplog, prompt, output, thoughts, total, esperado):
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt, output, thoughts, total))
    assert int(linhas(caplog, "call")[0]["total"]) == esperado


def test_resposta_sem_contagem_nao_quebra_nem_soma(caplog):
    llm_usage.start_turn(PHONE)
    sem_uso = types.GenerateContentResponse(candidates=[])  # usage_metadata ausente

    llm_usage.record_gemini("session", sem_uso)
    llm_usage.end_turn()

    assert "llm_usage call=session tokens=indisponível" in caplog.text
    assert linhas(caplog, "TURN") == []  # nenhuma chamada contável -> sem linha de total


# ---------------------------------------------------------------- OpenRouter (o "decider")

def test_chamada_do_openrouter_com_custo_informado(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_openrouter("decider", "mercury", {"prompt_tokens": 300, "completion_tokens": 40,
                                                       "total_tokens": 340, "cost": 0.0000412})

    [call] = linhas(caplog, "call")
    assert (call["call"], call["model"], call["prompt"], call["output"], call["total"]) == ("decider", "mercury", "300", "40", "340")
    assert call["cost"] == "4.12e-05"


def test_openrouter_sem_total_soma_as_partes_e_sem_custo_omite(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_openrouter("decider", "mercury", {"prompt_tokens": 300, "completion_tokens": 40})

    [call] = linhas(caplog, "call")
    assert call["total"] == "340" and "cost" not in call


def test_openrouter_sem_uso(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_openrouter("decider", "mercury", None)
    assert "llm_usage call=decider model=mercury tokens=indisponível" in caplog.text


# ---------------------------------------------------------------- o total de uma mensagem

def test_total_da_mensagem_soma_todas_as_chamadas(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=1000, output=30, total=1030))
    llm_usage.record_gemini("session.closing", gemini_response(prompt=1200, output=15, thoughts=5, total=1220))
    llm_usage.record_openrouter("decider", "mercury", {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340})

    llm_usage.end_turn()

    assert total_da_mensagem(caplog) == {"calls": 3, "prompt": 2500, "output": 90, "total": 2590}
    assert linhas(caplog, "TURN")[0]["user"] == PHONE


def test_mensagem_sem_chamadas_nao_gera_total(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.end_turn()
    assert linhas(caplog, "TURN") == []


def test_cada_mensagem_comeca_do_zero(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=1000, output=10, total=1010))
    llm_usage.end_turn()

    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=50, output=5, total=55))
    llm_usage.end_turn()

    primeira, segunda = linhas(caplog, "TURN")
    assert (primeira["total"], segunda["total"]) == ("1010", "55")


def test_chamada_fora_de_uma_mensagem_e_logada_mas_nao_somada(caplog):
    """Ex.: uma chamada feita por um script, sem start_turn: loga com user=- e não acumula em nada."""
    llm_usage.record_gemini("script", gemini_response(prompt=10, output=1, total=11))
    llm_usage.end_turn()  # sem start_turn: não faz nada

    assert linhas(caplog, "call")[0]["user"] == "-"
    assert linhas(caplog, "TURN") == []


def test_chamada_depois_de_fechar_a_mensagem_nao_entra_no_total_dela(caplog):
    llm_usage.start_turn(PHONE)
    llm_usage.record_gemini("session", gemini_response(prompt=100, output=10, total=110))
    llm_usage.end_turn()
    llm_usage.record_gemini("atrasada", gemini_response(prompt=999, output=999, total=1998))

    assert total_da_mensagem(caplog)["total"] == 110


def test_mensagens_simultaneas_nao_misturam_os_totais(caplog):
    """Cada mensagem do WhatsApp roda numa thread própria, e cada thread tem a sua cópia do
    ContextVar. Aqui simulamos isso com dois contextos separados, intercalando as chamadas."""
    ana, beto = contextvars.copy_context(), contextvars.copy_context()

    ana.run(llm_usage.start_turn, "5586111110000")
    beto.run(llm_usage.start_turn, "5586222220000")
    ana.run(llm_usage.record_gemini, "session", gemini_response(prompt=100, output=10, total=110))
    beto.run(llm_usage.record_gemini, "idle", gemini_response(prompt=5000, output=50, total=5050))
    ana.run(llm_usage.record_gemini, "session.closing", gemini_response(prompt=200, output=20, total=220))
    ana.run(llm_usage.end_turn)
    beto.run(llm_usage.end_turn)

    totais = {t["user"]: (int(t["calls"]), int(t["total"])) for t in linhas(caplog, "TURN")}
    assert totais == {"5586111110000": (2, 330), "5586222220000": (1, 5050)}


# ---------------------------------------------------------------- de ponta a ponta, com o agente

def test_mensagem_real_da_sessao_soma_as_duas_chamadas_do_agente(caplog, db, user, start_workout, make_plan,
                                                                 gemini):
    """Uma pergunta como 'quais faltam?' passa por 2 chamadas ao Gemini (escolher a ferramenta e
    escrever a resposta). O total da mensagem tem que somar as duas — é o que o usuário custou."""
    start_workout(make_plan("Perna", [("Agachamento livre", 2)]))
    escolher = gemini.call("list_exercises")
    responder = gemini.text("Falta o agachamento!")
    escolher.usage_metadata = gemini_response(prompt=1500, output=40, total=1540).usage_metadata
    responder.usage_metadata = gemini_response(prompt=1700, output=8, total=1708).usage_metadata
    gemini.script(escolher, responder)

    llm_usage.start_turn(user.phone_number)          # o app.py faz isso ao receber a mensagem
    session_agent.handle(db, user, "quais faltam?")
    llm_usage.end_turn()

    assert [c["call"] for c in linhas(caplog, "call")] == ["session", "session"]
    assert total_da_mensagem(caplog) == {"calls": 2, "prompt": 3200, "output": 48, "total": 3248}
    # model None: as respostas do Gemini falso só ganharam a contagem de tokens, não o model_version
    assert gravadas(db) == [(user.id, "session", None, 1500, 40, None), (user.id, "session", None, 1700, 8, None)]


def test_registrar_uma_serie_custa_uma_chamada_so(caplog, db, user, start_workout, make_plan, gemini, exercise_id):
    """O registro de série (a mensagem mais comum do treino) não tem mais a 2ª chamada só pro comentário."""
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2)]))
    escolher = gemini.call("log_sets", exercise_id=exercise_id(build_context(db, user, ws), "Agachamento livre"),
                           sets=[{"weight_kg": 60, "reps": 10}])
    escolher.usage_metadata = gemini_response(prompt=1500, output=40, total=1540).usage_metadata
    gemini.script(escolher)

    llm_usage.start_turn(user.phone_number)
    session_agent.handle(db, user, "agachamento 60kg 10 reps")
    llm_usage.end_turn()

    assert total_da_mensagem(caplog) == {"calls": 1, "prompt": 1500, "output": 40, "total": 1540}


# ---------------------------------------------------------------- a gravação na tabela llm_calls

def test_cada_chamada_vira_uma_linha_ligada_ao_usuario(db, user):
    llm_usage.start_turn(user.phone_number)
    llm_usage.record_gemini("idle", gemini_response(prompt=900, output=12, thoughts=3, total=915))
    llm_usage.record_openrouter("decider", "mercury", {"prompt_tokens": 300, "completion_tokens": 40,
                                                       "cost": 0.0000412})
    llm_usage.end_turn()

    assert gravadas(db) == [
        (user.id, "idle", "gemini-flash-lite-latest", 900, 15, None),  # raciocínio somado na saída
        (user.id, "decider", "mercury", 300, 40, Decimal("0.0000412")),  # só o OpenRouter informa custo
    ]


def test_nada_e_gravado_antes_do_fim_da_mensagem(db, user):
    llm_usage.start_turn(user.phone_number)
    llm_usage.record_gemini("idle", gemini_response(prompt=900, output=12, total=912))

    assert gravadas(db) == []  # grava tudo de uma vez no end_turn, numa transação só


def test_mensagem_sem_chamadas_nao_grava_nada(db, user):
    llm_usage.start_turn(user.phone_number)
    llm_usage.record_gemini("idle", types.GenerateContentResponse(candidates=[]))  # sem contagem
    llm_usage.end_turn()

    assert gravadas(db) == []


def test_telefone_sem_usuario_grava_com_user_id_vazio(db):
    """Se a mensagem que criaria o usuário deu erro e foi desfeita, ele não existe no banco — mas os
    tokens foram gastos do mesmo jeito. A linha fica sem dono em vez de se perder."""
    llm_usage.start_turn("5586000000000")
    llm_usage.record_gemini("onboarding", gemini_response(prompt=500, output=20, total=520))
    llm_usage.end_turn()

    assert gravadas(db) == [(None, "onboarding", "gemini-flash-lite-latest", 500, 20, None)]


def test_chamada_fora_de_uma_mensagem_nao_e_gravada(db):
    llm_usage.record_gemini("script", gemini_response(prompt=10, output=1, total=11))
    llm_usage.end_turn()

    assert gravadas(db) == []


def test_falha_ao_gravar_nao_quebra_a_mensagem(caplog, monkeypatch, user):
    """O consumo é secundário: se o banco falhar aqui, a resposta ao usuário segue normalmente."""
    def banco_fora_do_ar():
        raise ConnectionError("banco fora do ar")

    monkeypatch.setattr("agent.llm_usage.SessionLocal", SimpleNamespace(begin=banco_fora_do_ar))
    llm_usage.start_turn(user.phone_number)
    llm_usage.record_gemini("idle", gemini_response(prompt=900, output=12, total=912))

    llm_usage.end_turn()  # não levanta

    assert "falha ao gravar o consumo" in caplog.text
    assert total_da_mensagem(caplog)["total"] == 912  # o log continua lá
    assert llm_usage._turn.get() is None  # e o acumulador fechou


# ---------------------------------------------------------------- cache do Gemini

def test_tokens_do_cache_sao_logados_e_gravados(caplog, llm_calls_db, user):
    llm_usage.start_turn(user.phone_number)
    llm_usage.record_gemini("session", gemini_response(prompt=2000, output=30, total=2030, cached=1500))
    llm_usage.record_gemini("session", gemini_response(prompt=2000, output=30, total=2030))
    llm_usage.end_turn()

    primeira, segunda = linhas(caplog, "call")
    assert primeira["cached"] == "1500" and "cached" not in segunda  # só aparece no log quando houve cache
    assert [c.cached_tokens for c in llm_calls_db.scalars(select(LlmCall).order_by(LlmCall.id))] == [1500, 0]
