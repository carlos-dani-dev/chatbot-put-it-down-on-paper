"""Painel local (admin/): as páginas abrem com dados de verdade, a conversa é agrupada em turnos
certinhos e o banco é aberto em modo só leitura.

As páginas são testadas com o TestClient do FastAPI, que "abre" a página sem subir servidor. O
`dependency_overrides` troca o banco do painel pelo banco em memória do teste — o painel nunca
chega a conectar no banco do .env.

O admin/ fica fora do git (.gitignore): na sua máquina estes testes rodam; no GitHub Actions,
onde a pasta não existe, são pulados.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("admin.app", reason="admin/ não existe aqui (fica fora do git)")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from admin import queries
from admin.app import app, get_db, read_only_engine
from agent.models import ChatMessage, LlmCall

AGORA = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def painel(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def turno(db, user, quando, pergunta, resposta, chamadas=()):
    """Grava um turno como o app grava: pergunta e resposta com o mesmo horário (mesma transação)
    e as chamadas à LLM um pouco depois (transação própria, no fim da mensagem)."""
    for role, content in (("user", pergunta), ("assistant", resposta)):
        db.add(ChatMessage(user_id=user.id, role=role, content=content, state=user.state, created_at=quando))
    for i, (label, prompt, output) in enumerate(chamadas, start=1):
        db.add(LlmCall(user_id=user.id, label=label, model="gemini-flash-lite-latest", prompt_tokens=prompt,
                       output_tokens=output, created_at=quando + timedelta(seconds=2, milliseconds=i)))
    db.flush()


@pytest.fixture
def conversa(db, make_user):
    ana = make_user(state="session")
    ana.last_message_at = AGORA
    turno(db, ana, AGORA - timedelta(minutes=10), "bora treinar", "Treino iniciado!", [("session_setup", 1000, 20)])
    turno(db, ana, AGORA - timedelta(minutes=5), "supino 80kg 8", "Boa! Falta uma.",
          [("session", 3000, 40), ("session.closing", 3200, 10)])
    turno(db, ana, AGORA, "/ajuda", "Comandos: ...")  # comando universal: sem LLM
    return ana


# ---------------------------------------------------------------- páginas

def test_visao_geral_soma_tokens_por_estado(painel, conversa):
    pagina = painel.get("/?dias=7")

    assert pagina.status_code == 200
    assert "session.closing" in pagina.text and "session_setup" in pagina.text
    assert "7.270" in pagina.text  # 1000 + 3000 + 3200 de prompt + 20 + 40 + 10 de saída


@pytest.mark.parametrize("dias", [1, 7, 30, 0])
def test_visao_geral_abre_em_todos_os_periodos(painel, conversa, dias):
    assert painel.get(f"/?dias={dias}").status_code == 200


def test_paginas_abrem_com_o_banco_vazio(painel):
    for url in ("/", "/usuarios", "/chamadas"):
        assert painel.get(url).status_code == 200


def test_lista_de_usuarios(painel, conversa):
    pagina = painel.get("/usuarios")

    assert pagina.status_code == 200
    assert conversa.phone_number in pagina.text
    assert f"/usuarios/{conversa.id}" in pagina.text


def test_pagina_do_usuario_mostra_a_conversa_e_as_chamadas(painel, conversa):
    pagina = painel.get(f"/usuarios/{conversa.id}")

    assert pagina.status_code == 200
    for trecho in ("supino 80kg 8", "Boa! Falta uma.", "session.closing", "6.250 tokens"):  # 3040 + 3210
        assert trecho in pagina.text


def test_usuario_inexistente_da_404(painel):
    assert painel.get("/usuarios/999").status_code == 404


def test_chamadas_filtradas_por_estado(painel, conversa):
    pagina = painel.get("/chamadas?label=session")

    linhas = pagina.text.count("<tr class=\"border-t")
    assert linhas == 1  # só a chamada "session", não a "session.closing" nem a "session_setup"


# ---------------------------------------------------------------- turnos

def msg(role, quando, state="session"):
    return SimpleNamespace(role=role, created_at=quando, state=state)


def call(quando, prompt=100, output=10):
    return SimpleNamespace(created_at=quando, prompt_tokens=prompt, output_tokens=output, cost_usd=None)


def test_chamadas_vao_pro_turno_que_as_gerou():
    t1, t2 = AGORA, AGORA + timedelta(minutes=1)
    mensagens = [msg("user", t1), msg("assistant", t1), msg("user", t2), msg("assistant", t2)]
    chamadas = [call(t1 + timedelta(seconds=3)), call(t1 + timedelta(seconds=3)), call(t2 + timedelta(seconds=2))]

    turnos = queries.group_turns(mensagens, chamadas)

    assert [(len(t["messages"]), len(t["calls"])) for t in turnos] == [(2, 2), (2, 1)]
    assert turnos[0]["prompt"] == 200


def test_resposta_sozinha_vira_um_turno_proprio():
    """Ex.: o aviso de "Pagamento confirmado", que o bot manda sem o usuário ter escrito nada."""
    mensagens = [msg("user", AGORA), msg("assistant", AGORA), msg("assistant", AGORA + timedelta(hours=1))]

    turnos = queries.group_turns(mensagens, [])

    assert [[m.role for m in t["messages"]] for t in turnos] == [["user", "assistant"], ["assistant"]]


def test_duas_mensagens_do_usuario_no_mesmo_segundo_ficam_separadas():
    """No SQLite o horário só tem segundos; duas mensagens rápidas teriam o mesmo created_at."""
    mensagens = [msg("user", AGORA), msg("assistant", AGORA), msg("user", AGORA), msg("assistant", AGORA)]

    assert len(queries.group_turns(mensagens, [])) == 2


def test_chamada_antes_da_primeira_mensagem_mostrada_e_ignorada():
    turnos = queries.group_turns([msg("user", AGORA)], [call(AGORA - timedelta(minutes=1))])
    assert turnos[0]["calls"] == []


def test_horario_sem_fuso_do_sqlite_e_tratado_como_utc():
    sem_fuso = AGORA.replace(tzinfo=None)
    turnos = queries.group_turns([msg("user", sem_fuso)], [call(AGORA + timedelta(seconds=1))])
    assert len(turnos[0]["calls"]) == 1


# ---------------------------------------------------------------- só leitura

def test_banco_do_painel_recusa_escrita(tmp_path):
    url = f"sqlite:///{tmp_path / 'painel.db'}"
    from sqlalchemy import create_engine
    with create_engine(url).begin() as conn:  # prepara o arquivo com uma conexão normal
        conn.execute(text("CREATE TABLE t (x INTEGER)"))

    engine = read_only_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM t")).scalar() == 0  # ler pode
        with pytest.raises(OperationalError, match="readonly"):
            conn.execute(text("INSERT INTO t VALUES (1)"))  # escrever não
