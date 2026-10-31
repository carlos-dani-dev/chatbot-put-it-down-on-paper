"""Mensagens da mesma pessoa chegando juntas — a "fila por pessoa" (get_or_create_locked_user).

Cenário real: o usuário manda "supino 80kg 8" e, um segundo depois, "supino 80kg 6". Cada mensagem
roda numa thread e passa segundos esperando o Gemini. Sem trava, as duas leem "maior série = 2" e
gravam duas "série 3". Com a trava, a segunda espera a primeira terminar e grava a "série 4".

Precisa de um Postgres DE VERDADE: o SQLite dos outros testes não tem trava por linha (ele trava o
banco inteiro), então não reproduziria a corrida. Estes testes rodam se TEST_POSTGRES_URL estiver
definida — no GitHub Actions há um Postgres descartável pra isso; na sua máquina, por exemplo:

    docker run -d --rm --name pg-teste -e POSTGRES_PASSWORD=teste -p 127.0.0.1:55432:5432 postgres:17-alpine
    TEST_POSTGRES_URL=postgresql+psycopg2://postgres:teste@127.0.0.1:55432/postgres poetry run pytest tests/test_concorrencia.py
"""
import os
import threading
import time

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agent.context.session import build_context
from agent.models import Base, User, WorkoutSet
from agent.repos import session_repo
from agent.repos.user_repo import get_or_create_locked_user, get_or_create_user
from agent.tools.session import log_sets

PG_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL não definida (precisa de um Postgres de teste)")

ESPERA_DO_GEMINI = 0.5  # o tempo em que a mensagem "pensa" entre ler o estado e gravar
PHONE = "5586999990000"


@pytest.fixture
def Pg():
    """sessionmaker de um Postgres de teste, com as tabelas recriadas do zero."""
    if "supabase" in PG_URL:
        pytest.fail("TEST_POSTGRES_URL aponta pro Supabase — este teste APAGA as tabelas. Use um Postgres descartável.")
    engine = create_engine(PG_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, autoflush=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def preparar_treino_com_duas_series(Pg):
    with Pg.begin() as db:
        user = get_or_create_user(db, PHONE)
        ws = session_repo.start_session(db, user, None)
        ctx = build_context(db, user, ws)
        log_sets(db, user, ws, ctx, "new", [{"weight_kg": 80, "reps": 10}, {"weight_kg": 80, "reps": 9}],
                 new_exercise_name="Supino")


def mensagem(Pg, carregar_usuario, reps, erros):
    """Uma mensagem "supino 80kg <reps>" do começo ao fim, numa transação só — como o _process_turn.

    Imita as DUAS esperas do agente de sessão: o Gemini escolhe a ferramenta, a série é gravada,
    e o Gemini escreve o comentário ("Boa! Falta uma!"). É nessa segunda espera que a série já
    está gravada mas a transação ainda não terminou — e uma mensagem paralela não a enxerga.
    """
    try:
        with Pg.begin() as db:
            user = carregar_usuario(db, PHONE)
            ws = session_repo.get_open_session(db, user)
            time.sleep(ESPERA_DO_GEMINI)  # 1ª chamada: o Gemini decide chamar log_sets
            ctx = build_context(db, user, ws)
            supino = next(f"ex:{e.id}" for e in ctx.exercises if e.name == "Supino")
            log_sets(db, user, ws, ctx, supino, [{"weight_kg": 80, "reps": reps}])
            time.sleep(ESPERA_DO_GEMINI)  # 2ª chamada: o Gemini escreve o comentário; ainda sem commit
    except Exception as exc:  # o teste confere depois: nenhuma mensagem pode quebrar
        erros.append(exc)


def duas_mensagens_quase_juntas(Pg, carregar_usuario):
    erros = []
    a = threading.Thread(target=mensagem, args=(Pg, carregar_usuario, 8, erros))
    b = threading.Thread(target=mensagem, args=(Pg, carregar_usuario, 6, erros))
    a.start()
    time.sleep(0.1)  # a segunda chega 0,1s depois, enquanto a primeira ainda "pensa"
    b.start()
    a.join()
    b.join()
    with Pg() as db:
        numeros = db.scalars(select(WorkoutSet.set_number).order_by(WorkoutSet.set_number)).all()
    return numeros, erros


def test_duas_mensagens_seguidas_numeram_as_series_em_sequencia(Pg):
    preparar_treino_com_duas_series(Pg)

    numeros, erros = duas_mensagens_quase_juntas(Pg, get_or_create_locked_user)

    assert erros == []
    assert numeros == [1, 2, 3, 4]  # sem a trava, saía [1, 2, 3, 3]


def test_sem_a_trava_a_corrida_acontece(Pg):
    """Documenta o problema que a trava resolve: com a leitura comum (get_or_create_user), as duas
    mensagens leem o mesmo estado e gravam o mesmo número. Se um dia este teste falhar, é sinal de
    que o cenário mudou e o teste acima deixou de provar alguma coisa."""
    preparar_treino_com_duas_series(Pg)

    numeros, _ = duas_mensagens_quase_juntas(Pg, get_or_create_user)

    assert numeros == [1, 2, 3, 3]


def test_a_segunda_mensagem_espera_a_primeira_terminar(Pg):
    preparar_treino_com_duas_series(Pg)
    inicio = time.monotonic()

    duas_mensagens_quase_juntas(Pg, get_or_create_locked_user)

    # cada mensagem leva 2 esperas. Em sequência: ~4 esperas no total; em paralelo (sem trava): ~2
    assert time.monotonic() - inicio >= 3.5 * ESPERA_DO_GEMINI


def test_pessoas_diferentes_nao_esperam_uma_pela_outra(Pg):
    def mensagem_de(phone, duracao, terminou):
        with Pg.begin() as db:
            get_or_create_locked_user(db, phone)
            time.sleep(duracao)
        terminou[phone] = time.monotonic()

    terminou = {}
    inicio = time.monotonic()
    ana = threading.Thread(target=mensagem_de, args=("5586111110000", 1.0, terminou))
    beto = threading.Thread(target=mensagem_de, args=("5586222220000", 0.1, terminou))
    ana.start()
    time.sleep(0.05)
    beto.start()
    ana.join()
    beto.join()

    # o Beto não ficou preso atrás da mensagem longa da Ana
    assert terminou["5586222220000"] - inicio < 0.6


def test_primeiras_mensagens_simultaneas_de_alguem_novo_criam_um_usuario_so(Pg):
    erros, largada = [], threading.Barrier(2)

    def primeira_mensagem():
        try:
            with Pg.begin() as db:
                largada.wait()  # as duas tentam criar o usuário no mesmo instante
                get_or_create_locked_user(db, PHONE)
                time.sleep(0.2)
        except Exception as exc:
            erros.append(exc)

    threads = [threading.Thread(target=primeira_mensagem) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert erros == []  # sem a criação protegida, uma quebrava com "telefone duplicado"
    with Pg() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_usuario_novo_nasce_com_os_valores_padrao(Pg):
    with Pg.begin() as db:
        user = get_or_create_locked_user(db, PHONE)
        assert (user.state, user.state_data, user.onboarding_completed) == ("onboarding", {}, False)
        user_id = user.id  # guardado antes do fim da transação: depois disso o objeto não lê mais o banco
    with Pg.begin() as db:
        assert get_or_create_locked_user(db, PHONE).id == user_id  # a segunda vez acha o mesmo
