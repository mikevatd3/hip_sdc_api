from sqlalchemy import text
import pytest

from .metadata_api.connection import public_engine
from ._api.access import (
    get_child_geoids,
    expand_expandable_geoids,
    get_child_geoids_by_gis,
)


@pytest.fixture(scope="function")
def db_session():
    db = public_engine.connect()

    yield db

    db.close()


def test_fixture(db_session):
    result = db_session.execute(
        text(
            """
            SELECT *
            FROM acs2021_5yr.b01001
            WHERE geoid = '14000US26163511400';
            """
        )
    )

    assert result.rowcount != 0


def test_get_all_children_gis(db_session):
    release = "acs2021_5yr"

    db_session.execute(
        text("SET search_path TO :acs,public;"), {"acs": release}
    )
    result = get_child_geoids_by_gis("06000US2616322000", "140", db_session)

    assert 275 < len(result) < 300


def test_get_all_child_geoids(db_session):
    release = "acs2021_5yr"

    result = get_child_geoids(release, "06000US2616322000", "140", db_session)

    assert 275 < len(result) < 300


def test_expand_expandables(db_session):
    result = expand_expandable_geoids(
        [("06000US2616322000", "140")], "acs2021_5yr", db_session
    )

    assert 275 < len(result[0]) < 300
