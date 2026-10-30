from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

USER_STATES = ("onboarding", "idle", "planning", "session_setup", "session")


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Usuário e estado da conversa
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="onboarding", server_default="onboarding")
    state_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    plans: Mapped[list["Plan"]] = relationship("Plan", back_populates="user")
    workout_sessions: Mapped[list["WorkoutSession"]] = relationship("WorkoutSession", back_populates="user")


# --------------------------------------------------------------------------
# Log das mensagens trocadas (contexto para o LLM, debug e idempotência)
# Não faz parte do schema de treino: pode ser simplificado ou removido.
# --------------------------------------------------------------------------
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    wa_message_id: Mapped[str | None] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="chat_messages")


# --------------------------------------------------------------------------
# Catálogo de exercícios
# --------------------------------------------------------------------------
class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    muscle_group: Mapped[str | None] = mapped_column(String)  # músculo principal
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    aliases: Mapped[list["ExerciseAlias"]] = relationship("ExerciseAlias", back_populates="exercise", cascade="all, delete-orphan", passive_deletes=True)


class ExerciseAlias(Base):
    __tablename__ = "exercise_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))  # NULL = global
    alias: Mapped[str] = mapped_column(String, nullable=False)

    exercise: Mapped["Exercise"] = relationship("Exercise", back_populates="aliases")


# --------------------------------------------------------------------------
# Planos (o treino do dia: "Peito", "Costas"...)
# --------------------------------------------------------------------------
class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = ativo

    user: Mapped["User"] = relationship("User", back_populates="plans")
    exercises: Mapped[list["PlanExercise"]] = relationship(
        "PlanExercise",
        back_populates="plan",
        order_by="PlanExercise.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PlanExercise(Base):
    __tablename__ = "plan_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercises.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    target_sets: Mapped[int] = mapped_column(Integer, nullable=False)

    plan: Mapped["Plan"] = relationship("Plan", back_populates="exercises")
    exercise: Mapped["Exercise"] = relationship("Exercise")


# --------------------------------------------------------------------------
# Sessão de treino (ida à academia). plan_id NULL = treino livre.
# Tabela "sessions"; a classe se chama WorkoutSession para não confundir
# com sessão de chat.
# --------------------------------------------------------------------------
class WorkoutSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("plans.id"))
    current_exercise_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("exercises.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(String)

    user: Mapped["User"] = relationship("User", back_populates="workout_sessions")
    plan: Mapped["Plan | None"] = relationship("Plan")
    current_exercise: Mapped["Exercise | None"] = relationship("Exercise")
    sets: Mapped[list["WorkoutSet"]] = relationship(
        "WorkoutSet", back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


# --------------------------------------------------------------------------
# Séries registradas. plan_exercise_id NULL = treino livre ou exercício extra.
# --------------------------------------------------------------------------
class WorkoutSet(Base):
    __tablename__ = "sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercises.id"), nullable=False)
    plan_exercise_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("plan_exercises.id", ondelete="SET NULL"))
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    rest_seconds: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str | None] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    session: Mapped["WorkoutSession"] = relationship("WorkoutSession", back_populates="sets")
    exercise: Mapped["Exercise"] = relationship("Exercise")

# --------------------------------------------------------------------------
# Cobranças Pix do plano Pro. Só o webhook de pagamento muda status/paid_at;
# o pedido no chat apenas cria a cobrança pendente.
# --------------------------------------------------------------------------
class PixCharge(Base):
    __tablename__ = "pix_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending | approved | expired | cancelled
    qr_code: Mapped[str] = mapped_column(Text, nullable=False)  # Pix copia e cola
    ticket_url: Mapped[str | None] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User")
