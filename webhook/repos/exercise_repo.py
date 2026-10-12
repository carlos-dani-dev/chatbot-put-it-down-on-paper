from typing import Any

from sqlalchemy import func

from ..models import Exercise


def find_by_name(db: Any, name: str) -> Exercise | None:
    return db.query(Exercise).filter(func.lower(Exercise.name) == name.lower()).first()


def create(db: Any, name: str) -> Exercise:
    exercise = Exercise(name=name)
    db.add(exercise)
    db.flush()
    return exercise


def find_or_create(db: Any, name: str) -> tuple[Exercise, bool]:
    exercise = find_by_name(db, name)
    if exercise is not None:
        return exercise, False
    return create(db, name), True
