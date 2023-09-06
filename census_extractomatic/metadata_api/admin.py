import secrets

from sqlalchemy import inspect
from flask import redirect, url_for, request
from flask_admin import Admin
from flask_login import current_user
from flask_admin.contrib.sqla import ModelView

from .models import D3EditionMetadata, D3TableMetadata, D3VariableMetadata, D3VariableGroup
from .connection import MetadataSession


db = MetadataSession()


def make_view(table_metadata_class):
    class VerboseView(ModelView):
        column_display_pk = True # optional, but I like to see the IDs in the list
        column_hide_backrefs = False
        column_list = [c_attr.key for c_attr in inspect(table_metadata_class).mapper.column_attrs]

        def is_accessible(self):
            return current_user.is_authenticated

        def inaccessible_callback(self, name, **kwargs):
            # redirect to login page if user doesn't have access
            return redirect(url_for('login', next=request.url))

    return VerboseView


class TableView(ModelView):
    inline_models = (D3VariableMetadata, D3EditionMetadata, D3VariableGroup)
    column_display_pk = True # optional, but I like to see the IDs in the list
    column_hide_backrefs = False
    column_list = [c_attr.key for c_attr in inspect(D3TableMetadata).mapper.column_attrs]

    def is_accessible(self):
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        # redirect to login page if user doesn't have access
        return redirect(url_for('login', next=request.url))


VariableView = make_view(D3VariableMetadata)
EditionView = make_view(D3EditionMetadata)


def register_d3_metadata_admin(app):
    # set optional bootswatch theme
    app.config['FLASK_ADMIN_SWATCH'] = 'slate'
    secret_key = secrets.token_hex(16)
    app.config['SECRET_KEY'] = secret_key

    admin = Admin(app, name='D3 Data Pipeline', template_mode='bootstrap3')
    admin.add_view(TableView(D3TableMetadata, db))
    admin.add_view(VariableView(D3VariableMetadata, db))
    admin.add_view(EditionView(D3EditionMetadata, db))
