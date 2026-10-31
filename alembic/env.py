import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from agent.config import settings
from agent.models import Base

target_metadata = Base.metadata


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Banco alvo: o mesmo do app. ALEMBIC_DB_URL permite apontar só o Alembic pra outro banco
# (ex.: um SQLite vazio pra gerar a migração inicial, ou o Supabase) — o config.py carrega o
# .env com override=True, então trocar SQLALCHEMY_DB_URL na linha de comando não teria efeito.
DB_URL = os.environ.get("ALEMBIC_DB_URL") or settings.db_url

def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine
    connectable = create_engine(DB_URL)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # o SQLite não altera colunas: o Alembic recria a tabela por baixo
            compare_type=True,      # detecta mudança de tipo de coluna, não só coluna nova
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
