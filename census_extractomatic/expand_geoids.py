from typing import Any
from sqlalchemy import text
from .collect_children import get_child_geoids

class ShowDataException(Exception):
    pass


def find_explicit_geoids(explicit_geoids, db):
    db.session.execute(
        text("SET search_path TO :acs,public;"), {"acs": release}
    )
    try:
        result = db.session.execute(
            text(
                """SELECT geoid
               FROM acs2021_5yr.geoheader
               WHERE geoid IN :geoids;"""
            ),
            {"geoids": tuple(explicit_geoids)},
        )

    except Exception as e:
        print(e)





def expand_geoids(geoid_list, release, db) -> tuple[set[str], Any]:
    # Look for geoid "groups" of the form `child_sumlevel|parent_geoid`.
    # These will expand into a list of geoids like the old comparison endpoint used to
    expanded_geoids = []
    explicit_geoids = []
    child_parent_map = {}
    for geoid_str in geoid_list:
        geoid_split = geoid_str.split("|")
        if len(geoid_split) == 2 and len(geoid_split[0]) == 3:
            (child_summary_level, parent_geoid) = geoid_split
            child_geoid_list = [
                child_geoid.geoid
                for child_geoid in get_child_geoids(
                    release, parent_geoid, child_summary_level, db
                )
            ]
            expanded_geoids.extend(child_geoid_list)
            for child_geoid in child_geoid_list:
                child_parent_map[child_geoid] = parent_geoid
        else:
            explicit_geoids.append(geoid_str)

    # Since the expanded geoids were sourced from the database they don't need to be checked
    valid_geo_ids = []
    valid_geo_ids.extend(expanded_geoids)

    # Check to make sure the geo ids the user entered are valid
    if explicit_geoids:
        db.session.execute(
            text("SET search_path TO :acs,public;"), {"acs": release}
        )
        try:
            result = db.session.execute(
                text(
                    """SELECT geoid
                   FROM acs2021_5yr.geoheader
                   WHERE geoid IN :geoids;"""
                ),
                {"geoids": tuple(explicit_geoids)},
            )

        except Exception as e:
            print(e)

        valid_geo_ids.extend([geo[0] for geo in result])

    invalid_geo_ids = set(expanded_geoids + explicit_geoids) - set(
        valid_geo_ids
    )
    if invalid_geo_ids:
        raise ShowDataException(
            "The %s release doesn't include GeoID(s) %s."
            % (get_acs_name(release), ",".join(invalid_geo_ids))
        )

    return set(valid_geo_ids), child_parent_map
