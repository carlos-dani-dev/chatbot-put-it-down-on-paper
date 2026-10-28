from ..models import User
from sqlalchemy.orm import Session

def get_or_create_user(db: Session, from_number: str) -> User:
    user = db.query(User).filter_by(phone_number=from_number).first()

    if user is None:
        user = User(phone_number=from_number)
        db.add(user)
        db.flush()

    return user

