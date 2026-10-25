from sqlalchemy.orm import Session

from ..models import ChatMessage, User


def get_last_messages(db: Session, user: User, limit: int = 2) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter_by(user_id=user.id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )


def log_message(db: Session, user: User, role: str, content: str, wa_message_id: str | None = None) -> ChatMessage:
    if wa_message_id is not None:
        existing = db.query(ChatMessage).filter_by(wa_message_id=wa_message_id).first()
        if existing is not None:
            return existing

    message = ChatMessage(user_id=user.id, role=role, content=content, wa_message_id=wa_message_id)
    db.add(message)
    db.flush()
    return message
