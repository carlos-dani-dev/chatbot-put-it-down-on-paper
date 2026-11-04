"""As demais ferramentas da sessão: apagar séries (sempre em 2 passos), finalizar, listar,
desempenho anterior — e quais ferramentas são oferecidas à LLM em cada situação.
"""
from datetime import datetime

import pytest

from sqlalchemy import select

from agent.context.session import build_context
from agent.models import WorkoutSet
from agent.tools import session as tools


def ids_das_series(db, ws):
    return db.scalars(select(WorkoutSet.id).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)).all()


# ---------------------------------------------------------------- apagar séries: propor -> confirmar

def test_pedir_exclusao_nao_apaga_so_deixa_pendente(db, user, start_workout, make_plan, log):
    ws = start_workout(make_plan())
    log(ws, "Agachamento livre", (60, 10), (60, 8))
    ctx = build_context(db, user, ws)
    alvo = ctx.sets[1]

    r = tools.request_delete_sets(db, user, ws, ctx, [f"set:{alvo.id}"])

    assert r["ok"] is True and r["requires_confirmation"] is True
    assert r["preview"] == ["Agachamento livre (série 2: 60kg x 8)"]
    assert user.state_data["pending_action"] == {"type": "delete_sets", "set_ids": [alvo.id]}
    assert len(ids_das_series(db, ws)) == 2  # nada apagado ainda


def test_pedir_exclusao_so_com_ids_invalidos_e_recusado(db, user, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10))

    r = tools.request_delete_sets(db, user, ws, build_context(db, user, ws), ["set:999"])

    assert r["ok"] is False
    assert "pending_action" not in (user.state_data or {})


def test_confirmar_apaga_as_series_pendentes(db, user, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10), (12, 8))
    primeira, segunda = ids_das_series(db, ws)
    tools.request_delete_sets(db, user, ws, build_context(db, user, ws), [f"set:{segunda}"])

    r = tools.confirm_pending_action(db, user, ws, build_context(db, user, ws))

    assert r == {"ok": True, "action": "delete_sets", "deleted_count": 1, "missing_ids": []}
    assert ids_das_series(db, ws) == [primeira]
    assert "pending_action" not in user.state_data


def test_confirmar_nunca_apaga_serie_de_outra_sessao(db, user, start_workout, log):
    """Mesmo que o pendente aponte pra uma série de outro treino, a confirmação só apaga desta sessão."""
    antiga = start_workout()
    log(antiga, "Rosca direta", (10, 10))
    [serie_antiga] = ids_das_series(db, antiga)
    atual = start_workout()
    user.state_data = {"pending_action": {"type": "delete_sets", "set_ids": [serie_antiga]}}

    r = tools.confirm_pending_action(db, user, atual, build_context(db, user, atual))

    assert r["deleted_count"] == 0 and r["missing_ids"] == [serie_antiga]
    assert ids_das_series(db, antiga) == [serie_antiga]


def test_confirmar_sem_pendente_e_recusado(db, user, start_workout):
    ws = start_workout()
    assert tools.confirm_pending_action(db, user, ws, build_context(db, user, ws))["ok"] is False


def test_cancelar_limpa_o_pendente_sem_apagar(db, user, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10))
    [serie] = ids_das_series(db, ws)
    tools.request_delete_sets(db, user, ws, build_context(db, user, ws), [f"set:{serie}"])

    r = tools.cancel_pending_action(db, user, ws, build_context(db, user, ws))

    assert r["ok"] is True
    assert "pending_action" not in user.state_data
    assert ids_das_series(db, ws) == [serie]


def test_desfazer_a_ultima_propoe_a_ultima_serie_daquele_exercicio(db, user, start_workout, make_plan, log, exercise_id):
    ws = start_workout(make_plan())
    log(ws, "Agachamento livre", (60, 10), (60, 8))
    log(ws, "Stiff", (40, 12))  # registrada depois, mas é de OUTRO exercício
    ctx = build_context(db, user, ws)

    r = tools.undo_last_set(db, user, ws, ctx, exercise_id(ctx, "Agachamento livre"))

    assert r["preview"] == ["Agachamento livre (série 2: 60kg x 8)"]
    assert r["requires_confirmation"] is True


def test_desfazer_sem_series_do_exercicio_e_recusado(db, user, start_workout, make_plan, exercise_id):
    ws = start_workout(make_plan())
    ctx = build_context(db, user, ws)
    assert tools.undo_last_set(db, user, ws, ctx, exercise_id(ctx, "Stiff"))["ok"] is False


# ---------------------------------------------------------------- finalizar

def test_finalizar_fecha_a_sessao_e_volta_pro_menu(db, user, start_workout, log):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10), (12, 8))

    r = tools.finish_session(db, user, ws, build_context(db, user, ws))

    assert r == {"ok": True, "sets_registered": 2, "next_state": "idle"}
    assert isinstance(ws.ended_at, datetime) and ws.ended_reason == "finished"


# ---------------------------------------------------------------- listar exercícios

