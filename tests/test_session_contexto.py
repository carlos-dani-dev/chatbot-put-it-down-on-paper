"""Contexto da sessão: o que o agente enxerga a cada rodada, e a tradução dos IDs que a LLM usa.

A LLM nunca escreve nome livre: ela escolhe IDs do contexto ("ex:3", "set:7"). Estas funções
são a fronteira de segurança — qualquer ID que não seja da sessão atual tem que ser recusado.
"""
import pytest

from agent.context.session import ExerciseInfo, SessionContext, SetInfo, build_context


def contexto(exercises=(), sets=()):
    """SessionContext montado na mão — sem banco, pra testar só a lógica dos IDs."""
    return SessionContext(ws=None, plan_name=None, exercises=list(exercises), sets=list(sets),
                          pending_action=None, history="")


AGACHAMENTO = ExerciseInfo(id=3, name="Agachamento livre", target_sets=2, done_sets=0)
SERIE_10 = SetInfo(id=10, exercise_id=3, exercise_name="Agachamento livre", weight_kg=60, reps=10, set_number=1)
SERIE_11 = SetInfo(id=11, exercise_id=3, exercise_name="Agachamento livre", weight_kg=60, reps=8, set_number=2)


@pytest.mark.parametrize(
    ("valor", "encontra"),
    [
        ("ex:3", True),
        ("ex:999", False),     # ID que não está na sessão
        ("plan:3", False),     # prefixo de outra coisa
        ("ex:abc", False),     # lixo
        ("3", False),          # sem prefixo
        ("", False),
        (None, False),
    ],
)
def test_find_exercise_so_aceita_ids_da_sessao(valor, encontra):
    ctx = contexto(exercises=[AGACHAMENTO])
    assert (ctx.find_exercise(valor) is AGACHAMENTO) is encontra


def test_find_set_ids_separa_validos_dos_recusados():
    ctx = contexto(sets=[SERIE_10, SERIE_11])

    validos, recusados = ctx.find_set_ids(["set:10", "set:999", "ex:10", "set:x", "set:11"])

    assert validos == [10, 11]
    assert recusados == ["set:999", "ex:10", "set:x"]


def test_ids_oferecidos_a_llm():
    ctx = contexto(exercises=[AGACHAMENTO], sets=[SERIE_10, SERIE_11])
    assert ctx.exercise_ids() == ["ex:3"]
    assert ctx.set_ids() == ["set:10", "set:11"]


# ---- daqui pra baixo, com banco: as fixtures db/user/make_plan/... vêm do conftest.py ----

def test_treino_com_plano_traz_exercicios_na_ordem_do_plano_com_metas(db, user, make_plan, start_workout):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1), ("Panturrilha", 4)]))

    ctx = build_context(db, user, ws)

    assert ctx.plan_name == "Perna"
    assert [(e.name, e.target_sets, e.done_sets) for e in ctx.exercises] == [
        ("Agachamento livre", 2, 0), ("Stiff", 1, 0), ("Panturrilha", 4, 0),
    ]


def test_exercicio_extra_entra_no_fim_sem_meta(db, user, make_plan, start_workout, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2)]))
    log(ws, "Agachamento livre", (60, 10))
    log(ws, "Cadeira extensora", (40, 12), (40, 12))  # não está no plano

    ctx = build_context(db, user, ws)

    assert [(e.name, e.target_sets, e.done_sets) for e in ctx.exercises] == [
        ("Agachamento livre", 2, 1), ("Cadeira extensora", None, 2),
    ]


def test_treino_livre_comeca_vazio_e_mostra_o_que_foi_registrado(db, user, start_workout, log):
    ws = start_workout()  # sem plano
    assert build_context(db, user, ws).exercises == []

    log(ws, "Rosca direta", (12, 10))
    ctx = build_context(db, user, ws)

    assert ctx.plan_name is None
    assert [(e.name, e.target_sets) for e in ctx.exercises] == [("Rosca direta", None)]
    assert [(s.exercise_name, s.weight_kg, s.reps, s.set_number) for s in ctx.sets] == [("Rosca direta", 12.0, 10, 1)]


def test_series_de_outra_sessao_nao_aparecem(db, user, make_plan, start_workout, log):
    plano = make_plan()
    antiga = start_workout(plano)
    log(antiga, "Agachamento livre", (50, 10))
    nova = start_workout(plano)  # abre outra sessão (a antiga é abandonada)

    ctx = build_context(db, user, nova)

    assert ctx.sets == []
    assert ctx.exercises[0].done_sets == 0


def test_acao_pendente_vem_do_estado_do_usuario(db, user, start_workout):
    ws = start_workout()
    user.state_data = {"pending_action": {"type": "delete_sets", "set_ids": [1]}}

    assert build_context(db, user, ws).pending_action == {"type": "delete_sets", "set_ids": [1]}
