"""planning — montar plano (rascunho -> salvar), limites do Grátis, excluir em 2 passos."""
import pytest

from agent.context.planning import build_context
from agent.repos import plan_repo
from agent.tools import planning as tools


def ex(*pares):
    """ex(("Supino", 3), ("Crucifixo", 2)) -> lista de exercícios no formato da ferramenta."""
    return [{"name": n, "target_sets": t} for n, t in pares]


def rascunho(user):
    return (user.state_data or {}).get("plan_draft")


def planos_ativos(db, user):
    return plan_repo.list_active_plan_names(db, user)


# ---------------------------------------------------------------- rascunho

def test_criar_rascunho(db, user):
    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    assert r == {"ok": True, "plan_name": "Peito", "items": ex(("Supino", 3)), "editing": False}
    assert rascunho(user) == {"name": "Peito", "items": ex(("Supino", 3))}
    assert planos_ativos(db, user) == []  # rascunho não é plano salvo


def test_acrescentar_ao_rascunho_existente(db, user):
    tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Crucifixo", 2)))

    assert [i["name"] for i in r["items"]] == ["Supino", "Crucifixo"]


def test_rascunho_novo_exige_nome(db, user):
    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)))
    assert r["ok"] is False and rascunho(user) is None


@pytest.mark.parametrize(
    ("exercicios", "erro"),
    [
        ([], "Nenhum exercício"),
        (ex(("Supino", 0)), "séries inválida"),
        (ex(("Supino", 21)), "séries inválida"),
        ([{"name": "  ", "target_sets": 3}], "nome vazio"),
        (ex(*[(f"Ex {i}", 3) for i in range(21)]), "Máximo de 20"),
    ],
)
def test_entrada_invalida_nao_mexe_no_rascunho(db, user, exercicios, erro):
    r = tools.add_to_draft(db, user, build_context(db, user), exercicios, name="Peito")
    assert r["ok"] is False and erro in r["error"]
    assert rascunho(user) is None


def test_nome_do_exercicio_e_guardado_literal(db, user):
    """Proteção do caso 'barra' x 'halteres': o nome vai como veio, sem normalizar."""
    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Supino reto com barra", 3)), name="Peito")
    assert r["items"][0]["name"] == "Supino reto com barra"


def test_salvar_cria_o_plano_limpa_o_rascunho_e_volta_pro_menu(db, user):
    tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3), ("Crucifixo", 2)), name="Peito")

    r = tools.save_draft(db, user, build_context(db, user))

    assert r["ok"] is True and r["next_state"] == "idle"
    assert planos_ativos(db, user) == ["Peito"]
    assert [(e.name, pe.target_sets) for pe, e in plan_repo.get_plan_exercises(db, r["plan_id"])] == [
        ("Supino", 3), ("Crucifixo", 2),
    ]
    assert rascunho(user) is None


def test_salvar_sem_rascunho_e_recusado(db, user):
    assert tools.save_draft(db, user, build_context(db, user))["ok"] is False


def test_descartar(db, user):
    tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    r = tools.discard_draft(db, user, build_context(db, user))

    assert r == {"ok": True, "next_state": "idle", "plan_name": "Peito", "editing": False}
    assert rascunho(user) is None and planos_ativos(db, user) == []


# ---------------------------------------------------------------- limites do Grátis (1 plano, 5 exercícios)

def test_gratis_com_um_plano_nao_comeca_outro(db, user, make_plan):
    make_plan("Perna")

    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    assert r["ok"] is False and r["limit_reached"] == "plans"
    assert rascunho(user) is None


def test_exercicios_alem_do_limite_recusam_o_pedido_inteiro(db, user):
    """Com 4 no rascunho e 3 pedidos (limite 5), NADA entra — nunca 'metade' sem o usuário saber."""
    tools.add_to_draft(db, user, build_context(db, user), ex(*[(f"Ex {i}", 3) for i in range(4)]), name="Peito")

    r = tools.add_to_draft(db, user, build_context(db, user), ex(("A", 3), ("B", 3), ("C", 3)))

    assert r["ok"] is False and r["limit_reached"] == "exercises"
    assert "cabem só mais 1" in r["error"]
    assert len(rascunho(user)["items"]) == 4


def test_exatamente_no_limite_e_aceito(db, user):
    r = tools.add_to_draft(db, user, build_context(db, user), ex(*[(f"Ex {i}", 3) for i in range(5)]), name="Peito")
    assert r["ok"] is True


def test_salvar_confere_o_limite_de_novo(db, user, make_plan):
    """Plano criado por outro caminho entre o rascunho e o salvar: a última barreira segura."""
    tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")
    make_plan("Perna")

    r = tools.save_draft(db, user, build_context(db, user))

    assert r["ok"] is False and r["limit_reached"] == "plans"