def test_listar_exercicios_traz_a_meta_ja_resolvida(db, user, start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1)]))
    log(ws, "Stiff", (40, 12))
    log(ws, "Leg press", (100, 10))

    r = tools.list_exercises(db, user, ws, build_context(db, user, ws))

    assert r["exercises"] == [
        {"name": "Agachamento livre", "done_sets": 0, "target_sets": 2, "remaining_sets": 2, "target_reached": False},
        {"name": "Stiff", "done_sets": 1, "target_sets": 1, "remaining_sets": 0, "target_reached": True},
        {"name": "Leg press", "done_sets": 1, "target_sets": None},  # extra: sem campos de meta
    ]


def test_listar_exercicios_em_treino_livre_vazio(db, user, start_workout):
    ws = start_workout()
    r = tools.list_exercises(db, user, ws, build_context(db, user, ws))
    assert r["exercises"] == [] and "Treino livre" in r["note"]


# ---------------------------------------------------------------- desempenho anterior

def test_desempenho_anterior_vem_da_ultima_sessao_e_nao_da_atual(db, user, start_workout, make_plan, log, exercise_id):
    plano = make_plan()
    retrasada = start_workout(plano)
    log(retrasada, "Agachamento livre", (50, 10))
    passada = start_workout(plano)
    log(passada, "Agachamento livre", (55, 10), (57.5, 8))
    hoje = start_workout(plano)
    log(hoje, "Agachamento livre", (60, 10))  # a de hoje não conta como "anterior"
    ctx = build_context(db, user, hoje)

    r = tools.check_performance(db, user, hoje, ctx, exercise_id(ctx, "Agachamento livre"))

    assert r["has_history"] is True
    assert r["sets"] == [{"weight_kg": 55.0, "reps": 10}, {"weight_kg": 57.5, "reps": 8}]


def test_desempenho_anterior_sem_historico(db, user, start_workout, make_plan, exercise_id):
    ws = start_workout(make_plan())
    ctx = build_context(db, user, ws)
    r = tools.check_performance(db, user, ws, ctx, exercise_id(ctx, "Stiff"))
    assert r == {"ok": True, "has_history": False, "exercise_name": "Stiff"}


def test_desempenho_anterior_nao_mistura_usuarios(db, user, make_user, start_workout, make_plan, log, exercise_id):
    outro = make_user()
    log(start_workout(owner=outro), "Agachamento livre", (200, 1), owner=outro)
    ws = start_workout(make_plan())
    ctx = build_context(db, user, ws)

    r = tools.check_performance(db, user, ws, ctx, exercise_id(ctx, "Agachamento livre"))

    assert r["has_history"] is False


# ---------------------------------------------------------------- ferramentas oferecidas à LLM

def nomes(decls):
    return [d.name for d in decls]


def test_sessao_vazia_so_oferece_registrar_finalizar_e_listar(db, user, start_workout):
    ws = start_workout()
    assert nomes(tools.build_declarations(build_context(db, user, ws))) == ["log_sets", "finish_session", "list_exercises"]


def test_com_series_e_pendente_oferece_tudo(db, user, start_workout, make_plan, log):
    ws = start_workout(make_plan())
    log(ws, "Agachamento livre", (60, 10))
    user.state_data = {"pending_action": {"type": "delete_sets", "set_ids": [1]}}

    oferecidas = nomes(tools.build_declarations(build_context(db, user, ws)))

    assert oferecidas == [
        "log_sets", "request_delete_sets", "undo_last_set", "confirm_pending_action",
        "cancel_pending_action", "finish_session", "list_exercises", "check_performance",
    ]


def test_ids_oferecidos_sao_exatamente_os_da_sessao(db, user, start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2)]))
    log(ws, "Agachamento livre", (60, 10))
    ctx = build_context(db, user, ws)

    decls = {d.name: d for d in tools.build_declarations(ctx)}

    assert decls["log_sets"].parameters.properties["exercise_id"].enum == ctx.exercise_ids() + ["new"]
    assert decls["request_delete_sets"].parameters.properties["set_ids"].items.enum == ctx.set_ids()


# ---------------------------------------------------------------- comentário montado em código

@pytest.mark.parametrize("resultado,esperado", [
    ({"total_done": 1, "target_sets": 3, "target_reached": False}, "Boa! Manda a próxima. 💪"),
    ({"total_done": 3, "target_sets": 3, "target_reached": True, "next_pending_exercise": "Crucifixo"},
     "Bora pro *Crucifixo*! 💪"),
    ({"total_done": 3, "target_sets": 3, "target_reached": True, "next_pending_exercise": None},
     "Manda o próximo exercício! 💪"),
    ({"total_done": 3, "target_sets": 3, "target_reached": True, "all_targets_reached": True},
     "Quando quiser, é só mandar *Finalizar treino*. 🏁"),
    ({"total_done": 2, "target_sets": None}, "Isso aí, segue o baile! 🏋️"),  # treino livre: não fala em meta
])
def test_comentario_depois_do_registro(resultado, esperado):
    assert tools.log_sets_reaction(resultado) == esperado


def test_comentario_nunca_fala_em_meta_quando_ela_nao_foi_batida():
    for feitas in range(1, 10):
        texto = tools.log_sets_reaction({"total_done": feitas, "target_sets": 10, "target_reached": False})
        assert "meta" not in texto.lower() and "Finalizar" not in texto
