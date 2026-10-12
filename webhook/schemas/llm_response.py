from typing import Literal
from pydantic import BaseModel


class ParsedSet(BaseModel):
    weight_kg: float | None = None     # None = não informou; 0 = peso corporal
    reps: int
    rest_seconds: int | None = None
 
 
class ParsedEntry(BaseModel):
    exercise: str | None = None        # None = o exercício atual
    sets: list[ParsedSet]
 
 
class ParsedSessionMessage(BaseModel):
    intent: Literal["log_sets", "unknown"]
    entries: list[ParsedEntry] = []


class ParsedExercise(BaseModel):
    name: str
    target_sets: int


class ParsedExercises(BaseModel):
    items: list[ParsedExercise]


class ParsedPlan(BaseModel):
    name: str
    items: list[ParsedExercise]