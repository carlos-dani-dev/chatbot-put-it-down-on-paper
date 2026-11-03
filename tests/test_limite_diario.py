"""Limite diário de mensagens do plano Grátis (billing.daily_limit_intercept).

O app.py chama isto antes de qualquer agente: mensagem barrada não chega na LLM, então não gasta
token. A contagem usa as próprias mensagens gravadas em chat_messages.
"""
from datetime import datetime, timedelta, timezone

import pytest

from agent import billing
from agent.billing import FREE_DAILY_MESSAGES, daily_limit_intercept
from agent.models import ChatMessage
from agent.schemas.replies import TextReply


@pytest.fixture
def mensagens(db):
    """mensagens(user, 10) -> grava 10 mensagens do usuário agora (como o app.py grava)."""
    def _add(user, n, role="user", quando=None):
        for i in range(n):
            db.add(ChatMessage(user_id=user.id, role=role, content=f"msg {i}", state=user.state,
                               **({"created_at": quando} if quando else {})))
        db.flush()
    return _add


def test_abaixo_do_limite_segue_normal(db, user, mensagens):
    mensagens(user, 10)
    assert daily_limit_intercept(db, user) is None


def test_a_ultima_mensagem_do_limite_ainda_passa(db, user, mensagens):
    mensagens(user, FREE_DAILY_MESSAGES)
    assert daily_limit_intercept(db, user) is None


def test_a_primeira_depois_do_limite_recebe_o_aviso(db, make_user, mensagens):
    user = make_user(state="session")
    mensagens(user, FREE_DAILY_MESSAGES + 1)

    reply, estado = daily_limit_intercept(db, user)

    assert isinstance(reply, TextReply)
    assert f"{FREE_DAILY_MESSAGES} mensagens de hoje" in reply.text
    assert "*/pro*" in reply.text  # ensina a saída
    assert estado == "session"  # não tira ninguém do treino


def avisar(db, user):
    """Faz o que o app.py faz com o aviso: grava a resposta como mensagem do bot."""
    reply, _ = daily_limit_intercept(db, user)
    db.add(ChatMessage(user_id=user.id, role="assistant", content=reply.text, state=user.state))
    db.flush()


def test_depois_do_aviso_fica_em_silencio(db, user, mensagens):
    """Quem continua mandando não ganha uma resposta por mensagem — o app não envia nada."""
    mensagens(user, FREE_DAILY_MESSAGES + 1)
    avisar(db, user)
    mensagens(user, 1)

    reply, estado = daily_limit_intercept(db, user)

    assert reply is None and estado == user.state


def test_avisa_mesmo_se_a_mensagem_que_passou_do_limite_nao_passou_por_aqui(db, user, mensagens):
    """A 61ª foi um /pro (tratado antes do limite): a 62ª ainda precisa receber o aviso, e não silêncio."""
    mensagens(user, FREE_DAILY_MESSAGES + 2)

    reply, _ = daily_limit_intercept(db, user)

    assert isinstance(reply, TextReply) and "mensagens de hoje" in reply.text


def test_aviso_de_ontem_nao_silencia_hoje(db, user, mensagens):
    meia_noite = datetime.now(billing.LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    ontem = (meia_noite - timedelta(minutes=1)).astimezone(timezone.utc)
    db.add(ChatMessage(user_id=user.id, role="assistant", content="🙏 Você usou as 60 mensagens", created_at=ontem))
    mensagens(user, FREE_DAILY_MESSAGES + 5)

    reply, _ = daily_limit_intercept(db, user)

    assert isinstance(reply, TextReply)


def test_pro_nao_tem_limite(db, user, make_pro, mensagens):
    make_pro(user)
    mensagens(user, FREE_DAILY_MESSAGES * 3)

    assert daily_limit_intercept(db, user) is None


def test_mensagens_de_ontem_nao_contam(db, user, mensagens):
    meia_noite = datetime.now(billing.LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    ontem = (meia_noite - timedelta(minutes=1)).astimezone(timezone.utc)
    mensagens(user, FREE_DAILY_MESSAGES * 2, quando=ontem)
    mensagens(user, 5)

    assert billing.user_messages_today(db, user) == 5
    assert daily_limit_intercept(db, user) is None


def test_respostas_do_bot_nao_contam(db, user, mensagens):
    mensagens(user, FREE_DAILY_MESSAGES * 2, role="assistant")
    mensagens(user, 1)

    assert billing.user_messages_today(db, user) == 1


def test_cada_pessoa_tem_o_seu_limite(db, make_user, mensagens):
    gastou, nova = make_user(), make_user()
    mensagens(gastou, FREE_DAILY_MESSAGES + 1)
    mensagens(nova, 1)

    assert daily_limit_intercept(db, gastou) is not None
    assert daily_limit_intercept(db, nova) is None
