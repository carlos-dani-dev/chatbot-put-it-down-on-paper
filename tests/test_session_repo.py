"""session_repo — abrir, achar e abandonar sessões.

Protege a correção dos "treinos sumindo": uma sessão largada sem finalizar ficava aberta, e as
séries do treino seguinte caíam nela. Agora abrir uma sessão abandona qualquer outra aberta.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agent.models import WorkoutSet
from agent.repos import session_repo


def test_abrir_sessao_abandona_a_anterior_aberta(db, user, start_workout, log):
    antiga = start_workout()
    log(antiga, "Rosca direta", (12, 10))

    nova = start_workout()

    assert antiga.ended_reason == "abandoned"
    assert antiga.ended_at is not None
    assert nova.ended_at is None
    assert session_repo.get_open_session(db, user) is nova


def test_sessao_abandonada_termina_na_hora_da_ultima_serie(db, start_workout, log):
    antiga = start_workout()
    log(antiga, "Rosca direta", (12, 10))
    ultima = db.scalar(select(WorkoutSet).where(WorkoutSet.session_id == antiga.id))
    ultima.logged_at = datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc)
    db.flush()

    start_workout()

    assert antiga.ended_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc)


def test_sessao_abandonada_sem_series_termina_quando_comecou(db, start_workout):
    vazia = start_workout()
    start_workout()
    assert vazia.ended_at == vazia.started_at


def test_sessao_finalizada_nao_e_reaberta_nem_abandonada(db, user, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10))
    session_repo.close_session(db, ws)

    start_workout()

    assert ws.ended_reason == "finished"
    assert session_repo.get_open_session(db, user) is not ws


def test_sem_sessao_aberta(db, user):
    assert session_repo.get_open_session(db, user) is None


def test_sessoes_de_outro_usuario_nao_sao_abandonadas(db, user, make_user, start_workout):
    outro = make_user()
    dele = start_workout(owner=outro)

    start_workout()  # o MEU treino

    assert dele.ended_at is None


def test_fechar_conta_as_series_da_sessao(db, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10), (12, 8), (12, 6))

    assert session_repo.close_session(db, ws) == 3
    assert ws.ended_at > datetime.now(timezone.utc) - timedelta(minutes=1)
