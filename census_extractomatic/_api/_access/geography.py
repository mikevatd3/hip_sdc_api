from dataclasses import dataclass
import math
from sqlalchemy import text

from icecream import ic
from pypika import Table, Query, CustomFunction, Parameter, Order
from pypika_gis.spatialtypes import postgis as st
from .ts_custom_functions import ts_indexed, prep_q_for_text_search


simplify = CustomFunction("ST_SimplifyPreserveTopology", ["geom", "threshold"])


def make_geo_statement(
    table,
    with_geom=False,
    sumlevs=None,
):

    columns = [
        table.display_name,
        table.simple_name,
        table.sumlevel,
        table.full_geoid,
        table.population,
        table.aland,
        table.awater,
    ]

    if with_geom:
        columns.append(st.AsGeoJSON(simplify(table.geom, 0.0001)).as_("geom"))

    stmt = Query.from_(table).select(*columns)
    stmt = stmt.limit(Parameter(":limit")).offset(Parameter(":offset"))
    stmt = stmt.orderby(table.population, order=Order.desc)

    if sumlevs:
        stmt = stmt.where(table.sumlevel.isin(Parameter(":sumlevs")))

    return stmt


def get_geographies_with_ids(
    geoids,
    db,
    schema="tiger2021",
    with_geom=False,
    fetchone=False,
    limit=20,
    offset=0,
    sumlevs=None,
):
    census_name_lookup = Table("census_name_lookup", schema=schema)
    stmt = make_geo_statement(
        census_name_lookup,
        with_geom=with_geom,
        sumlevs=sumlevs,
    )

    stmt = stmt.where(census_name_lookup.full_geoid.isin(Parameter(":geoids")))

    result = db.execute(
        text(stmt.get_sql()),
        {
            "geoids": tuple(geoids),
            "limit": limit,
            "offset": offset,
            "sumlevs": sumlevs,
        },
    )

    if fetchone:
        return result.fetchone()

    return result


def search_geos_by_point(
    lat,
    lon,
    db,
    schema="tiger2021",
    with_geom=False,
    limit=15,
    offset=0,
    sumlevs: tuple[str, ...] | None = None,
):
    census_name_lookup = Table("census_name_lookup", schema=schema)
    stmt = make_geo_statement(
        census_name_lookup,
        with_geom=with_geom,
        sumlevs=sumlevs,
    )

    stmt = stmt.where(
        st.Intersects(
            census_name_lookup.geom,
            st.SetSRID(st.Point(Parameter(":lon"), Parameter(":lat")), 4326),
        )
    )

    result = db.execute(
        text(stmt.get_sql()),
        {
            "lat": lat,
            "lon": lon,
            "limit": limit,
            "offset": offset,
            "sumlevs": sumlevs,
        },
    )

    return result


def search_geos_by_query(
    q,
    db,
    schema="tiger2021",
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

    census_name_lookup = Table("census_name_lookup", schema=schema)
    stmt = make_geo_statement(
        census_name_lookup,
        with_geom=with_geom,
        sumlevs=sumlevs,
    )

    stmt = stmt.where(
        ts_indexed(census_name_lookup.searchable_geo_name, Parameter(":q"))
    )

    result = db.execute(
        text(stmt.get_sql()),
        {
            "q": prep_q_for_text_search(q),
            "limit": limit,
            "offset": offset,
            "sumlevs": sumlevs,
        },
    )

    return result


@dataclass
class ViewportLocation:
    zoom: int
    x: int
    y: int

    @property
    def tiles_across(self):
        return 2 ** self.zoom

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
    # For now it might not be worth using the query builder
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
    result = get_geographies_with_ids(geoids, db, limit=None)
    return {
        row.full_geoid: {
            "display_name": row.display_name,
            "sum_level": row.sumlevel,
            "geoid": row.full_geoid,
        }
        for row in result
    }

