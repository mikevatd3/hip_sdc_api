from flask import Blueprint

from .connection import MetadataSession
from .query import build_table_metadata


metadata_api = Blueprint('metadata_api', __name__)


def get_db():
    return MetadataSession()


@metadata_api.get("/")
def index():
    return "This is returning stuff."


@metadata_api.get("/tables/<tables>")
def return_table_metadata(tables: str):
    table_names = tables.split(",")

    db = get_db()
    metadata = build_table_metadata(table_names, db)

    return metadata.model_dump_json()
