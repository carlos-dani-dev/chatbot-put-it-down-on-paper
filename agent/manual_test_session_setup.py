"""Teste manual de ponta a ponta do agente de `session_setup`.

    poetry run python -m agent.manual_test_session_setup
"""
from webhook.database import SessionLocal
from webhook.models import ChatMessage, Plan, User, WorkoutSession
from webhook.repos import plan_repo, session_repo, user_repo

from agent.agents import session_setup as session_setup_agent

TEST_PHONE = "0000000000-agent-setup-test"


def cleanup():
    with SessionLocal.begin() as db:
        user = db.query(User).filter_by(phone_number=TEST_PHONE).first()
        if user:
            db.query(WorkoutSession).filter_by(user_id=user.id).delete()
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
            user.state = "session_setup"

        # 1) treino livre
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_setup_agent.handle(db, user, "quero treinar livre")
            print(f"1) start_free_session -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "session"
            assert user.state_data.get("session_id") is not None

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            assert ws is not None and ws.plan_id is None
            session_repo.close_session(db, ws)
            user.state = "session_setup"
            user.state_data = {}

        # 2) com plano
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_setup_agent.handle(db, user, "quero treinar o plano de peito")
            print(f"2) start_session_with_plan -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "session"

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            assert ws is not None and ws.plan_id is not None
            print(f"   sessão aberta com plan_id={ws.plan_id} (deve ser o id do plano Peito)\n")

        print("=== TODOS OS PASSOS OK ===")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
