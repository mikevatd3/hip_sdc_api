from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash
import click

from .models import User
from ..metadata_api.connection import PublicSession


def create_user(
    username: str, email: str, password: str, pass_confirm: str, db: Session
) -> None:
    if password == pass_confirm:
        db.add(
            User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
            )
        )
        db.commit()
    else:
        raise ValueError("Passwords didn't match")


@click.command()
@click.argument("username")
@click.argument("password")
@click.argument("email")
@click.argument("pass_confirm")
def main(username, password, email, pass_confirm):
    with PublicSession() as db:
        create_user(username, email, password, pass_confirm, db)


if __name__ == "__main__":
    main()
