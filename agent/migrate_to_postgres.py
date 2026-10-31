"""Copia todos os dados do SQLite (app.db) para o PostgreSQL (Supabase), uma única vez.

- Origem: SOURCE_DB_URL (padrão: sqlite:///./app.db)
- Destino: SUPABASE_DB_URL do agent/.env (ou TARGET_DB_URL, se definido)

Tudo roda numa transação só no destino: ou copia tudo, ou nada. O destino precisa já ter as
tabelas (`alembic upgrade head`) e estar vazio. Depois de copiar, reajusta as sequências de
ID do Postgres, senão o próximo INSERT tentaria reutilizar o id 1.

Rodar da raiz do projeto, com o bot parado (pra ninguém gravar no SQLite durante a cópia):
    poetry run python -m agent.migrate_to_postgres --dry-run   # ensaio: copia e desfaz
    poetry run python -m agent.migrate_to_postgres             # copia de verdade
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, func, select, text

from agent.models import Base

ENV_FILE = Path(__file__).resolve().parent / ".env"
CHUNK = 1000


def _target_url() -> str:
    url = os.environ.get("TARGET_DB_URL") or dotenv_values(ENV_FILE).get("SUPABASE_DB_URL")
    if not url or not url.startswith("postgresql"):
        sys.exit("Destino não configurado: defina SUPABASE_DB_URL no agent/.env (postgresql+psycopg2://...).")
    return url


def _as_utc(row: dict) -> dict:
    # o SQLite descarta o fuso e o projeto grava tudo em UTC: devolve o fuso antes de ir pro timestamptz
    return {
        k: v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v
        for k, v in row.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="copia, confere e desfaz tudo no final")
    args = parser.parse_args()

    source = create_engine(os.environ.get("SOURCE_DB_URL", "sqlite:///./app.db"))
    target = create_engine(_target_url())
    if source.dialect.name != "sqlite":
        sys.exit(f"A origem deveria ser SQLite, mas é {source.dialect.name}.")

    tables = Base.metadata.sorted_tables  # pais antes dos filhos (ordem das chaves estrangeiras)

    with source.connect() as src, target.connect() as dst:
        tx = dst.begin()
        try:
            version = dst.execute(text("select version_num from alembic_version")).scalar()
            src_version = src.execute(text("select version_num from alembic_version")).scalar()
            if version != src_version:
                sys.exit(f"Versões do Alembic diferentes: origem={src_version} destino={version}. "
                         "Rode `alembic upgrade head` no destino.")
            not_empty = [t.name for t in tables if dst.execute(select(func.count()).select_from(t)).scalar()]
            if not_empty:
                sys.exit(f"O destino já tem dados em: {not_empty}. A cópia só roda num banco vazio.")

            print(f"{'tabela':<18}{'origem':>8}{'destino':>9}")
            for table in tables:
                rows = [_as_utc(dict(r._mapping)) for r in src.execute(select(table)).all()]
                for i in range(0, len(rows), CHUNK):
                    dst.execute(table.insert(), rows[i:i + CHUNK])

                if "id" in table.c:
                    dst.execute(text(
                        f"select setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"coalesce(max(id), 1), max(id) is not null) from {table.name}"
                    ))

                copied = dst.execute(select(func.count()).select_from(table)).scalar()
                print(f"{table.name:<18}{len(rows):>8}{copied:>9}")
                if copied != len(rows):
                    raise RuntimeError(f"Contagem não bate em {table.name}: {len(rows)} x {copied}")
        except BaseException:
            tx.rollback()
            raise

        if args.dry_run:
            tx.rollback()
            print("\nEnsaio ok: tudo foi copiado e desfeito. Nada ficou gravado no destino.")
        else:
            tx.commit()
            print("\nCópia concluída e gravada no destino.")


if __name__ == "__main__":
    main()
