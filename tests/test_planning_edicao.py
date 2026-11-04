"""planning — editar um plano salvo (edit_plan -> add/remove/set_sets -> save_draft no MESMO plano)
e as travas contra exercício inventado e plano com nome repetido."""
import pytest

from agent.agents import planning as agente
from agent.context.planning import build_context
from agent.models import WorkoutSet
from agent.repos import plan_repo
from agent.tools import planning as tools


def ctx(db, user):
    return build_context(db, user)


def rascunho(user):
    return (user.state_data or {}).get("plan_draft")


def itens(db, plan):
    """[(nome, séries), ...] do plano salvo, na ordem."""
    return [(ex.name, pe.target_sets) for pe, ex in plan_repo.get_plan_exercises(db, plan.id)]


@pytest.fixture
def push(make_plan):
    return make_plan("Push", [("Supino", 4), ("Desenvolvimento", 3), ("Tríceps corda", 3)])


# ---------------------------------------------------------------- abrir pra edição

def test_editar_abre_o_plano_salvo_como_rascunho(db, user, push):
    r = tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")

    assert r["ok"] and r["editing"] == "Push"
    assert rascunho(user) == {
        "name": "Push", "plan_id": push.id,
        "items": [{"name": "Supino", "target_sets": 4}, {"name": "Desenvolvimento", "target_sets": 3},
                  {"name": "Tríceps corda", "target_sets": 3}],
    }


def test_editar_com_outro_plano_aberto_e_recusado(db, user, push):
    user.state_data = {"plan_draft": {"name": "Pull", "items": [{"name": "Remada", "target_sets": 3}]}}

    r = tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")

    assert r["ok"] is False and "Salve ou descarte" in r["error"]
    assert rascunho(user)["name"] == "Pull"


def test_editar_plano_inexistente(db, user, push):
    assert tools.edit_plan(db, user, ctx(db, user), "plan:999")["ok"] is False


# ---------------------------------------------------------------- mexer no rascunho

