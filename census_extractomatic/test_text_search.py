import logging

from census_extractomatic.metadata_api.connection import public_engine
from ._api.access import search_geos_by_query, get_parent_geoids, get_details_for_geoids

logger = logging.getLogger()


def test_search_geods_by_query():
    with public_engine.connect() as db:
        result = search_geos_by_query("Detroit", db)
        assert result.fetchone().full_geoid == "06000US2616322000"


def test_get_parent_geoids():
    with public_engine.connect() as db:
        parents = get_parent_geoids("06000US2616322000", db)

        logger.warning(tuple(level["geoid"] for level in parents))
        
        assert len(parents) == 3


def test_get_geoid_info():
    with public_engine.connect() as db:
        details = get_details_for_geoids(
            ('06000US2616322000', '05000US26163'), db
        )

        assert len(details) == 2


# Ideas for data testing -- where are results from one query used in another?
# If not used in sql queries, what about quering dictionaries?
# Check entire path
