"""Teste manual de ponta a ponta do agente de `onboarding`.

    poetry run python -m agent.manual_test_onboarding
"""
from agent.database import SessionLocal
from agent.models import ChatMessage, User
from agent.repos import user_repo

from agent.agents import onboarding as onboarding_agent

TEST_PHONE_YES = "0000000000-agent-onboarding-yes"
TEST_PHONE_NO = "0000000000-agent-onboarding-no"


def cleanup(phone: str):
    with SessionLocal.begin() as db:
        user = db.query(User).filter_by(phone_number=phone).first()
        if user:
            db.query(ChatMessage).filter_by(user_id=user.id).delete()
            db.delete(user)


def main():
    cleanup(TEST_PHONE_YES)
    cleanup(TEST_PHONE_NO)
    try:
        # cenário 1: confirma o início
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE_YES)
            reply, next_state = onboarding_agent.handle(db, user, None)
            print(f"1) primeira mensagem (boas-vindas fixa) -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "onboarding"
            assert "Vamos começar" in reply.text

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE_YES)
            reply, next_state = onboarding_agent.handle(db, user, "sim, bora!")
            print(f"2) confirm_start via agente -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "idle", "deveria ter ido pro idle"
            assert user.onboarding_completed is True

        # cenário 2: recusa o início
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE_NO)
            onboarding_agent.handle(db, user, None)

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE_NO)
            reply, next_state = onboarding_agent.handle(db, user, "agora não, depois eu vejo")
            print(f"3) decline_start via agente -> next_state={next_state}\n   {reply.text!r}\n")
            assert next_state == "idle", "deveria ter ido pro idle mesmo recusando"
            assert user.onboarding_completed is True

        print("=== TODOS OS PASSOS OK ===")
    finally:
        cleanup(TEST_PHONE_YES)
        cleanup(TEST_PHONE_NO)
        print("--- cleanup ok ---")


if __name__ == "__main__":
    main()
