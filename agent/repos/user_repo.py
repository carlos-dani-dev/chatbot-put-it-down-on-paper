from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import User


def get_or_create_user(db: Session, from_number: str) -> User:
    user = db.query(User).filter_by(phone_number=from_number).first()

    if user is None:
        user = User(phone_number=from_number)
        db.add(user)
        db.flush()

    return user


def get_or_create_locked_user(db: Session, from_number: str) -> User:
    """Carrega o usuário TRAVANDO a linha dele até o fim da transação (SELECT ... FOR UPDATE).

    Duas mensagens da mesma pessoa chegando juntas viram uma fila: a segunda espera a primeira
    terminar e gravar, e só então lê o estado — já atualizado. Sem isso, as duas liam o mesmo
    estado ao mesmo tempo e uma sobrescrevia a outra (ex.: duas "série 3", rascunho de plano
    perdido). Mensagens de pessoas diferentes não se esperam: cada uma trava só a própria linha.
    A trava fica no banco, então vale também com vários processos ou máquinas.
    """
    # 1. garante que a linha existe sem corrida: se as duas primeiras mensagens de alguém novo
    #    chegarem juntas, uma cria e a outra não faz nada (em vez de quebrar com telefone duplicado)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        create = pg_insert(User).values(phone_number=from_number).on_conflict_do_nothing(index_elements=["phone_number"])
    elif dialect == "sqlite":
        create = sqlite_insert(User).values(phone_number=from_number).on_conflict_do_nothing(index_elements=["phone_number"])
    else:  # pragma: no cover — o projeto só usa Postgres e SQLite
        create = insert(User).values(phone_number=from_number)
    db.execute(create)

    # 2. lê travando. No SQLite (dev/testes) o FOR UPDATE é ignorado: ele já trava o banco
    #    inteiro na escrita. populate_existing: relê do banco mesmo que o objeto já esteja em memória.
    return db.scalars(
        select(User)
        .where(User.phone_number == from_number)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