def test_pro_nao_tem_limite(db, user, make_plan, make_pro):
    make_pro(user)
    make_plan("Perna")

    r = tools.add_to_draft(db, user, build_context(db, user), ex(*[(f"Ex {i}", 3) for i in range(12)]), name="Peito")

    assert r["ok"] is True and len(r["items"]) == 12


# ---------------------------------------------------------------- consultar planos

def test_listar_e_ver_exercicios(db, user, make_plan):
    plano = make_plan("Perna", [("Agachamento livre", 3)])
    ctx = build_context(db, user)

    assert tools.list_plans(db, user, ctx)["plans"] == [{"plan_id": f"plan:{plano.id}", "name": "Perna"}]
    assert tools.list_plan_exercises(db, user, ctx, f"plan:{plano.id}")["exercises"] == [
        {"name": "Agachamento livre", "target_sets": 3},
    ]
    assert tools.list_plan_exercises(db, user, ctx, "plan:999")["ok"] is False


# ---------------------------------------------------------------- excluir: propor -> confirmar

def test_pedir_exclusao_so_deixa_pendente(db, user, make_plan):
    plano = make_plan("Perna")

    r = tools.request_delete_plan(db, user, build_context(db, user), f"plan:{plano.id}")

    assert r == {"ok": True, "requires_confirmation": True, "plan_name": "Perna"}
    assert user.state_data["pending_action"] == {"type": "delete_plan", "plan_id": plano.id, "plan_name": "Perna"}
    assert planos_ativos(db, user) == ["Perna"]


def test_confirmar_arquiva_o_plano_e_volta_pro_menu(db, user, make_plan):
    plano = make_plan("Perna")
    tools.request_delete_plan(db, user, build_context(db, user), f"plan:{plano.id}")

    r = tools.confirm_pending_action(db, user, build_context(db, user))

    assert r == {"ok": True, "action": "delete_plan", "plan_name": "Perna", "next_state": "idle"}
    assert planos_ativos(db, user) == []
    assert plano.archived_at is not None  # arquivado, não apagado: o histórico de treinos continua


def test_confirmar_plano_que_nao_existe_mais(db, user):
    user.state_data = {"pending_action": {"type": "delete_plan", "plan_id": 999, "plan_name": "X"}}

    r = tools.confirm_pending_action(db, user, build_context(db, user))

    assert r["ok"] is False and "pending_action" not in user.state_data


def test_cancelar_exclusao(db, user, make_plan):
    plano = make_plan("Perna")
    tools.request_delete_plan(db, user, build_context(db, user), f"plan:{plano.id}")

    assert tools.cancel_pending_action(db, user, build_context(db, user)) == {"ok": True}
    assert planos_ativos(db, user) == ["Perna"]


def test_excluir_libera_vaga_no_gratis(db, user, make_plan):
    plano = make_plan("Perna")
    tools.request_delete_plan(db, user, build_context(db, user), f"plan:{plano.id}")
    tools.confirm_pending_action(db, user, build_context(db, user))

    r = tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    assert r["ok"] is True


# ---------------------------------------------------------------- ir treinar

def test_ir_treinar_com_rascunho_aberto_e_bloqueado(db, user):
    tools.add_to_draft(db, user, build_context(db, user), ex(("Supino", 3)), name="Peito")

    r = tools.go_to_session(db, user, build_context(db, user))

    assert r["ok"] is False and "Salve ou descarte" in r["error"]


def test_ir_treinar_sem_rascunho(db, user):
    assert tools.go_to_session(db, user, build_context(db, user)) == {"ok": True, "next_state": "session_setup"}


# ---------------------------------------------------------------- ferramentas oferecidas

def nomes(db, user):
    return [d.name for d in tools.build_declarations(build_context(db, user))]


def test_sem_rascunho_nem_planos(db, user):
    assert nomes(db, user) == ["add_to_draft", "list_plans", "assinar_pro", "go_to_session"]


def test_rascunho_vazio_nao_oferece_salvar(db, user):
    user.state_data = {"plan_draft": {"name": "Peito", "items": []}}
    oferecidas = nomes(db, user)
    assert "discard_draft" in oferecidas and "save_draft" not in oferecidas


def test_com_rascunho_planos_e_pendente(db, user, make_plan, make_pro):
    make_pro(user)
    make_plan("Perna")
    user.state_data = {"plan_draft": {"name": "Peito", "items": ex(("Supino", 3))},
                       "pending_action": {"type": "delete_plan", "plan_id": 1, "plan_name": "Perna"}}

    assert nomes(db, user) == [
        "add_to_draft", "list_plans", "remove_from_draft", "set_sets", "save_draft", "discard_draft",
        "list_plan_exercises", "request_delete_plan", "confirm_pending_action", "cancel_pending_action",
        "go_to_session",
    ]  # Pro: sem assinar_pro; com plano aberto: sem edit_plan
