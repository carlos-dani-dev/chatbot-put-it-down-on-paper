"""RLS (Row Level Security) — nenhuma tabela pode ficar aberta pra API pública do Supabase.

Roda as migrações de verdade num banco Postgres novo e descartável que imita o Supabase: com os
papéis da API (`anon`, `authenticated`) e com a permissão padrão que o Supabase dá a eles em toda
tabela nova. Depois confere que toda tabela do app tem RLS e que a API não lê nada.

Se este teste falhar porque você criou uma tabela: na migração dela, depois do create_table, ponha
    op.execute('ALTER TABLE "<nome>" ENABLE ROW LEVEL SECURITY')
(dentro de um `if op.get_bind().dialect.name == "postgresql"`, como na e9a3c6d04b51).

Precisa de um Postgres descartável (TEST_POSTGRES_URL), como os testes de concorrência — no GitHub
Actions já existe um. Cria e apaga o próprio banco, sem tocar no banco do TEST_POSTGRES_URL.
"""
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from agent.models import Base

PG_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL não definida (precisa de um Postgres de teste)")

API_ROLES = ("anon", "authenticated")


@pytest.fixture(scope="module")
def migrado(tmp_path_factory):
    """URL de um banco novo, preparado como o Supabase e com todas as migrações aplicadas."""
    if "supabase" in PG_URL:
        pytest.fail("TEST_POSTGRES_URL aponta pro Supabase — este teste cria e apaga bancos. Use um Postgres descartável.")
    server = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"rls_teste_{uuid.uuid4().hex[:8]}"
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
        for role in API_ROLES:  # papéis valem pro servidor todo; criados uma vez só
            conn.execute(text(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)

    db = create_engine(url)
    with db.begin() as conn:
        # o que o Supabase faz: toda tabela nova no public já nasce liberada pra API
        conn.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated"))

    old = os.environ.get("ALEMBIC_DB_URL")
    os.environ["ALEMBIC_DB_URL"] = url  # o alembic/env.py lê daqui
    try:
        # sem o alembic.ini: ele reconfigura o logging e desligaria os logs que outros testes conferem
        config = Config()
        config.set_main_option("script_location", "alembic")
        command.upgrade(config, "head")
    finally:
        if old is None:
            os.environ.pop("ALEMBIC_DB_URL")
        else:
            os.environ["ALEMBIC_DB_URL"] = old

    yield db
    db.dispose()
    with server.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    server.dispose()


def test_toda_tabela_do_app_tem_rls(migrado):
    with migrado.connect() as conn:
        rls = dict(conn.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
        )).all())

    esperadas = set(Base.metadata.tables) | {"alembic_version"}
    assert esperadas <= set(rls), "alguma tabela dos models não foi criada pelas migrações"
    sem_rls = sorted(t for t in esperadas if not rls[t])
    assert sem_rls == [], f"tabelas sem RLS (veja o topo deste arquivo): {sem_rls}"


@pytest.mark.parametrize("role", API_ROLES)
def test_api_do_supabase_nao_le_nada(migrado, role):
    with migrado.connect() as conn:
        sem_permissao = conn.execute(text(
            "SELECT bool_and(NOT has_table_privilege(:role, c.oid, 'SELECT')) FROM pg_class c "
            "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'"
        ), {"role": role}).scalar()
    assert sem_permissao is True


def test_tabela_criada_depois_tambem_nasce_fechada(migrado):
    """As permissões padrão também foram tiradas: uma tabela nova não volta a abrir pra API."""
    with migrado.begin() as conn:
        conn.execute(text("CREATE TABLE tabela_do_futuro (id int)"))
        aberta = conn.execute(text("SELECT has_table_privilege('anon', 'tabela_do_futuro', 'SELECT')")).scalar()
        conn.execute(text("DROP TABLE tabela_do_futuro"))
    assert aberta is False


def test_dono_das_tabelas_continua_lendo_e_gravando(migrado):
    """O bot conecta como dono das tabelas: a RLS sem policy não pode atrapalhar ele."""
    with migrado.begin() as conn:
        conn.execute(text("INSERT INTO users (phone_number) VALUES ('5586999990000')"))
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
        conn.execute(text("DELETE FROM users"))


def test_mesmo_com_permissao_a_rls_esconde_as_linhas(migrado):
    """A segunda tranca: se alguém devolver a permissão da API por engano, a RLS ainda não mostra nada."""
    with migrado.begin() as conn:
        conn.execute(text("INSERT INTO users (phone_number) VALUES ('5586999990000')"))
        conn.execute(text("GRANT SELECT ON users TO anon"))
        conn.execute(text("SET LOCAL ROLE anon"))
        visiveis = conn.execute(text("SELECT count(*) FROM users")).scalar()
        conn.execute(text("RESET ROLE"))
        conn.execute(text("REVOKE SELECT ON users FROM anon"))
        conn.execute(text("DELETE FROM users"))
    assert visiveis == 0
