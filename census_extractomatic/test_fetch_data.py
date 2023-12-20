import pytest
from icecream import ic
from returns.result import Success

from .metadata_api.connection import public_engine

from ._api.access import fetch_data


@pytest.fixture(scope="function")
def db_session():
    db = public_engine.connect()

    yield db

    db.close()


# These need to be tested in more detail
def test_simple_fetch(db_session):
    release = "acs2021_5yr"
    table_ids = ("B01001",)
    geoids = ("06000US2616322000",)

    result = fetch_data(table_ids, geoids, release, db_session)

    assert type(result) == Success


def test_multi_table_fetch(db_session):
    release = "acs2021_5yr"
    table_ids = ("B01001","B19001","B25001")
    geoids = ("06000US2616322000",)

    result = fetch_data(table_ids, geoids, release, db_session)

    assert type(result) == Success


def test_multi_geo_fetch(db_session):
    release = "acs2021_5yr"
    table_ids = ("B01001","B19001","B25001")
    geoids = ("06000US2616322000","05000US26163", "04000US26")

    result = fetch_data(table_ids, geoids, release, db_session)

    assert type(result) == Success
