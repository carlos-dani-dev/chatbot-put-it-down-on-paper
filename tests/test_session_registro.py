"""log_sets — registrar séries: validação, numeração e situação da meta (calculada em código).

Aqui a ferramenta é chamada direto, como o agente faria depois que a LLM escolhe os argumentos.
"""
import pytest
from sqlalchemy import select

from agent.context.session import build_context
from agent.models import Exercise, WorkoutSet
from agent.tools.session import log_sets


def series_no_banco(db, ws):
    rows = db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id)).all()
    return [(float(s.weight_kg), s.reps, s.set_number) for s in rows]


# ---------------------------------------------------------------- validação

@pytest.mark.parametrize(
    ("sets", "erro"),
    [
        ([], "Nenhuma série informada"),
        ([{"weight_kg": 60, "reps": 10}] * 21, "Máximo de 20 séries"),
        ([{"weight_kg": -1, "reps": 10}], "carga inválida"),
        ([{"weight_kg": 501, "reps": 10}], "carga inválida"),
        ([{"weight_kg": None, "reps": 10}], "carga inválida"),
        ([{"weight_kg": 60, "reps": 0}], "repetições inválidas"),
        ([{"weight_kg": 60, "reps": 101}], "repetições inválidas"),
        # a 2ª série é inválida: NADA é gravado, nem a 1ª
        ([{"weight_kg": 60, "reps": 10}, {"weight_kg": 60, "reps": 0}], "Série 2"),
    ],
)
def test_entrada_invalida_e_recusada_sem_gravar_nada(db, user, make_plan, start_workout, exercise_id, sets, erro):
    ws = start_workout(make_plan())
    ctx = build_context(db, user, ws)

    resultado = log_sets(db, user, ws, ctx, exercise_id(ctx, "Agachamento livre"), sets)

    assert resultado["ok"] is False
    assert erro in resultado["error"]
    assert series_no_banco(db, ws) == []


def test_exercise_id_fora_da_sessao_e_recusado(db, user, make_plan, start_workout):
    ws = start_workout(make_plan())
    ctx = build_context(db, user, ws)

    resultado = log_sets(db, user, ws, ctx, "ex:999", [{"weight_kg": 60, "reps": 10}])

    assert resultado["ok"] is False
    assert "exercise_id inválido" in resultado["error"]


def test_exercicio_novo_sem_nome_e_recusado(db, user, start_workout):
    ws = start_workout()
    resultado = log_sets(db, user, ws, build_context(db, user, ws), "new", [{"weight_kg": 10, "reps": 10}])
    assert resultado["ok"] is False


def test_peso_corporal_zero_kg_e_aceito(db, user, start_workout, log):
    ws = start_workout()
    assert log(ws, "Barra fixa", (0, 8))["ok"] is True


def test_descanso_fora_da_faixa_e_ignorado_mas_a_serie_entra(db, user, start_workout):
    ws = start_workout()
    resultado = log_sets(db, user, ws, build_context(db, user, ws), "new",
                         [{"weight_kg": 20, "reps": 10, "rest_seconds": 99999}], new_exercise_name="Remada")

    assert resultado["ok"] is True
    assert db.scalar(select(WorkoutSet.rest_seconds)) is None


# ---------------------------------------------------------------- gravação

def test_series_gravadas_com_numeracao_que_continua_entre_mensagens(db, start_workout, make_plan, log):
    ws = start_workout(make_plan())

    primeira = log(ws, "Agachamento livre", (60, 10), (60, 8))
    segunda = log(ws, "Agachamento livre", (65, 6))

    assert primeira["set_range"] == [1, 2]
    assert segunda["set_range"] == [3, 3]
    assert series_no_banco(db, ws) == [(60.0, 10, 1), (60.0, 8, 2), (65.0, 6, 3)]


