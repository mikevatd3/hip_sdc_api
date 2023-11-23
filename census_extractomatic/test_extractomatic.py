from collections import namedtuple
from typing import Any
import logging
from unittest.mock import patch

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.sql.elements import TextClause
from returns.result import Success, Failure

from .download_specified_data import (
    build_fetch_query,
    grab_remaining_geoid_info,
    check_table_requests,
    fetch_data,
    drop_whitespace,
)


logger = logging.getLogger()


@pytest.fixture
def MockSession():
    GeoidResult = namedtuple(
        "GeoidResult", "full_geoid population display_name"
    )
    TableResult = namedtuple(
        "TableResult",
        "table_id table_title universe denominator_column_id column_id column_title indent",
    )
    DataResult = namedtuple(
        "DataResult", "geoid b01995001 b01995001_moe b01979001 b01979001_moe"
    )

    class DBResult:
        def __init__(self, items: list[Any]):
            self.items = items

        def count(self):
            return len(self.items)

        def __iter__(self):
            return iter(self.items)

    class MockDB:
        class Session:
            def __init__(
                self,
                execute_result,
                fail_args: set[tuple[str, ...]] = set(),
                empty_args: set[tuple[str, ...]] = set(),
            ):
                self.execute_result = execute_result
                self.fail_args = fail_args
                self.empty_args = empty_args

            def execute(self, _: TextClause, args: dict) -> list:
                if any(val in self.fail_args for val in args.values()):
                    raise ProgrammingError(
                        "There was an error in the query.", {}, Exception()
                    )
                if any(val in self.empty_args for val in args.values()):
                    return DBResult([])

                return self.execute_result

        def __init__(self, func_name) -> None:
            if func_name == "grab_remaining_geoid_info":
                self.session = self.Session(
                    DBResult(
                        [
                            GeoidResult(
                                "14000US26163511400",
                                120_120,
                                "Great census tract",
                            ),
                        ]
                    ),
                    fail_args={("86000US48202",)},
                    empty_args={("04000US26",)},
                )
            elif func_name == "check_table_requests":
                self.session = self.Session(
                    DBResult(
                        [
                            TableResult(
                                "B01995",
                                "Some important table",
                                "Schools",
                                "B01995001",
                                "B01995001",
                                "Total schools",
                                0,
                            ),
                            TableResult(
                                "B01995",
                                "Some important table",
                                "Schools",
                                "B01995001",
                                "B01995002",
                                "Old schools",
                                1,
                            ),
                            TableResult(
                                "B01995",
                                "Some important table",
                                "Schools",
                                "B01995001",
                                "B01995003",
                                "New schools",
                                1,
                            ),
                        ]
                    ),
                    fail_args={("B01999",)},
                    empty_args={("B02000",)},
                )
            elif func_name == "fetch_data":
                self.session = self.Session(
                    DBResult(
                        [
                            DataResult(
                                "14000US26163511400",
                                0,
                                2,
                                5,
                                6,
                            ),
                            DataResult(
                                "16000US2616322000",
                                3,
                                0,
                                6,
                                3,
                            ),
                            DataResult(
                                "05000US26163",
                                1,
                                4,
                                8,
                                10,
                            ),
                        ]
                    ),
                    fail_args={("FAIL_GEOID",)},
                    empty_args={("EMPTY_GEOID",)},
                )

    return MockDB


def test_grab_remianing_geoid_info_success(MockSession):
    db = MockSession("grab_remaining_geoid_info")
    result = grab_remaining_geoid_info(("14000US26163511400",), db)

    assert isinstance(result, Success)


def test_grab_remianing_geoid_info_failure(MockSession):
    db = MockSession("grab_remaining_geoid_info")
    result = grab_remaining_geoid_info(("86000US48202",), db)

    assert isinstance(result, Failure)


def test_grab_remianing_geoid_info_empty(MockSession):
    db = MockSession("grab_remaining_geoid_info")
    result = grab_remaining_geoid_info(("04000US26",), db)

    assert isinstance(result, Failure)


def test_check_table_requests_success(MockSession):
    db = MockSession("check_table_requests")
    result = check_table_requests(("B01995",), db)

    assert isinstance(result, Success)


def test_check_table_requests_failure(MockSession):
    db = MockSession("check_table_requests")
    result = check_table_requests(("B01999",), db)

    assert isinstance(result, Failure)


def test_check_table_requests_empty(MockSession):
    db = MockSession("check_table_requests")
    result = check_table_requests(("B02000",), db)

    assert isinstance(result, Failure)


def test_fetch_data_query_build():
    query = build_fetch_query(["b01995", "b01999", "b02000"])

    logger.warning(query)

    expected = drop_whitespace(
        """
    SELECT * 
    FROM b01995_moe
    OUTER JOIN b01999_moe USING (geoid)
    OUTER JOIN b02000_moe USING (geoid)
    WHERE geoid IN :geo_ids;
    """
    )

    assert query == expected


def test_fetch_data_query_build_one_geoid():
    query = build_fetch_query(["b01995"])

    logger.warning(query)

    expected = drop_whitespace(
        """
    SELECT * 
    FROM b01995_moe
    WHERE geoid IN :geo_ids;
    """
    )

    for q, ex in zip(query.split("\n"), expected.split("\n")):
        assert q == ex


def test_fetch_build_data_sucess(MockSession):
    db = MockSession("fetch_data")
    result = fetch_data(
        ("b01995", "b01979"),
        ("14000US26163511400", "16000US2616322000", "05000US26163"),
        db
    )

    assert isinstance(result, Success)

    
def test_fetch_data_failure(MockSession):
    db = MockSession("fetch_data")
    result = fetch_data(
        ("B01999",),
        ("FAIL_GEOID",),
        db
    )

    assert isinstance(result, Failure)


def test_fetch_data_empty(MockSession):
    db = MockSession("fetch_data")
    result = fetch_data(
        ("B02000",),
        ("EMPTY_GEOID",),
        db
    )

    assert isinstance(result, Failure)


def test_group_tables():
    pass


def test_column_prep_loop():
    pass


def test_data_prep_loop():
    pass


def test_create_temp_file():
    pass

