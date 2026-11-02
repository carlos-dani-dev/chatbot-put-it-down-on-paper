"""Página de resultados (agent/dashboard.py + agent/templates/dashboard.html): a aba Ficha e a
página montada com as duas abas.

O template é renderizado direto pelo Jinja, sem subir o app (importar agent.app liga o Sentry).
"""
from datetime import datetime, timezone

import pytest
from fastapi.templating import Jinja2Templates

from agent.dashboard import build_dashboard_context
from agent.models import Exercise

templates = Jinja2Templates(directory="agent/templates")


def render(context: dict) -> str:
    return templates.get_template("dashboard.html").render(**context)


@pytest.fixture
def com_grupos(db):
    """Dá músculo aos exercícios criados pelo make_plan (ele cria sem)."""
    def _set(**grupos):
        for ex in db.query(Exercise).filter(Exercise.name.in_(grupos)).all():
            ex.muscle_group = grupos[ex.name]
        db.flush()
    return _set


# ---------------------------------------------------------------- ficha

def test_ficha_lista_os_planos_com_os_exercicios_na_ordem(db, user, make_plan, com_grupos):
    make_plan("Peito", [("Supino reto", 4), ("Crucifixo", 3)])
    make_plan("Perna", [("Agachamento livre", 5)])
    com_grupos(**{"Supino reto": "Peitoral"})

    ficha = build_dashboard_context(db, user)["ficha"]

    assert ficha == [
        {"name": "Peito", "total_sets": 7, "exercises": [
            {"name": "Supino reto", "muscle_group": "Peitoral", "target_sets": 4},
            {"name": "Crucifixo", "muscle_group": None, "target_sets": 3},
        ]},
        {"name": "Perna", "total_sets": 5, "exercises": [
            {"name": "Agachamento livre", "muscle_group": None, "target_sets": 5},
        ]},
    ]


def test_ficha_nao_mostra_plano_excluido(db, user, make_plan):
    make_plan("Peito", [("Supino reto", 4)])
    antigo = make_plan("Costas", [("Remada curvada", 3)])
    antigo.archived_at = datetime.now(timezone.utc)
    db.flush()

    assert [p["name"] for p in build_dashboard_context(db, user)["ficha"]] == ["Peito"]


def test_ficha_aparece_mesmo_sem_nenhum_treino(db, user, make_plan):
    """Quem só montou o plano ainda não tem resultados, mas já tem ficha."""
    make_plan("Peito", [("Supino reto", 4)])

    context = build_dashboard_context(db, user)

    assert context["has_data"] is False
    assert [p["name"] for p in context["ficha"]] == ["Peito"]


def test_ficha_so_mostra_os_planos_do_proprio_usuario(db, make_user, make_plan):
    dono, outro = make_user(), make_user()
    make_plan("Peito", [("Supino reto", 4)], owner=dono)

    assert build_dashboard_context(db, outro)["ficha"] == []
    assert len(build_dashboard_context(db, dono)["ficha"]) == 1


# ---------------------------------------------------------------- página

def test_menu_tem_so_home_e_ficha(db, user, make_plan):
    html = render(build_dashboard_context(db, user))

    menu = html.split('<nav class="drawer"')[1].split("</nav>")[0]
    assert menu.count("<a ") == 2
    assert 'href="#home"' in menu and 'href="#ficha"' in menu


def test_pagina_com_treinos_tem_todos_os_resultados_e_a_ficha(db, user, make_plan, start_workout, log):
    plano = make_plan("Peito", [("Supino reto", 4), ("Crucifixo", 3)])
    ws = start_workout(plano)
    log(ws, "Supino reto", (80, 10), (80, 8))

    html = render(build_dashboard_context(db, user))

    # os resultados de antes continuam todos lá
    for secao in ('id="recente"', 'id="calendario"', 'id="progresso"', 'id="ranking"'):
        assert secao in html
    # e a ficha, com os dois exercícios do plano
    ficha = html.split('id="view-ficha"')[1]
    assert "Supino reto" in ficha and "Crucifixo" in ficha and "7 séries no total" in ficha


def test_ficha_vazia_sugere_criar_um_plano(db, user):
    html = render(build_dashboard_context(db, user))

    ficha = html.split('id="view-ficha"')[1]
    assert "Você ainda não tem planos cadastrados" in ficha


def test_nomes_vindos_do_usuario_sao_escapados(db, user, make_plan):
    make_plan("<script>alert(1)</script>", [("Supino reto", 4)])

    html = render(build_dashboard_context(db, user))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
