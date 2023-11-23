from sqlalchemy import text
# from returns.result import Failure, Result
# Eventually wrap all db calls with this


PARENT_CHILD_CONTAINMENT = {
    "040": [
        "050",
        "060",
        "101",
        "140",
        "150",
        "500",
        "610",
        "620",
        "950",
        "960",
        "970",
    ],
    "050": ["060", "101", "140", "150"],
    "140": ["101", "150"],
    "150": ["101"],
}

UNOFFICIAL_CHILDREN = {
    "160": ["140", "150"],
    "310": ["160", "860"],
    "040": ["310", "860"],
    "050": [
        "160",
        "860",
        "950",
        "960",
        "970",
    ],
}


def get_all_child_geoids(child_summary_level, db):
    result = db.session.execute(
        text(
            """SELECT geoid,name
           FROM geoheader
           WHERE sumlevel=:sumlev AND component='00' AND geoid NOT IN ('04000US72')
           ORDER BY name"""
        ),
        {"sumlev": int(child_summary_level)},
    )

    return result.fetchall()


def get_child_geoids_by_coverage(parent_geoid, child_summary_level, db):
    result = db.session.execute(
        text(
            """SELECT geoid, name
           FROM tiger2021.census_geo_containment, geoheader
           WHERE geoheader.geoid = census_geo_containment.child_geoid
             AND census_geo_containment.parent_geoid = :parent_geoid
             AND census_geo_containment.child_geoid LIKE :child_geoids
             AND census_geo_containment.percent_covered > 10"""
        ),
        {
            "parent_geoid": parent_geoid,
            "child_geoids": child_summary_level + "%",
        },
    )

    rowdicts = []
    seen_geoids = set()
    for row in result:
        if not row["geoid"] in seen_geoids:
            rowdicts.append(row)
            seen_geoids.add(row["geoid"])

    return rowdicts


def get_child_geoids_by_gis(parent_geoid, child_summary_level, db):
    child_geoids = []
    result = db.session.execute(
        text(
            """
            SELECT child_geoid as full_geoid
            FROM tiger2021.census_geo_containment parent
            WHERE parent_geoid = :parent_geoid
            AND child_geoid LIKE :child_sumlevel
            AND percent_covered > 10;
         """
        ),
        {
            "child_sumlevel": child_summary_level + "%",
            "parent_geoid": parent_geoid,
        },
    )
    child_geoids = [r.full_geoid for r in result]

    if child_geoids:
        # Use the "worst"/biggest ACS to find all child geoids
        result = db.session.execute(
            text(
                """SELECT geoid,name
               FROM geoheader
               WHERE geoid IN :child_geoids
               ORDER BY name"""
            ),
            {"child_geoids": tuple(child_geoids)},
        )
        return result.fetchall()
    else:
        return []


def get_child_geoids_by_prefix(parent_geoid, child_summary_level, db):
    short_geoid = parent_geoid.upper().split("US")[1],
    child_geoid_prefix = f"{child_summary_level}00US{short_geoid}%%"

    # Use the "worst"/biggest ACS to find all child geoids
    result = db.session.execute(
        text(
            """SELECT geoid,name
           FROM geoheader
           WHERE geoid LIKE :geoid_prefix
             AND name NOT LIKE :not_name
           ORDER BY geoid"""
        ),
        {"geoid_prefix": child_geoid_prefix, "not_name": "%%not defined%%"},
    )
    return result.fetchall()


# "acs2021_5yr",
def get_child_geoids(release, parent_geoid, child_summary_level, db):
    parent_sumlevel = parent_geoid[0:3]

    db.session.execute(
        text("SET search_path TO :acs,public;"), {"acs": release}
    )

    if parent_sumlevel == "010":
        return get_all_child_geoids(child_summary_level, db)

    if (
        parent_sumlevel in PARENT_CHILD_CONTAINMENT
        and child_summary_level in PARENT_CHILD_CONTAINMENT[parent_sumlevel]
    ):
        return get_child_geoids_by_prefix(parent_geoid, child_summary_level, db)

    if (
        parent_sumlevel in UNOFFICIAL_CHILDREN
        and child_summary_level in UNOFFICIAL_CHILDREN[parent_sumlevel]
    ):
        return get_child_geoids_by_coverage(
            parent_geoid, child_summary_level, db
        )
    return get_child_geoids_by_gis(parent_geoid, child_summary_level, db)