def test_serie_de_exercicio_do_plano_fica_ligada_ao_item_do_plano(db, make_plan, start_workout, log):
    ws = start_workout(make_plan())
    log(ws, "Agachamento livre", (60, 10))
    log(ws, "Leg press", (100, 10))  # fora do plano

    ligacoes = db.scalars(select(WorkoutSet.plan_exercise_id).order_by(WorkoutSet.id)).all()
    assert ligacoes[0] is not None
    assert ligacoes[1] is None


def test_exercicio_novo_reaproveita_o_cadastrado_ignorando_maiusculas(db, start_workout, log):
    db.add(Exercise(name="Supino Reto"))
    db.flush()
    ws = start_workout()

    resultado = log(ws, "supino reto", (80, 8))

    assert resultado["exercise_name"] == "Supino Reto"  # o nome cadastrado, não o digitado
    assert len(db.scalars(select(Exercise)).all()) == 1  # não duplicou


def test_registro_marca_o_exercicio_atual_da_sessao(db, start_workout, make_plan, log):
    ws = start_workout(make_plan())
    log(ws, "Stiff", (40, 12))
    assert db.get(Exercise, ws.current_exercise_id).name == "Stiff"


# ---------------------------------------------------------------- situação da meta (o bug do "meta batida com 1 de 2")

def test_o_caso_do_bug_primeira_serie_de_meta_2(start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1)]))

    r = log(ws, "Agachamento livre", (60, 10))

    assert (r["total_done"], r["target_sets"]) == (1, 2)
    assert r["target_reached"] is False
    assert r["remaining_sets"] == 1
    assert r["next_pending_exercise"] == "Stiff"
    assert r["all_targets_reached"] is False


def test_meta_batida_e_sugere_o_proximo_pendente(start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1)]))

    r = log(ws, "Agachamento livre", (60, 10), (60, 10))

    assert r["target_reached"] is True
    assert r["remaining_sets"] == 0
    assert r["next_pending_exercise"] == "Stiff"


def test_proximo_pendente_nunca_e_o_proprio_exercicio(start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 3), ("Stiff", 1)]))
    r = log(ws, "Agachamento livre", (60, 10))  # ainda falta agachamento, mas a sugestão é OUTRO
    assert r["next_pending_exercise"] == "Stiff"


def test_ultimo_exercicio_completa_o_plano(start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 1), ("Stiff", 1)]))
    log(ws, "Agachamento livre", (60, 10))

    r = log(ws, "Stiff", (40, 12))

    assert r["all_targets_reached"] is True
    assert r["next_pending_exercise"] is None


def test_dois_exercicios_na_mesma_rodada_com_contexto_antigo(db, user, start_workout, make_plan, exercise_id):
    """O agente executa várias ferramentas da mesma resposta com o MESMO ctx, montado antes de
    qualquer registro. A situação da meta tem que vir do banco, não desse ctx desatualizado."""
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 1), ("Stiff", 1)]))
    ctx_compartilhado = build_context(db, user, ws)

    log_sets(db, user, ws, ctx_compartilhado, exercise_id(ctx_compartilhado, "Agachamento livre"), [{"weight_kg": 50, "reps": 8}])
    r = log_sets(db, user, ws, ctx_compartilhado, exercise_id(ctx_compartilhado, "Stiff"), [{"weight_kg": 50, "reps": 8}])

    assert r["all_targets_reached"] is True


def test_exercicio_extra_num_treino_com_plano_nao_tem_meta_mas_ve_o_plano(start_workout, make_plan, log):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2)]))

    r = log(ws, "Leg press", (100, 10))

    assert "target_reached" not in r and "remaining_sets" not in r
    assert r["next_pending_exercise"] == "Agachamento livre"
    assert r["all_targets_reached"] is False


def test_treino_livre_nao_tem_nenhum_campo_de_meta(start_workout, log):
    ws = start_workout()

    r = log(ws, "Rosca direta", (12, 10))

    for campo in ("target_reached", "remaining_sets", "next_pending_exercise", "all_targets_reached"):
        assert campo not in r
