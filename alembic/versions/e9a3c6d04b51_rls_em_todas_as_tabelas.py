"""RLS em todas as tabelas

Liga a Row Level Security em todas as tabelas, sem nenhuma policy: só o dono das tabelas (o
usuário `postgres`, com que o bot, o deploy e o painel conectam) passa. Qualquer outro acesso —
em especial a API pública do Supabase (papéis `anon` e `authenticated`) — não enxerga linha
nenhuma. E tira desses papéis as permissões nas tabelas, inclusive nas que forem criadas depois.

Só faz algo no Postgres: o SQLite (testes e banco local) não tem RLS nem esses papéis. Tabela
nova precisa de `enable row level security` na própria migração — o tests/test_rls.py avisa.

Revision ID: e9a3c6d04b51
Revises: b4d81e5c2a17
Create Date: 2026-11-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e9a3c6d04b51'
down_revision: Union[str, Sequence[str], None] = 'b4d81e5c2a17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "users", "chat_messages", "exercises", "exercise_aliases", "plans", "plan_exercises", "sessions",
    "sets", "pix_charges", "coupons", "coupon_redemptions", "llm_calls", "alembic_version",
]

# papéis da API do Supabase; num Postgres comum eles não existem e esta parte é pulada
_REVOKE_API_ROLES = """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
    END IF;
  END LOOP;
END $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(_REVOKE_API_ROLES)


def downgrade() -> None:
    """Downgrade schema.

    Só desliga a RLS. As permissões da API NÃO são devolvidas de propósito: o bot não usa a API,
    e devolver reabriria as tabelas pra quem tiver a chave anon.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
