"""Log das mensagens trocadas (agent/repos/chat_message_repo.py) — em especial a coluna `state`,
que diz em que estado o usuário estava em cada mensagem (onde as pessoas passam o tempo)."""
from agent.repos import chat_message_repo


def test_mensagem_guarda_o_estado_do_usuario(db, make_user):
    user = make_user(state="planning")

    msg = chat_message_repo.log_message(db, user, "user", "quero um plano de perna")

    assert msg.state == "planning"


def test_pergunta_e_resposta_ficam_com_o_estado_em_que_a_mensagem_chegou(db, make_user):
    """O app.py só troca user.state DEPOIS de gravar a resposta: as duas mensagens da conversa ficam
    no estado que tratou a mensagem, mesmo quando ela leva o usuário pra outro estado."""
    user = make_user(state="session_setup")

    pergunta = chat_message_repo.log_message(db, user, "user", "bora treinar perna")
    resposta = chat_message_repo.log_message(db, user, "assistant", "Treino de perna iniciado!")
    user.state = "session"  # o que o app.py faz no fim da mensagem

    assert (pergunta.state, resposta.state) == ("session_setup", "session_setup")
    assert chat_message_repo.log_message(db, user, "user", "agachamento 60kg 10").state == "session"


def test_mensagem_repetida_do_whatsapp_nao_e_gravada_de_novo(db, make_user):
    """A Meta às vezes entrega a mesma mensagem duas vezes; o wa_message_id evita a duplicata."""
    user = make_user(state="idle")
    primeira = chat_message_repo.log_message(db, user, "user", "oi", wa_message_id="wamid.1")
    user.state = "session"

    repetida = chat_message_repo.log_message(db, user, "user", "oi", wa_message_id="wamid.1")

    assert repetida.id == primeira.id and repetida.state == "idle"
