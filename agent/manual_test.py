"""Teste manual de ponta a ponta do agente de `session` (tool-calling).

Roda contra o MESMO banco configurado em webhook/.env — usa um número de
telefone descartável e limpa tudo no final.

    poetry run python agent/manual_test.py
"""
from webhook.database import SessionLocal
from webhook.models import ChatMessage, Plan, User, WorkoutSession, WorkoutSet
from webhook.repos import plan_repo, session_repo, user_repo

from agent import session_agent, tools
from agent.context import build_context

TEST_PHONE = "0000000000-agent-test"


def cleanup():
    with SessionLocal.begin() as db:
        user = db.query(User).filter_by(phone_number=TEST_PHONE).first()
        if user:
            db.query(WorkoutSet).filter_by(user_id=user.id).delete()
            db.query(WorkoutSession).filter_by(user_id=user.id).delete()
            db.query(Plan).filter_by(user_id=user.id).delete()
            db.query(ChatMessage).filter_by(user_id=user.id).delete()
            db.delete(user)
            print("--- cleanup ok ---")


def main():
    cleanup()
    try:
        # 1) plano com 2 exercícios + sessão aberta
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            plan_repo.save_plan(
                db, user,
                {"name": "Peito", "items": [
                    {"name": "Supino Reto", "target_sets": 3},
                    {"name": "Crucifixo", "target_sets": 3},
                ]},
            )
            plan = plan_repo.list_active_plans(db, user, limit=8)[0]
            plan_id = plan.id
            ws = session_repo.start_session(db, user, plan_id=plan_id)
            session_id = ws.id
            user.state_data = {"session_id": session_id}
            user.state = "session"
        print(f"1) plano+sessão criados (plan_id={plan_id}, session_id={session_id})\n")

        # 2) registrar série via agente, texto livre
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_agent.handle(db, user, "supino 80kg 8 reps")
            print(f"2) log_sets via agente -> next_state={next_state}\n   {reply.text!r}\n")

        # inspeciona o que foi gravado
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            ctx = build_context(db, user, ws)
            print("   contexto após o registro:")
            for e in ctx.exercises:
                print(f"     ex:{e.id} {e.name} ({e.done_sets}/{e.target_sets})")
            for s in ctx.sets:
                print(f"     set:{s.id} {s.exercise_name} série {s.set_number}: {s.weight_kg}kg x {s.reps}")
            first_set_id = ctx.sets[0].id
            first_exercise_id = ctx.exercises[0].id
            print()

        # 3) pedir exclusão (deve só propor, sem apagar) e depois confirmar
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_agent.handle(db, user, "retire a última série de supino reto")
            print(f"3a) undo_last_set via agente -> next_state={next_state}\n    {reply.text!r}")
            print(f"    pending_action após a proposta: {user.state_data.get('pending_action')}\n")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            count_antes = session_repo.count_sets(db, ws.id)
            print(f"    séries no banco ANTES da confirmação: {count_antes} (deve ainda ter a série)\n")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_agent.handle(db, user, "sim, pode confirmar")
            print(f"3b) confirm_pending_action via agente -> next_state={next_state}\n    {reply.text!r}")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            count_depois = session_repo.count_sets(db, ws.id)
            print(f"    séries no banco DEPOIS da confirmação: {count_depois} (deve ter diminuído em 1)\n")

        # 4) exercise_id inventado -> deve ser rejeitado pela validação em código
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            ctx = build_context(db, user, ws)
            result = tools.log_sets(db, user, ws, ctx, exercise_id="ex:999999", sets=[{"weight_kg": 50, "reps": 8}])
            print(f"4) log_sets com exercise_id inventado -> {result}")
            assert result["ok"] is False, "deveria ter rejeitado o ID inventado"
            print("   OK: rejeitado como esperado\n")

        # 5) check_performance de um exercício com histórico em OUTRA sessão
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws_antiga = session_repo.start_session(db, user, plan_id=plan_id)
            ws_antiga_id = ws_antiga.id
            plan_exs = plan_repo.get_plan_exercises(db, plan_id)
            pe, ex = plan_exs[1]  # Crucifixo
            session_repo.log_sets(db, ws_antiga, ex.id, pe.id, [(20.0, 10, None), (22.5, 8, None)], "(setup teste)")
            session_repo.close_session(db, ws_antiga)
            print(f"   (setup) sessão antiga com Crucifixo criada e fechada (session_id={ws_antiga_id})\n")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_agent.handle(db, user, "como foi meu desempenho no crucifixo da última vez?")
            print(f"5) check_performance via agente -> next_state={next_state}\n   {reply.text!r}\n")

        # 6) finalizar sessão
        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            reply, next_state = session_agent.handle(db, user, "quero finalizar o treino")
            print(f"6) finish_session via agente -> next_state={next_state}\n   {reply.text!r}\n")

        with SessionLocal.begin() as db:
            user = user_repo.get_or_create_user(db, TEST_PHONE)
            ws = session_repo.get_open_session(db, user)
            print(f"   sessão aberta após finalizar: {ws} (deve ser None)")
            assert ws is None, "sessão deveria ter sido encerrada"
            assert next_state == "idle", "next_state deveria ser idle"
            print("   OK: sessão encerrada e next_state=idle\n")

        print("=== TODOS OS PASSOS OK ===")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
