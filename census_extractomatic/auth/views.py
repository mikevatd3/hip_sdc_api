from flask_login import current_user, login_user, logout_user
from flask import render_template, redirect, url_for, request
from sqlalchemy import select
from werkzeug.urls import url_parse
from . import auth
from .forms import LoginForm
from .models import User
from ..metadata_api.connection import PublicSession

@auth.route('/login', methods=['GET', 'POST'])
def login():
    nologin = False
    if current_user.is_authenticated:
        return redirect("/metadata")
    form = LoginForm()
    if form.validate_on_submit():
        with PublicSession() as db:
            stmt = select(User).where(User.email==form.email.data.lower())
            user = db.scalars(stmt).first()

        if user is None or not user.check_password(form.password.data):
            nologin = True
        else:
            login_user(user, remember=form.remember_me.data)
            return redirect("/admin")
    return render_template('auth/login.html', title='Sign In', form=form, message=nologin)

@auth.route('/logout')
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
