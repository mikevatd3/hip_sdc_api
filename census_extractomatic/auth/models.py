from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import Mapped, MappedColumn, DeclarativeBase
from sqlalchemy import Integer, String, select
from flask_login import UserMixin
from ..api import login
from ..metadata_api.connection import public_engine, PublicSession

class Base(DeclarativeBase):
    pass


class User(UserMixin, Base):
    __tablename__ = 'users'
    id: Mapped[int] = MappedColumn(Integer, primary_key=True)
    username: Mapped[str] = MappedColumn(String(64), index=True, unique=True)
    email: Mapped[str] = MappedColumn(String(128), index=True, unique=True)
    password_hash: Mapped[str] = MappedColumn(String(128))

    def __repr__(self) -> str:
        return f'<User {self.username}'

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    

@login.user_loader
def load_user(id):
    with PublicSession() as db:
        stmt = select(User).where(User.id == int(id))
        return db.scalars(stmt).first()

Base.metadata.create_all(public_engine)
