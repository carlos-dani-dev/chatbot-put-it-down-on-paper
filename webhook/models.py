import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    phone_number_id: Mapped[str]=mapped_column(String, nullable=False, primary_key=True)
    phone_number: Mapped[str]=mapped_column(String, nullable=False)

    session: Mapped["WAPChatSession"]=relationship(
        "WAPChatSession", back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )


class WAPChatSession(Base):  # only one for each user
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID]=mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_phone_number_id: Mapped[str]=mapped_column(
        String, ForeignKey("users.phone_number_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    created_at: Mapped[datetime]=mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    reset_at: Mapped[datetime]=mapped_column(DateTime, nullable=False)
    session_history: Mapped[dict[str, Any]]=mapped_column(JSON)

    user: Mapped["User"]=relationship("User", back_populates="session")
    messages: Mapped[list["WAPChatMessage"]]=relationship(
        "WAPChatMessage", back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class WAPChatMessage(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID]=mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID]=mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str]=mapped_column(String, nullable=False)
    content: Mapped[str]=mapped_column(String, nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    session: Mapped["WAPChatSession"]=relationship("WAPChatSession", back_populates="messages")
