"""session_setup — escolher como começar o treino: contexto, ferramentas e o que é oferecido à LLM."""
import pytest

from agent.context.session_setup import PlanInfo, SessionSetupContext, build_context
from agent.repos import plan_repo, session_repo
from agent.tools import session_setup as tools


def contexto(*plans):
    return SessionSetupContext(plans=list(plans), history="")


PERNA = PlanInfo(id=7, name="Perna")


@pytest.mark.parametrize(
    ("valor", "encontra"),
    [("plan:7", True), ("plan:8", False), ("ex:7", False), ("plan:x", False), ("", False), (None, False)],
)
def test_find_plan_so_aceita_planos_do_usuario(valor, encontra):
    assert (contexto(PERNA).find_plan(valor) is PERNA) is encontra


def test_contexto_lista_so_planos_ativos_do_proprio_usuario(db, user, make_user, make_plan):
    ativo = make_plan("Perna")
    arquivado = make_plan("Antigo")
    plan_repo.archive_plan(db, arquivado)
    make_plan("Do outro", owner=make_user())

    ctx = build_context(db, user)

    assert [p.name for p in ctx.plans] == ["Perna"]
    assert ctx.plan_ids() == [f"plan:{ativo.id}"]


# ---------------------------------------------------------------- ferramentas

def test_treino_livre_abre_sessao_sem_plano(db, user):
    r = tools.start_free_session(db, user, build_context(db, user))

    ws = session_repo.get_open_session(db, user)
    assert r == {"ok": True, "next_state": "session", "mode": "free"}
    assert ws.plan_id is None
    assert user.state_data == {"session_id": ws.id}


def test_treino_com_plano_abre_sessao_do_plano(db, user, make_plan):
    plano = make_plan("Perna")

    r = tools.start_session_with_plan(db, user, build_context(db, user), f"plan:{plano.id}")

    assert r == {"ok": True, "next_state": "session", "plan_name": "Perna"}
    assert session_repo.get_open_session(db, user).plan_id == plano.id


def test_plano_invalido_nao_abre_sessao(db, user, make_plan):
    make_plan("Perna")

    r = tools.start_session_with_plan(db, user, build_context(db, user), "plan:999")

    assert r["ok"] is False and "plan_id inválido" in r["error"]
    assert session_repo.get_open_session(db, user) is None


def test_plano_de_outro_usuario_nao_abre(db, user, make_user, make_plan):
    dele = make_plan("Do outro", owner=make_user())

    r = tools.start_session_with_plan(db, user, build_context(db, user), f"plan:{dele.id}")

    assert r["ok"] is False


def test_comecar_treino_abandona_o_anterior_aberto(db, user, start_workout):
    largado = start_workout()

    tools.start_free_session(db, user, build_context(db, user))

    assert largado.ended_reason == "abandoned"


@pytest.mark.parametrize(("ferramenta", "estado"), [("go_back", "idle"), ("go_to_planning", "planning")])
def test_ferramentas_de_navegacao(db, user, ferramenta, estado):
    assert tools.TOOL_IMPLS[ferramenta](db, user, build_context(db, user)) == {"ok": True, "next_state": estado}


# ---------------------------------------------------------------- o que é oferecido à LLM

def test_sem_planos_nao_oferece_comecar_com_plano(db, user):
    nomes = [d.name for d in tools.build_declarations(build_context(db, user))]
    assert nomes == ["start_free_session", "go_back", "go_to_planning"]


def test_com_planos_oferece_so_os_ids_dos_planos_dele(db, user, make_plan):
    make_plan("Perna")
    make_plan("Peito")
    ctx = build_context(db, user)

    decls = {d.name: d for d in tools.build_declarations(ctx)}

    assert decls["start_session_with_plan"].parameters.properties["plan_id"].enum == ctx.plan_ids()
