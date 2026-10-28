"""Teste manual de ponta a ponta do agente de `idle`.

    poetry run python -m agent.manual_test_idle
"""
from webhook.database import SessionLocal
from webhook.models import ChatMessage, Plan, User
from webhook.repos import plan_repo, user_repo

from agent.agents import idle as idle_agent

TEST_PHONE = "0000000000-agent-idle-test"


def cleanup():
    with SessionLocal.begin() as db:
        user = db.query(User).filter_by(phone_number=TEST_PHONE).first()
        if user:
            db.query(Plan).filter_by(user_id=user.id).delete()
            db.query(ChatMessage).filter_by(user_id=user.id).delete()
            db.delete(user)
            print("--- cleanup ok ---")


def main():
    cleanup()
    try:
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            plan_repo.save_plan(db, user, {"name": "Peito", "items": [{"name": "Supino", "target_sets": 3}]})

        # 1) start_planning muda pra planning
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = idle_agent.handle(db, user, "quero planejar um treino novo")
            print(f"1) start_planning -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "planning"
            user.state = "idle"
            user.state_data = {}

        # 2) start_session muda pra session_setup
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = idle_agent.handle(db, user, "quero começar um treino")
            print(f"2) start_session -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "session_setup"
            user.state = "idle"
            user.state_data = {}

        # 3) list_plans funciona sem mudar de estado
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = idle_agent.handle(db, user, "quais planos eu tenho cadastrados?")
            print(f"3) list_plans -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "idle"
            assert "Peito" in reply.text

        print("=== TODOS OS PASSOS OK ===")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
