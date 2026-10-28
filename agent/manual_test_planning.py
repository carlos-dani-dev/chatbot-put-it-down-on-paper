"""Teste manual de ponta a ponta do agente de `planning`.

    poetry run python -m agent.manual_test_planning
"""
from agent.database import SessionLocal
from agent.models import ChatMessage, Plan, User
from agent.repos import plan_repo, user_repo

from agent.agents import planning as planning_agent

TEST_PHONE = "0000000000-agent-planning-test"


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
        # 1) criar rascunho e salvar
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            user.state = "planning"
            reply, next_state = planning_agent.handle(db, user, "Peito: supino 3 series, crucifixo 3 series")
            print(f"1a) add_to_draft -> next_state={next_state}\n    {reply.text!r}")
            assert next_state == "planning"
            assert user.state_data.get("plan_draft") is not None
            print(f"    draft: {user.state_data['plan_draft']}\n")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = planning_agent.handle(db, user, "pode salvar")
            print(f"1b) save_draft -> next_state={next_state}\n    {reply.text!r}\n")
            assert next_state == "idle"
            assert user.state_data.get("plan_draft") is None

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            plans = plan_repo.list_active_plans(db, user, limit=8)
            names = [p.name for p in plans]
            print(f"    planos no banco: {names}")
            assert any("peito" in n.lower() for n in names), "plano deveria ter sido salvo de verdade"
            peito_name = next(n for n in names if "peito" in n.lower())
            print()

        # 2) criar outro rascunho, tentar ir pra sessão (deve ser rejeitado), descartar
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            user.state = "planning"
            user.state_data = {}
            reply, next_state = planning_agent.handle(db, user, "Costas: remada 4 series")
            print(f"2a) add_to_draft (novo rascunho) -> next_state={next_state}\n    {reply.text!r}\n")
            assert user.state_data.get("plan_draft") is not None

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = planning_agent.handle(db, user, "quero começar um treino agora")
            print(f"2b) go_to_session com rascunho pendente -> next_state={next_state}\n    {reply.text!r}\n")
            assert next_state == "planning", "NÃO deveria ter saído do planning com rascunho pendente"
            assert user.state_data.get("plan_draft") is not None, "rascunho não deveria ter sumido"

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = planning_agent.handle(db, user, "pode descartar esse plano")
            print(f"2c) discard_draft -> next_state={next_state}\n    {reply.text!r}\n")
            assert next_state == "idle"
            assert user.state_data.get("plan_draft") is None

        # 3) excluir o plano "Peito" existente, com confirmação separada
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            user.state = "planning"
            user.state_data = {}
            reply, next_state = planning_agent.handle(db, user, "quero excluir o plano de peito")
            print(f"3a) request_delete_plan -> next_state={next_state}\n    {reply.text!r}")
            print(f"    pending_action: {user.state_data.get('pending_action')}\n")
            assert next_state == "planning"
            assert user.state_data.get("pending_action") is not None

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            plans = plan_repo.list_active_plans(db, user, limit=8)
            names = [p.name for p in plans]
            print(f"    planos no banco ANTES da confirmação: {names} (deve ainda ter {peito_name!r})\n")
            assert peito_name in names

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = planning_agent.handle(db, user, "sim, pode confirmar a exclusão")
            print(f"3b) confirm_pending_action -> next_state={next_state}\n    {reply.text!r}\n")
            assert next_state == "idle"

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            plans = plan_repo.list_active_plans(db, user, limit=8)
            names = [p.name for p in plans]
            print(f"    planos no banco DEPOIS da confirmação: {names} ({peito_name!r} deve ter sumido)")
            assert peito_name not in names

        print("\n=== TODOS OS PASSOS OK ===")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