def test_adicionar_tirar_e_mudar_series(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")

    tools.add_to_draft(db, user, ctx(db, user), [{"name": "Crucifixo", "target_sets": 3}])
    tools.remove_from_draft(db, user, ctx(db, user), ["desenvolvimento"])  # sem diferenciar maiúsculas
    r = tools.set_sets(db, user, ctx(db, user), "Triceps corda", 4)          # nem acento

    assert r["items"] == [{"name": "Supino", "target_sets": 4}, {"name": "Tríceps corda", "target_sets": 4},
                          {"name": "Crucifixo", "target_sets": 3}]
    assert itens(db, push) == [("Supino", 4), ("Desenvolvimento", 3), ("Tríceps corda", 3)]  # nada salvo ainda


def test_tirar_exercicio_que_nao_esta_no_plano_nao_tira_nada(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")

    r = tools.remove_from_draft(db, user, ctx(db, user), ["Supino", "Agachamento"])

    assert r["ok"] is False and "Agachamento" in r["error"]
    assert len(rascunho(user)["items"]) == 3


def test_mudar_series_de_exercicio_que_nao_esta_no_plano(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    r = tools.set_sets(db, user, ctx(db, user), "Agachamento", 3)
    assert r["ok"] is False and "Supino" in r["error"]  # mostra o que tem no plano


@pytest.mark.parametrize("series", [0, 21])
def test_series_invalidas(db, user, push, series):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    assert tools.set_sets(db, user, ctx(db, user), "Supino", series)["ok"] is False


def test_sem_plano_aberto_nao_da_pra_tirar_nem_mudar(db, user, push):
    assert "edit_plan" in tools.remove_from_draft(db, user, ctx(db, user), ["Supino"])["error"]
    assert "edit_plan" in tools.set_sets(db, user, ctx(db, user), "Supino", 3)["error"]


def test_adicionar_exercicio_que_ja_esta_no_plano_e_recusado(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")

    r = tools.add_to_draft(db, user, ctx(db, user), [{"name": "supino", "target_sets": 5}])

    assert r["ok"] is False and "set_sets" in r["error"]
    assert len(rascunho(user)["items"]) == 3


# ---------------------------------------------------------------- salvar / descartar

def test_salvar_atualiza_o_mesmo_plano(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    tools.add_to_draft(db, user, ctx(db, user), [{"name": "Crucifixo", "target_sets": 3}])
    tools.remove_from_draft(db, user, ctx(db, user), ["Desenvolvimento"])
    tools.set_sets(db, user, ctx(db, user), "Supino", 5)

    r = tools.save_draft(db, user, ctx(db, user))

    assert r["ok"] and r["plan_id"] == push.id and r["updated"] is True
    assert itens(db, push) == [("Supino", 5), ("Tríceps corda", 3), ("Crucifixo", 3)]
    assert plan_repo.list_active_plan_names(db, user) == ["Push"]  # nenhum plano novo
    assert rascunho(user) is None


def test_salvar_mantem_o_historico_ligado_ao_plano(db, user, push, start_workout, log):
    ws = start_workout(push)
    log(ws, "Supino", (80, 8))
    log(ws, "Desenvolvimento", (30, 10))
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    tools.remove_from_draft(db, user, ctx(db, user), ["Desenvolvimento"])
    tools.set_sets(db, user, ctx(db, user), "Supino", 5)

    tools.save_draft(db, user, ctx(db, user))

    supino, desenvolvimento = sorted(db.query(WorkoutSet).all(), key=lambda s: s.id)
    db.refresh(supino), db.refresh(desenvolvimento)
    assert supino.plan_exercise_id is not None                     # continua ligado ao item do plano
    item_supino = next(pe for pe, ex in plan_repo.get_plan_exercises(db, push.id) if ex.name == "Supino")
    assert supino.plan_exercise_id == item_supino.id and item_supino.target_sets == 5
    assert desenvolvimento.plan_exercise_id is None                # o item saiu, mas a série continua
    assert ws.plan_id == push.id


def test_descartar_a_edicao_deixa_o_plano_como_estava(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    tools.remove_from_draft(db, user, ctx(db, user), ["Supino"])

    r = tools.discard_draft(db, user, ctx(db, user))

    assert r["ok"] and "continua como estava" in r["note"]
    assert itens(db, push) == [("Supino", 4), ("Desenvolvimento", 3), ("Tríceps corda", 3)]


def test_plano_excluido_durante_a_edicao_nao_e_salvo(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    plan_repo.archive_plan(db, push)

    r = tools.save_draft(db, user, ctx(db, user))

    assert r["ok"] is False and "excluído" in r["error"]


# ---------------------------------------------------------------- limites do Grátis

def test_gratis_com_um_plano_pode_editar_ele(db, user, push):
    """O limite de 1 plano é pra criar plano novo — editar o único que tem continua liberado."""
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    tools.add_to_draft(db, user, ctx(db, user), [{"name": "Crucifixo", "target_sets": 3}])

    assert tools.save_draft(db, user, ctx(db, user))["ok"] is True


def test_gratis_na_edicao_continua_com_limite_de_exercicios(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")  # 3 exercícios, cabem mais 2

    r = tools.add_to_draft(db, user, ctx(db, user), [{"name": n, "target_sets": 3} for n in ("A", "B", "C")])

    assert r["ok"] is False and r["limit_reached"] == "exercises"


# ---------------------------------------------------------------- as travas

@pytest.mark.parametrize("nome", ["exercícios", "Exercicio", "treino", "  SÉRIES "])
def test_nome_generico_nao_vira_exercicio(db, user, nome):
    r = tools.add_to_draft(db, user, ctx(db, user), [{"name": nome, "target_sets": 1}], name="Pull")

    assert r["ok"] is False and "nunca invente" in r["error"]
    assert rascunho(user) is None


def test_nome_de_plano_repetido_aponta_pra_edicao(db, user, make_pro, push):
    make_pro(user)  # Pro: o que barra é o nome repetido, não o limite de planos

    r = tools.add_to_draft(db, user, ctx(db, user), [{"name": "Crucifixo", "target_sets": 3}], name="push")

    assert r["ok"] is False and "edit_plan" in r["error"] and f"plan:{push.id}" in r["error"]
    assert rascunho(user) is None


def test_salvar_rascunho_antigo_com_nome_repetido_e_recusado(db, user, make_pro, push):
    """Rascunho criado antes desta trava (ex.: o 'Push' com 'exercícios' que motivou a mudança)."""
    make_pro(user)
    user.state_data = {"plan_draft": {"name": "Push", "items": [{"name": "exercícios", "target_sets": 1}]}}

    r = tools.save_draft(db, user, ctx(db, user))

    assert r["ok"] is False and "edit_plan" in r["error"]
    assert plan_repo.list_active_plan_names(db, user) == ["Push"]


# ---------------------------------------------------------------- ferramentas oferecidas

def test_com_planos_e_sem_rascunho_oferece_editar(db, user, push):
    assert "edit_plan" in [d.name for d in tools.build_declarations(ctx(db, user))]


def test_tirar_e_mudar_series_so_aceitam_exercicios_do_plano_aberto(db, user, push):
    tools.edit_plan(db, user, ctx(db, user), f"plan:{push.id}")
    decls = {d.name: d for d in tools.build_declarations(ctx(db, user))}

    assert decls["set_sets"].parameters.properties["exercise"].enum == ["Supino", "Desenvolvimento", "Tríceps corda"]
    assert decls["remove_from_draft"].parameters.properties["exercises"].items.enum == [
        "Supino", "Desenvolvimento", "Tríceps corda"]
    assert "edit_plan" not in decls


# ---------------------------------------------------------------- a conversa do print, com o Gemini falso

def test_adicionar_exercicios_no_plano_existente(db, user, push, gemini):
    fake = gemini.script(
        gemini.call("edit_plan", plan_id=f"plan:{push.id}"),
        gemini.text("Abri o *Push*: Supino 4x, Desenvolvimento 3x, Tríceps corda 3x. Quais exercícios quer adicionar?"),
    )
    resposta, estado = agente.handle(db, user, "Quero adicionar exercícios no plano Push")

    assert estado == "planning" and "Quais exercícios" in resposta.text
    assert "Editando o plano salvo 'Push'" in fake.system_prompt(1)
    assert "NUNCA invente exercícios" in fake.system_prompt(0)

    gemini.script(
        gemini.call("add_to_draft", exercises=[{"name": "Crucifixo", "target_sets": 3}]),
        gemini.text("Push agora com Crucifixo (3x). Quer *Salvar o plano*?"),
    )
    agente.handle(db, user, "crucifixo 3 séries")

    gemini.script(gemini.call("save_draft"), gemini.text("Plano *Push* atualizado!"))
    resposta, estado = agente.handle(db, user, "salva")

    assert estado == "idle"
    assert plan_repo.list_active_plan_names(db, user) == ["Push"]
    assert itens(db, push)[-1] == ("Crucifixo", 3)


# ---------------------------------------------------------------- dois planos (o caso do print)

def test_com_dois_planos_o_prompt_mostra_qual_id_e_de_qual_plano(db, user, make_pro, make_plan, gemini):
    """Antes o prompt só tinha os nomes e as ferramentas só os IDs: com 'Peito' e 'Leg 2' salvos, a LLM
    adivinhou plan:2 pra "remova o supino reto do treino de peito" e abriu o Leg 2."""
    make_pro(user)
    peito = make_plan("Peito", [("Supino reto", 4)])
    leg = make_plan("Leg 2", [("Cadeira abdutora", 2)])
    fake = gemini.script(
        gemini.call("edit_plan", plan_id=f"plan:{peito.id}"),
        gemini.text("Abri o *Peito*."),
    )

    agente.handle(db, user, "Remova o supino reto do treino de peito")

    assert f"[plan:{peito.id}] Peito" in fake.system_prompt(0) and f"[plan:{leg.id}] Leg 2" in fake.system_prompt(0)
    assert rascunho(user)["plan_id"] == peito.id


@pytest.mark.parametrize("nome", ["treino de Leg 2", "Plano Peito", "Leg 2"])
def test_nome_de_treino_ou_de_plano_nao_vira_exercicio(db, user, make_pro, make_plan, nome):
    """No print, "Adicione o treino de Leg 2" virou um exercício chamado "treino de Leg 2"."""
    make_pro(user)
    make_plan("Peito", [("Supino reto", 4)])
    r = tools.add_to_draft(db, user, ctx(db, user), [{"name": nome, "target_sets": 1}], name="Leg 2")

    assert r["ok"] is False and "nunca invente" in r["error"]
