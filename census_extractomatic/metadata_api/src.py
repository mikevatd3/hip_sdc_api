from flask import Blueprint

metadata_api = Blueprint('metadata_api', __name__)

from .connection import MetadataSession
from .query import build_table_metadata


def get_db():
    return MetadataSession()

# Abstraction boundaries
#   -- ABCs, protocols
#
# Package boundaries
#   -- fix & publish d3_metadata
#       -- used in aggregation pipeline
#       -- used in metadata interface management
#       -- used in api to deliver metadata
# System boundaries

metadata_api.get("/tables/<tables>")
def return_table_metadata(tables: str):
    table_names = tables.split(",")

    db = get_db()
    metadata = build_table_metadata(table_names, db)

    return metadata.model_dump_json()
