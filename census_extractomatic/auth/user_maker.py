from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash
from .models import User


def create_user(
        username: str, 
        email: str, 
        password: str, 
        pass_confirm: str, 
        db: Session
    ) -> None:
    if password == pass_confirm:
        db.add(User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
        ))
        db.commit()
    else:
        raise ValueError("Passwords didn't match")


