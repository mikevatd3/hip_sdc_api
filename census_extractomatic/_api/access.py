from dataclasses import dataclass, asdict
from collections import namedtuple
from itertools import groupby
from textwrap import dedent
import logging
import math
import os

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, OperationalError
from returns.result import Result, Success, Failure

from .reference import (
    PARENT_CHILD_CONTAINMENT,
    UNOFFICIAL_CHILDREN,
    SUMLEV_NAMES,
)

# from returns.result import Failure, Result
# Eventually wrap all db calls with this


logger = logging.getLogger()


def convert_row_to_dict(row):
    return {col: getattr(row, col) for col in row._fields}


def grab_remaining_geoid_info(geoids: tuple[str, ...], db) -> Result:
    try:
        result = db.session.execute(
            text(
                """SELECT full_geoid,
                  population,
                  display_name
           FROM tiger2021.census_name_lookup
           WHERE full_geoid IN :geo_ids;"""
            ),
            {"geo_ids": geoids},
        )

        if result.count() == 0:
            return Failure("Query returned no results.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def check_table_requests(table_ids: tuple[str, ...], db) -> Result:
    try:
        result = db.session.execute(
            text(
                """SELECT tab.table_id,
                  tab.table_title,
                  tab.universe,
                  tab.denominator_column_id,
                  col.column_id,
                  col.column_title,
                  col.indent
           FROM census_column_metadata col
           LEFT JOIN census_table_metadata tab USING (table_id)
           WHERE table_id IN :table_ids
           ORDER BY column_id;"""
            ),
            {"table_ids": table_ids},
        )

        if result.count() == 0:
            return Failure("Query returned no results.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def basic_col_builder(column):
    return {
        "column_title": column.column_title,
        "indent": column.indent,
        "parent_column_id": column.parent_column_id,
    }


def show_col_builder(column):
    return {
        "name": column.column_title,
        "indent": column.indent,
    }


def group_tables(
    table_rows, col_strategy=basic_col_builder, table_approach="full"
):
    """
    For the table query that returns all the column rows, nest the
    columns within the table.
    """

    @dataclass(frozen=True, eq=True)
    class Table:
        table_id: str
        table_title: str
        simple_table_title: str
        subject_area: str
        universe: str
        denominator_column_id: str
        topics: list[str]

        def to_dict(self, approach: str):
            if approach == "short":
                return {
                    "denominator_column_id": self.denominator_column_id,
                    "title": self.table_title,
                    "universe": self.universe,
                }

            return asdict(self)

    valid_table_ids = []
    table_metadata = {}
    for table, columns in groupby(
        table_rows,
        lambda x: Table(
            *(getattr(x, field) for field in Table.__annotations__)
        ),  # Pull all the same named fields from the sqlalch row
    ):
        valid_table_ids.append(table.table_id)
        table_metadata[table.table_id] = {
            **table.to_dict(table_approach),
            "columns": {
                column.column_id: col_strategy(column) for column in columns
            },
        }

    return valid_table_ids, table_metadata


def drop_whitespace(text: str) -> str:
    return "\n".join(
        [line.strip() for line in text.split("\n") if line.strip()]
    )


def build_fetch_query(table_ids: list[str]):
    from_table, *join_tables = table_ids
    from_stmt = "%s_moe" % (from_table)

    join_clause = "\n".join(
        [
            "FULL OUTER JOIN %s_moe USING (geoid)" % (table_id)
            for table_id in join_tables
        ]
    )

    return drop_whitespace(
        f"""
        SELECT * 
        FROM {from_stmt}
        {join_clause}
        WHERE geoid IN :geoids;
        """
    )


def pack_tables(row):
    fields = row._fields
    row = list(row)  # keep the variables around
    variables = [col for col in fields if col not in {"index", "geoid"}]

    result = {}
    for table, vars in groupby(variables, lambda col: col[:6]):
        result[table.upper()] = {
            "estimate": {
                var.upper(): val
                for var, val in zip(fields, row)
                if not (var.endswith("_moe") | (var == "geoid"))
            },
            "error": {
                var[:9].upper(): val
                for var, val in zip(fields, row)
                if var.endswith("_moe")
            },
        }

    return result


# 'data' -> geoid -> tableid -> estimate | error -> variable

# Redesign
# 'data' -> geoid -> tableid -> variable -> estimate & error


def fetch_data(table_ids, geoids, db):
    try:
        sql = text(build_fetch_query(table_ids))
        result = db.execute(sql, {"geoids": tuple(geoids)}).all()
        if len(result) < 1:
            return Failure("Query returned no data.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def column_prep_loop(data_iter):
    table_for_geoid = {}
    table_for_geoid["estimate"] = {}
    table_for_geoid["error"] = {}

    data_iter = list(data_iter)

    # The variables and moes are arranged consecutively, so use zip
    # and slice notation to iterate over two cols at a time.
    for (col_name, value), (_, moe_value) in zip(
        data_iter[:-1:2], data_iter[1::2]
    ):
        col_name = col_name.upper()

        table_for_geoid["estimate"][col_name] = value
        table_for_geoid["error"][col_name] = moe_value

    return table_for_geoid


def data_prep_loop(result):
    data = {}
    for row in result.fetchall():
        row = dict(row._mapping)
        geoid = row.pop("geoid")
        data_for_geoid = {}

        cols_iter = iter(sorted(row.items(), key=lambda tup: tup[0]))  # The key

        for table_id, data_iter in groupby(
            cols_iter, lambda x: x[0][:-3].upper()  # Grouping columns by table
        ):
            data_for_geoid[table_id] = column_prep_loop(data_iter)

        data[geoid] = data_for_geoid

    return data


def prep_temp_file(valid_table_ids, valid_geoids, format: str):
    temp_path = tempfile.mkdtemp()  # side effect ?
    file_ident = (
        f"{acs}_{next(iter(valid_table_ids))}_{next(iter(valid_geoids))}"
    )
    inner_path = os.path.join(temp_path, file_ident)
    os.mkdir(inner_path)  # side effect !

    return os.path.join(inner_path, f"{file_ident}.{format}")


def prepare_download():
    """
    try:
        valid_geoids, _ = expand_geoids(geoids, release="acs2021_5yr")

    except ShowDataException as e:
        return Failure((400, e.message))


    if (num_geoids := len(valid_geoids)) > current_app.config.get(
        "MAX_GEOIDS_TO_DOWNLOAD", 500
    ):
        abort(
            400,
            f"You requested {num_geoids} geoids which is beyond our limit of 500.",
        )

    geoid_result = grab_remaining_geoid_info(tuple(valid_geoids), db)

    match geoid_result:
        case Success(inner):
            geo_rows = inner

        case Failure(e):
            app.logger.error(f"The query is failing with error: {e}.")
            abort(400, "Query error due to requested geoids.")

    table_result = check_table_requests(table_ids, db)

    match table_result:
        case Success(inner):
            table_rows = inner

        case Failure(e):
            app.logger.error(f"The query is failing with error: {e}.")
            abort(400, "Query error due to requested table ids.")"""


# Geography queries -----------------------------------------------------------
# ~ 285 lines - maybe place in a separate module
# TODO break out the query build step to greatly simplify this
# All query the same table, most using geoids


def get_geography_info(geoids, db, with_geom=False, fetchone=False):
    """
    This is almost identical to the functions below.

    TODO see if the query build can be nicely refactored.
    """
    select = [
        "SELECT display_name",
        "       simple_name",
        "       sumlevel",
        "       full_geoid",
        "       population",
        "       aland",
        "       awater",
    ]

    if with_geom:
        select.append(
            "       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00005), 6) as geom"
        )

    select_compiled = ",\n".join(select)

    result = db.execute(
        text(
            dedent(
                f"""
                {select_compiled}
                FROM tiger2021.census_name_lookup
                WHERE full_geoid IN :geoids
                LIMIT 1
                """
            )
        ),
        {"geoids": tuple(geoids)},
    )

    if fetchone:
        return result.fetchone()
    return result


def search_geos_by_point(
    lat,
    lon,
    db,
    with_geom=False,
    limit=15,
    offset=0,
    sumlevs: tuple[str, ...] | None = None,
):
    select = [
        "SELECT DISTINCT geoid",
        "                sumlevel",
        "                population",
        "                display_name",
        "                full_geoid",
        "                priority",
    ]

    if with_geom:
        select.append(
            "       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0001), 6) as geom"
        )

    select_compiled = ",\n".join(select)

    where_clause = [
        "WHERE ST_Intersects(geom, ST_SetSRID(ST_Point(:lon, :lat),4326))",
    ]

    if sumlevs is not None:
        where_clause.append("AND sumlevel IN :sumlevs")

    where_compiled = "\n".join(where_clause)

    return db.execute(
        text(
            dedent(
                f"""
                {select_compiled}
                FROM tiger2021.census_name_lookup
                {where_compiled}
                ORDER BY priority, population DESC NULLS LAST
                LIMIT :limit
                OFFSET :offset;
                """
            )
        ),
        {
            "lon": lon,
            "lat": lat,
            "limit": limit,
            "offset": offset,
            "sumlevs": sumlevs,
        },
    )


def prep_q_for_text_search(q):
    return q.replace(" ", " | ")


def search_geos_by_query(
    q,
    db,
    with_geom=False,
    limit=15,
    offset=0,
    sumlevs: tuple[str, ...] | None = None,
):
    """
    For this to work correctly it requires specific setup in the
    database. First there must be a text search column added to the tiger2021 table:

        ALTER TABLE tiger2021.census_name_lookup
            ADD COLUMN searchable_geo_name tsvector
                GENERATED ALWAYS AS (to_tsvector('english', coalesce(display_name, ''))) STORED;

    Then you create an index on that for faster searching (there are no frequent writes so no performance worries):

        CREATE INDEX display_name_search_idx ON tiger2021.census_name_lookup USING GIN (searchable_geo_name);
    """

    select = [
        "SELECT DISTINCT geoid",
        "                sumlevel",
        "                population",
        "                display_name",
        "                full_geoid",
        "                priority",
    ]

    if with_geom:
        select.append(
            "       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0001), 6) as geom"
        )

    select_compiled = ",\n".join(select)

    where_clause = [
        "WHERE ST_Intersects(geom, ST_SetSRID(ST_Point(:lon, :lat),4326))",
    ]

    if sumlevs is not None:
        where_clause.append("AND sumlevel IN :sumlevs")

    where_compiled = "\n".join(where_clause)

    prepped_q = prep_q_for_text_search(q)

    return db.execute(
        text(
            f"""
            {select_compiled}
            FROM tiger2021.census_name_lookup
            {where_compiled}
            ORDER BY sumlevel ASC, population DESC NULLS LAST
            LIMIT :limit
            OFFSET :offset;
            """
        ),
        {"q": prepped_q, "limit": limit, "offset": offset, "sumlevs": sumlevs},
    )


@dataclass
class ViewportLocation:
    zoom: int
    x: int
    y: int

    @property
    def tiles_across(self):
        return 2**self.zoom

    @property
    def lat_lon(self):
        """
        Guessing at what this does. It takes the x, y input which is in 'tiles'
        units and converts to lat, lon.

        In the outer function the 2**zoom is named 'tiles_across' but not sure if this is
        true since the number is often pretty large, eg. 2**11 == 2048.

        Example: Livonia, MI calls the tiles endpoint that are the outer product
        of 548-552 (x) and 756-758 (y).
        """

        lon = self.x / self.tiles_across * 360.0 - 180.0
        lat_rad = math.atan(
            math.sinh(math.pi * (1 - 2 * self.y / self.tiles_across))
        )
        lat = math.degrees(lat_rad)

        return lat, lon

    @property
    def degree_per_tile(self):
        return 360 / self.tiles_across

    @property
    def degree_per_pixel(self):
        return self.degree_per_tile / 256

    def tile_buffer(self):
        return self.degree_per_pixel * 10

    def simplify_threshold(self):
        return self.degree_per_pixel / 5

    def min_corner(self):
        return self.lat_lon

    def max_corner(self):
        return ViewportLocation(self.zoom, self.x + 1, self.y + 1).lat_lon


def get_neighboring_boundaries(sumlevel, loc: ViewportLocation, db):
    miny, minx = loc.min_corner()
    maxy, maxx = loc.max_corner()

    result = db.execute(
        text(
            f"""SELECT ST_AsGeoJSON(
                        ST_SimplifyPreserveTopology(
                            ST_Intersection(
                                ST_Buffer(
                                    ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326), 
                                    :tile_buffer, 'join=mitre'
                                ), geom
                            ), :simplify_threshold
                        ), 5
                    ) as geom,
                    full_geoid,
                    display_name
            FROM tiger2021.census_name_lookup
            WHERE sumlevel=:sumlev 
                AND ST_Intersects(ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326), geom)
            """
        ),
        {
            "minx": minx,
            "miny": miny,
            "maxx": maxx,
            "maxy": maxy,
            "sumlev": sumlevel,
            "tile_buffer": loc.tile_buffer(),
            "simplify_threshold": loc.simplify_threshold(),
        },
    )

    return result


def get_details_for_geoids(geoids, db):
    result = db.execute(
        text(
            """
            SELECT display_name,sumlevel,full_geoid
            FROM tiger2021.census_name_lookup
            WHERE full_geoid IN :geoids
            ORDER BY sumlevel DESC;
            """
        ),
        {"geoids": geoids},
    )

    return {
        row.full_geoid: {
            "display_name": row.display_name,
            "sum_level": row.sumlevel,
            "geoid": row.full_geoid,
        }
        for row in result
    }


# END Geography queries -------------------------------------------------------


# Trying to understand why we'd need this?
def get_all_child_geoids(child_summary_level, db):
    result = db.execute(
        text(
            """SELECT geoid,name
           FROM geoheader
           WHERE sumlevel=:sumlev AND component='00' AND geoid NOT IN ('04000US72')
           ORDER BY name"""
        ),
        {"sumlev": int(child_summary_level)},
    )

    return result.fetchall()


def get_geo_parents_from_db(geoid: str, db):
    result = db.execute(
        text(
            """
            SELECT parent_geoid as geoid, display_name
            FROM tiger2021.census_geo_containment cgc
            JOIN tiger2021.census_name_lookup cnl 
                 ON cgc.parent_geoid = cnl.full_geoid
            WHERE cgc.child_geoid = (:geoid)::varchar
            AND percent_covered > 5;
            """
        ),
        {"geoid": geoid},
    )

    return result


def infer_parent_geoids(short_geoid, sum_level):
    """
    The short geoid is that which doesn't include the sum_level identifier
    information. The Detroit county subdivision's short_geoid is
    2616322000, for example.
    """

    GeoidPlan = namedtuple("GeoidPlan", "relation coverage stem slice")

    infer_plans = {
        "050": GeoidPlan("county", 100, "05000US", slice(0, 5)),
        "040": GeoidPlan("state", 100, "04000US", slice(0, 2)),
        "310": GeoidPlan("CBSA", 100, "31000US", slice(0, 5)),
    }

    plan = infer_plans[sum_level]

    return {
        "relation": plan.relation,
        "geoid": plan.stem + short_geoid[plan.slice],
        "coverage": plan.coverage,
    }


def get_parent_geoids(geoid, db):
    geoid = geoid.upper()
    try:
        stem, short_geoid = geoid.split("US")
        sum_level = stem[:3]

    except ValueError:
        raise ValueError(
            "Invalid geoid. Make sure you're providing a full geoid like '15000US2616322000.'"
        )

    levels = []  # Create the pile of parents
    # First, add the geography itself back to the pile
    levels.append(
        {
            "relation": "this",
            "geoid": geoid,
            "coverage": 100.0,
        }
    )

    # Based on sumlevel, choose which comparisons to include
    # infer state
    if sum_level in (
        "050",
        "060",
        "140",
        "150",
        "160",
        "500",
        "610",
        "620",
        "795",
        "950",
        "960",
        "970",
    ):
        levels.append(infer_parent_geoids(short_geoid, "040"))

    # infer county
    if sum_level in ("060", "140", "150"):
        levels.append(infer_parent_geoids(short_geoid, "050"))

    # After inferring those two, add other geos from lookup.
    if sum_level in (
        "160",
        "310",
        "330",
        "350",
        "860",
        "950",
        "960",
        "970",
    ):
        parent_rows = get_geo_parents_from_db(geoid, db)
        for row in parent_rows:
            try:
                parent_geo_name = SUMLEV_NAMES[row.parent_geoid[:3]]["name"]
            except (IndexError, KeyError):
                logger.warning(
                    f"A parent for {geoid} has a corrupted geoid in geocontainment table."
                )
                continue

            levels.append(
                {
                    "relation": parent_geo_name,
                    "geoid": row.parent_geoid,
                    "coverage": row.percent_covered,
                }
            )

    return levels


def get_child_geoids_by_coverage(parent_geoid, child_summary_level, db):
    result = db.execute(
        text(
            """
            SELECT geoid, name
            FROM tiger2021.census_geo_containment, geoheader
            WHERE geoheader.geoid = census_geo_containment.child_geoid
                AND census_geo_containment.parent_geoid = :parent_geoid
                AND census_geo_containment.child_geoid LIKE :child_geoids
                AND census_geo_containment.percent_covered > 10;
            """
        ),
        {
            "parent_geoid": parent_geoid,
            "child_geoids": child_summary_level + "%",
        },
    )

    return result


def get_child_geoids_by_gis(child_summary_level, parent_geoid, db):
    result = db.execute(
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
        result = db.execute(
            text(
                """
                SELECT geoid,name
                FROM geoheader
                WHERE geoid IN :child_geoids
                ORDER BY name;"""
            ),
            {"child_geoids": tuple(child_geoids)},
        )
        return result.fetchall()
    else:
        return []


def get_child_geoids_by_prefix(parent_geoid, child_summary_level, db):
    short_geoid = (parent_geoid.upper().split("US")[1],)
    child_geoid_prefix = f"{child_summary_level}00US{short_geoid}%%"

    # Use the "worst"/biggest ACS to find all child geoids
    result = db.execute(
        text(
            """
            SELECT geoid,name
            FROM geoheader
            WHERE geoid LIKE :geoid_prefix
                AND name NOT LIKE :not_name
            ORDER BY geoid;
            """
        ),
        {"geoid_prefix": child_geoid_prefix, "not_name": "%%not defined%%"},
    )
    return result.fetchall()


def get_child_geoids(release, parent_geoid, child_summary_level, db):
    parent_sumlevel = parent_geoid[0:3]

    db.execute(text("SET search_path TO :acs,public;"), {"acs": release})

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


class ShowDataException(Exception):
    pass


def find_explicit_geoids(release, explicit_geoids, db):
    db.execute(text("SET search_path TO :acs,public;"), {"acs": release})
    result = db.execute(
        text(
            """
            SELECT geoid
            FROM acs2021_5yr.geoheader
            WHERE geoid IN :geoids;
            """
        ),
        {"geoids": tuple(explicit_geoids)},
    )

    return result


def corral_geoid_strings(geoids):
    """
    The endpoint accepts geoids, but also child-parent relation strings
    of the form sumlevel|parent_geoid. This separates a list of these
    two types into separate lists.
    """
    explicit_geoids: list[str] = []
    expandable_geoids: list[tuple[str, str]] = []

    for geoid in geoids:
        try:
            sumlevel, parent = geoid.split("|")
            expandable_geoids.append((sumlevel, parent))
        except ValueError:
            explicit_geoids.append(geoid)

    return explicit_geoids, expandable_geoids


def expand_expandable_geoids(expandable_geoids, release, db):
    expanded_geoids = []
    child_parent_map = {}
    for parent_geoid, child_sum_level in expandable_geoids:
        child_geoid_list = [
            child_geoid.geoid
            for child_geoid in get_child_geoids(
                release, parent_geoid, child_sum_level, db
            )
        ]
        expanded_geoids.extend(child_geoid_list)

        for child_geoid in child_geoid_list:
            child_parent_map[child_geoid] = parent_geoid

    return expanded_geoids, child_parent_map


def expand_geoids(geoid_list: list[str], release: str, db):
    explicit_geoids, expandable_geoids = corral_geoid_strings(geoid_list)

    expanded_geoids, child_parent_map = expand_expandable_geoids(
        expandable_geoids, release, db
    )

    # Since the expanded geoids were sourced from the database they don't need to be checked
    valid_geo_ids = []
    valid_geo_ids.extend(expanded_geoids)

    # Check to make sure the geo ids the user entered are valid
    if explicit_geoids:
        result = find_explicit_geoids(release, explicit_geoids, db)
        valid_geo_ids.extend([geo[0] for geo in result])

    invalid_geo_ids = set(expanded_geoids + explicit_geoids) - set(
        valid_geo_ids
    )
    if invalid_geo_ids:
        raise ShowDataException(
            f"The '{release}' release doesn't include GeoID(s) {','.join(invalid_geo_ids)}."
        )

    return set(valid_geo_ids), child_parent_map


def get_data_fallback(
    table_ids: list[str], geoids: list[str], db, acs="acs2021_5yr"
):
    stmt = build_fetch_query(table_ids)
    result = db.execute(stmt, {"geoids", geoids})

    data = {row.geoid: convert_row_to_dict(row) for row in result.fetchall()}

    return data, acs


def format_table_search_result(obj, include_columns, release):
    """internal util for formatting each object in `table_search` API response"""

    result = {
        "table_id": obj.table_id,
        "table_name": obj.table_title,
        "simple_table_name": obj.simple_table_title,
        "topics": obj.topics,
        "universe": obj.universe,
        "release": release,
    }

    if include_columns:
        result.update(
            {
                "id": obj.column_id,
                "type": "column",
                "unique_key": f"{obj.table_id}|{obj.column_id}",
                "column_id": obj.column_id,
                "column_name": obj.column_title,
            }
        )

        return result

    result.update(
        {
            "id": obj.table_id,
            "type": "table",
            "unique_key": obj.table_id,
        }
    )

    return result


def get_table_metadata(table_ids, release, db, include_columns=False):
    select = [
        "SELECT tab.table_id",
        "       tab.table_title",
        "       tab.simple_table_title",
        "       tab.subject_area",
        "       tab.universe",
        "       tab.denominator_column_id",
        "       tab.topics",
    ]

    _from = ["FROM census_table_metadata tab"]

    order_by = ["tab.table_id"]

    if include_columns:
        select.extend(
            [
                "         col.column_id",
                "         col.parent_column_id",
                "         col.column_title",
                "         col.indent",
            ]
        )

        _from.append("LEFT JOIN census_column_metadata col USING (table_id)")

        order_by.append("col.column_id")

    select_compiled = ",\n".join(select)
    from_compiled = "\n".join(_from)
    order_by_compiled = ", ".join(order_by)

    stmt = dedent(
        f"""
        {select_compiled}
        {from_compiled}
        WHERE table_id IN :table_ids
        ORDER BY {order_by_compiled};
        """
    )

    result = db.execute(text(stmt), {"table_ids": tuple(table_ids)})

    return result


def get_tabulation(tabulation_id, db):
    return db.execute(
        text(
            """SELECT *
            FROM census_tabulation_metadata
            WHERE tabulation_code=:tabulation
            """
        ),
        {"tabulation": tabulation_id},
    ).fetchone()
