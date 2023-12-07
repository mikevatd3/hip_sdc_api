import os
import json
from collections import OrderedDict

from flask import Flask, jsonify, make_response, abort
from flask import abort, request, g, current_app, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from raven.contrib.flask import Sentry
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
import pylibmc
import tomli
from returns.result import Result, Success, Failure
from icecream import ic

from ..validation import (
    qwarg_validate,
    NonemptyString,
    FloatRange,
    StringList,
    Integer as ValidInteger,
    Bool,
    OneOf,
    ClientRequestValidationException,
)
from .access import (
    ShowDataException,
    ViewportLocation,
    get_neighboring_boundaries,
    get_geography_info,
    get_parent_geoids,
    get_child_geoids,
    convert_row_to_dict,
    get_table_metadata,
    group_tables,
    fetch_data,
    expand_geoids,
    query_table_metadata,
    search_geos_by_point,
    search_geos_by_query,
    get_tabulation,
    show_col_builder,
)
from ..download_specified_data import check_table_requests
from .download_data import (
    prepare_csv_response,
    prepare_excel_response,
    prepare_shape_response,
    prepare_json_response,
    prepare_geojson_response,
)

from ._access.tables import search_tables
from ._access.geography import get_details_for_geoids
from .http_utils import crossdomain
from .reference import (
    SUMLEV_NAMES,
    ALLOWED_ACS,
    ALLOWED_TIGER,
    ACS_NAMES,
    default_table_search_release,
    supported_formats,
)


ic.configureOutput(includeContext=True)


with open("config.toml", "rb") as f:
    config = tomli.load(f)


app = Flask(__name__)
app.config.from_object(
    os.environ.get(
        "EXTRACTOMATIC_CONFIG_MODULE", "census_extractomatic.config.Development"
    )
)
app.config["SECRET_KEY"] = config["sessions"]["secret_key"]

db = SQLAlchemy(app)
# login = LoginManager(app)

# Set up metadata endpoint

from census_extractomatic.metadata_api.src import metadata_api
from census_extractomatic.metadata_api.admin import register_d3_metadata_admin


app.register_blueprint(metadata_api, url_prefix="/metadata")
register_d3_metadata_admin(app)

# from census_extractomatic.auth import auth

# app.register_blueprint(auth, url_prefix="/auth")


sentry = Sentry(app)


@app.before_request
def before_request():
    memcache_addr = app.config.get("MEMCACHE_ADDR")
    g.cache = pylibmc.Client(
        memcache_addr
    )  # if memcache_addr else mockcache.Client(memcache_addr)


@app.route("/1.0/geo/search")
@qwarg_validate(
    {
        "lat": {"valid": FloatRange(-90.0, 90.0)},
        "lon": {"valid": FloatRange(-180.0, 180.0)},
        "q": {"valid": NonemptyString()},
        "sumlevs": {"valid": StringList(item_validator=OneOf(SUMLEV_NAMES))},
        "geom": {"valid": Bool()},
        "limit": {"valid": ValidInteger()},
        "offset": {"valid": ValidInteger()},
    }
)
@crossdomain(origin="*")
def geo_search():
    # PREPARE QWARGS
    # Load defaults if not present in qwargs
    if not (limit := request.qwargs.limit):
        limit = 15

    limit = min((current_app.config.get("MAX_GEOIDS_TO_SHOW", 500), limit))

    if not (offset := request.qwargs.offset):
        offset = 0

    # Call db functions -- TODO include geom bool
    if request.qwargs.lat and request.qwargs.lon:
        result = search_geos_by_point(
            request.qwargs.lat,
            request.qwargs.lon,
            db.session,
            limit=limit,
            offset=offset,
            sumlevs=request.qwargs.sumlevs,
        )

    elif request.qwargs.q:
        result = search_geos_by_query(
            request.qwargs.q,
            db.session,
            limit=limit,
            offset=offset,
            sumlevs=request.qwargs.sumlevs,
        )
    else:
        abort(400, "Must provide either a lat/lon OR a query term.")

    return jsonify([convert_row_to_dict(row) for row in result])


@app.route(
    "/1.0/geo/<release>/tiles/<sumlevel>/<int:zoom>/<int:x>/<int:y>.geojson"
)
@crossdomain(origin="*")
def geo_tiles(release, sumlevel, zoom, x, y):
    if sumlevel not in SUMLEV_NAMES:
        abort(404, "Unknown sumlevel")
    if sumlevel == "010":
        abort(400, "Don't support US tiles")

    loc = ViewportLocation(zoom, x, y)

    rows = get_neighboring_boundaries(sumlevel, loc, db.session)

    results = []
    for row in rows:
        results.append(
            {
                "type": "Feature",
                "properties": {
                    "geoid": row.full_geoid,
                    "name": row.display_name,
                },
                "geometry": json.loads(row.geom) if row.geom else None,
            }
        )

    result = json.dumps(
        dict(type="FeatureCollection", features=results), separators=(",", ":")
    )

    resp = make_response(result)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day
    return resp


@app.route("/1.0/geo/<release>/<geoid>")
@qwarg_validate({"geom": {"valid": Bool(), "default": False}})
@crossdomain(origin="*")
def geo_lookup(release, geoid):
    geoid_parts = geoid.upper().split("US")
    if len(geoid_parts) != 2:
        abort(404, "Invalid GeoID")

    result = get_geography_info(
        (geoid,), db.session, with_geom=request.qwargs.geom, fetchone=True
    )

    if not result:
        abort(404, "Unknown GeoID")

    # Change result mapping to a vanilla dict
    result = convert_row_to_dict(result)
    geom = result.pop("geom", None)
    if geom:
        geom = json.loads(geom)

    result = json.dumps(
        dict(
            type="Feature", properties=result, geometry=geom
        ),  # Hand-writing GeoJSON, nice
        separators=(",", ":"),
        default=lambda x: str(x),
    )

    resp = make_response(result)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=%d" % int(3600 * 4))

    return resp


@app.route("/1.0/geo/<release>/<geoid>/parents")
@crossdomain(origin="*")
def geo_parent(release, geoid):
    levels = get_parent_geoids(geoid, db.session)

    details = get_details_for_geoids(
        tuple(level["geoid"] for level in levels), db.session
    )

    result = json.dumps(
        {
            "parents": [
                {**level, **details.get(level["geoid"], {})} for level in levels
            ]
        }
    )

    response = make_response(result)
    response.headers.set("Content-Type", "application/json")

    return response


@app.route("/1.0/table/search")
@qwarg_validate(
    {
        "acs": {
            "valid": OneOf(ALLOWED_ACS),
            "default": default_table_search_release,
        },
        "q": {"valid": NonemptyString()},
        "topics": {"valid": StringList()},
    }
)
@crossdomain(origin="*")
def table_search():
    # Matching for table id
    db.session.execute(
        text("SET search_path TO :acs, public;"), {"acs": request.qwargs.acs}
    )

    data = query_table_metadata(
        request.qwargs.q,
        request.qwargs.acs,
        db.session,
    )

    if data:
        data = sorted(data, key=lambda x: x["unique_key"])
        return json.dumps(data)

    else:
        abort(404, f"No table found matching query.")


@app.route("/1.0/table/ts")
@qwarg_validate(
    {
        "acs": {
            "valid": OneOf(ALLOWED_ACS),
            "default": default_table_search_release,
        },
        "q": {"valid": NonemptyString()},
        "limit": {"valid": ValidInteger()},
        "offset": {"valid": ValidInteger()},
    }
)
@crossdomain(origin="*")
def table_ts():
    # Matching for table id
    request.qwargs.q
    if not (limit := request.qwargs.limit):
        limit = 5

    if not (offset := request.qwargs.offset):
        offset = 0

    result = search_tables(
        request.qwargs.q,
        request.qwargs.acs,
        db.session,
        limit=limit,
        offset=offset,
    )

    if not result:
        abort(404, f"No table found matching query.")

    dicts = [convert_row_to_dict(row) for row in result]
    json_dicts = json.dumps(dicts)

    response = make_response(json_dicts)

    response.headers.set("Content-Type", "application/json")
    response.headers.set("Cache-Control", "public,max-age=86400")  # 1 day

    return response


@app.route("/1.0/tabulation/<tabulation_id>")
@crossdomain(origin="*")
def tabulation_details(tabulation_id):
    row = get_tabulation(tabulation_id, db.session)

    if not row:
        abort(404, "Tabulation %s not found." % tabulation_id)

    row = convert_row_to_dict(row)

    row["tables_by_release"] = {
        "one_yr": row.pop("tables_in_one_yr", []),
        "three_yr": row.pop("tables_in_three_yr", []),
        "five_yr": row.pop("tables_in_five_yr", []),
    }

    row.pop("weight", None)

    json_text = json.dumps(row)
    resp = make_response(json_text)
    resp.headers.set("Content-Type", "application/json")

    return resp


@app.route("/1.0/table/<table_id>")
@qwarg_validate(
    {
        "acs": {
            "valid": OneOf(ALLOWED_ACS),
            "default": default_table_search_release,
        }
    }
)
@crossdomain(origin="*")
def table_details(table_id):
    db.session.execute(
        text("SET search_path TO :acs, public;"), {"acs": request.qwargs.acs}
    )

    table_resp = get_table_metadata(
        (table_id,), request.qwargs.acs, db.session, include_columns=True
    )

    _, wrapped_up = group_tables(table_resp)

    response = json.dumps(wrapped_up[table_id])
    resp = make_response(response)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day

    return resp


@app.route("/2.0/table/<release>/<table_id>")
@crossdomain(origin="*")
def table_details_with_release(release, table_id):
    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": release})
    column_rows = get_table_metadata(
        (table_id,), release, db.session, include_columns=True
    )
    _, tables = group_tables(column_rows)

    try:
        return jsonify(tables[table_id])
    except KeyError:
        abort(404, f"{table_id} isn't included in the {release} edition.")


@app.route("/1.0/table/compare/rowcounts/<table_id>")
@qwarg_validate(
    {
        "year": {"valid": NonemptyString()},
        "sumlevel": {"valid": OneOf(SUMLEV_NAMES), "required": True},
        "within": {"valid": NonemptyString(), "required": True},
        "topics": {"valid": StringList()},
    }
)
@crossdomain(origin="*")
def table_geo_comparison_rowcount(table_id):
    """
    This is yet to be refactored.
    """
    years = request.qwargs.year.split(",")
    child_summary_level = request.qwargs.sumlevel
    parent_geoid = request.qwargs.within

    data = {}

    releases = []
    for year in years:
        releases += [name for name in ALLOWED_ACS if year in name]
    releases = sorted(releases)

    for acs in releases:
        db.session.execute(
            text("SET search_path TO :acs, public;"), {"acs": acs}
        )
        release = dict()
        release["release_name"] = ACS_NAMES[acs]["name"]
        release["release_slug"] = acs
        release["results"] = 0

        result = db.session.execute(
            text(
                """SELECT *
               FROM census_table_metadata
               WHERE table_id=:table_id;"""
            ),
            {"table_id": table_id},
        )
        table_record = result.fetchone()
        if table_record:
            table_record = table_record._mapping
            validated_table_id = table_record["table_id"]
            release["table_name"] = table_record["table_title"]
            release["table_universe"] = table_record["universe"]

            child_geoheaders = get_child_geoids(
                acs, parent_geoid, child_summary_level, db.session
            )

            if child_geoheaders:
                child_geoids = [
                    child._mapping["geoid"] for child in child_geoheaders
                ]
                result = db.session.execute(
                    text(
                        """SELECT COUNT(*)
                       FROM %s.%s
                       WHERE geoid IN :geoids"""
                        % (acs, validated_table_id)
                    ),
                    {"geoids": tuple(child_geoids)},
                )
                acs_rowcount = result.fetchone()
                release["results"] = acs_rowcount._mapping["count"]

        data[acs] = release

    json_string = json.dumps(data)
    resp = make_response(json_string)
    resp.headers.set("Content-Type", "application/json")

    return resp




def data_pull(table_ids, geoids, acs, db):
    max_geoids = current_app.config.get("MAX_GEOIDS_TO_SHOW", 1000)

    if acs not in ALLOWED_ACS:
        abort(404, f"The {acs} release isn't supported.")

    # valid_geo_ids only contains geos for which we want data
    try:
        valid_geo_ids, child_parent_map = expand_geoids(
            geoids, release=acs, db=db.session
        )
    except ShowDataException as e:
        abort(400, e)

    if not valid_geo_ids:
        abort(
            404,
            f"None of the geo_ids specified were valid: {', '.join(geoids)}",
        )

    if (id_count := len(valid_geo_ids)) > max_geoids:
        abort(
            400,
            f"You requested {id_count} geoids. The maximum is {max_geoids}. Please contact us for bulk data.",
        )

    # No idea what this means.
    # expand_geoids has validated parents of groups by getting children;
    # this will include those parent names in the reponse `geography` list
    # but leave them out of the response `data` list

    grouped_geo_ids = [item for item in geoids if "|" in item]
    parents_of_groups = set(
        [item_group.split("|")[1] for item_group in grouped_geo_ids]
    )
    named_geo_ids = valid_geo_ids | parents_of_groups

    # Fill in the display name for the geos
    try:
        geo_metadata = get_details_for_geoids(named_geo_ids, db.session)
    except Exception as e:
        abort(400, f"There was an error processing your request: {e}.")

    # let children know who their parents are to distinguish between
    # groups at the same summary level

    """
    if row.full_geoid in child_parent_map:
        geo_metadata[row.full_geoid]["parent_geoid"] = child_parent_map[
            row.full_geoid
        ]
    """

    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    try:
        columns = get_table_metadata(
            table_ids,
            acs,
            db.session,
            include_columns=True,
        )
    except (ProgrammingError, OperationalError) as e:
        raise e

    # The kwargs for this function force the output to match the reference
    valid_table_ids, table_metadata = group_tables(
        columns, col_strategy=show_col_builder, table_approach="short"
    )

    return (
        table_metadata,
        geo_metadata,
        valid_geo_ids,
        fetch_data(valid_table_ids, valid_geo_ids, db.session),
    )


@app.route("/1.0/geo/show/<release>")
@qwarg_validate(
    {
        "geo_ids": {"valid": StringList(), "required": True},
    }
)
def show_specified_geo_data(release):
    if release not in ALLOWED_TIGER:
        abort(404, "Unknown TIGER release")

    geoids, child_parent_map = expand_geoids(
        request.qwargs.geo_ids, release, db.session
    )

    geo_metadata = get_geography_info(
        geoids,
        db.session,
        with_geom=True,
    )

    return prepare_geojson_response(
        release,
        None,
        geo_metadata,
        None,
    )
    


@app.route("/1.0/data/show/<acs>")
@qwarg_validate(
    {
        "table_ids": {"valid": StringList(), "required": True},
        "geo_ids": {"valid": StringList(), "required": True},
    }
)
@crossdomain(origin="*")
def show_specified_data(acs):
    app.logger.debug(request.qwargs.table_ids)
    app.logger.debug(request.qwargs.geo_ids)

    table_metadata, geo_metadata, valid_geo_ids, result = data_pull(
        request.qwargs.table_ids, request.qwargs.geo_ids, acs, db
    )

    match result:
        case Failure(e):
            ic(e)
            abort(404, f"Unable to fetch data due to {e}")

        case Success(data):
            response = prepare_json_response(
                acs, table_metadata, geo_metadata, valid_geo_ids, data
            )
            return response


@app.route("/1.0/data/download/<acs>")
@qwarg_validate(
    {
        "table_ids": {"valid": StringList(), "required": True},
        "geo_ids": {"valid": StringList(), "required": True},
        "format": {"valid": OneOf(supported_formats), "required": True},
    }
)
@crossdomain(origin="*")
def download_specified_data(acs):
    app.logger.debug(request.qwargs.table_ids)
    app.logger.debug(request.qwargs.geo_ids)

    format_strategies = {
        "csv": prepare_csv_response,
        "geojson": prepare_geojson_response,
        "shape": prepare_shape_response,
        "excel": prepare_excel_response,
    }

    table_metadata, geo_metadata, valid_geo_ids, result = data_pull(
        request.qwargs.table_ids, request.qwargs.geo_ids, acs, db
    )

    match result:
        case Failure(e):
            abort(404, f"Unable to fetch data due to {type(e)}")

        case Success(data):
            response = format_strategies[request.qwargs.format](
                acs, table_metadata, geo_metadata, valid_geo_ids, data
            )

            return response


@app.route("/1.0/data/compare/<acs>/<table_id>")
@qwarg_validate(
    {
        "within": {"valid": NonemptyString(), "required": True},
        "sumlevel": {"valid": OneOf(SUMLEV_NAMES), "required": True},
        "geom": {"valid": Bool(), "default": False},
    }
)
@crossdomain(origin="*")
def data_compare_geographies_within_parent(acs, table_id):
    # make sure we support the requested ACS release
    if acs not in ALLOWED_ACS:
        abort(404, "The %s release isn't supported." % acs)
    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    parent_geoid = request.qwargs.within
    child_summary_level = request.qwargs.sumlevel

    # create the containers we need for our response
    comparison = {}
    table = {}
    parent_geography = {}
    child_geographies = {}

    # add some basic metadata about the comparison and data table requested.
    comparison["child_summary_level"] = child_summary_level
    comparison["child_geography_name"] = SUMLEV_NAMES.get(
        child_summary_level, {}
    ).get("name")
    comparison["child_geography_name_plural"] = SUMLEV_NAMES.get(
        child_summary_level, {}
    ).get("plural")
    
    result = check_table_requests((table_id,), db.session)

    match result:
        case Success(rows):
            table_metadata = rows.fetchall()

        case Failure(error):
            ic(error)
            abort(
                404,
                "Table %s isn't available in the %s release."
                % (table_id.upper(), acs),
            )

    validated_table_id = table_metadata[0]._mapping["table_id"]

    # get the basic table record, and add a map of columnID -> column name
    table_record = table_metadata[0]._mapping
    column_map = {}
    for record in table_metadata:
        record = record._mapping
        if record["column_id"]:
            column_map[record["column_id"]] = {}
            column_map[record["column_id"]]["name"] = record["column_title"]
            column_map[record["column_id"]]["indent"] = record["indent"]

    table["census_release"] = ACS_NAMES.get(acs).get("name")
    table["table_id"] = validated_table_id
    table["table_name"] = table_record["table_title"]
    table["table_universe"] = table_record["universe"]
    table["denominator_column_id"] = table_record["denominator_column_id"]
    table["columns"] = column_map

    # add some data about the parent geography
    result = db.session.execute(
        text("SELECT * FROM geoheader WHERE geoid=:geoid;"),
        {"geoid": parent_geoid},
    )
    parent_geoheader = result.fetchone()
    parent_sumlevel = "%03d" % parent_geoheader._mapping["sumlevel"]

    parent_geography["geography"] = OrderedDict()
    parent_geography["geography"]["name"] = parent_geoheader._mapping["name"]
    parent_geography["geography"]["summary_level"] = parent_sumlevel

    comparison["parent_summary_level"] = parent_sumlevel
    comparison["parent_geography_name"] = SUMLEV_NAMES.get(
        parent_sumlevel, {}
    ).get("name")
    comparison["parent_name"] = parent_geoheader._mapping["name"]
    comparison["parent_geoid"] = parent_geoid

    child_geoheaders = get_child_geoids(
        acs, parent_geoid, child_summary_level, db.session
    )

    # start compiling child data for our response
    child_geoid_list = [
        geoheader._mapping["geoid"] for geoheader in child_geoheaders
    ]
    child_geoid_names = dict(
        [
            (geoheader._mapping["geoid"], geoheader._mapping["name"])
            for geoheader in child_geoheaders
        ]
    )

    # get geographical data if requested
    child_geodata_map = {}
    if request.qwargs.geom:
        # get the parent geometry and add to API response
        result = db.session.execute(
            text(
                """SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,0.001), 5) as geometry
               FROM tiger2021.census_name_lookup
               WHERE full_geoid=:geo_ids;"""
            ),
            {"geo_ids": parent_geoid},
        )
        parent_geometry = result.fetchone()
        try:
            parent_geography._mapping["geography"]["geometry"] = json.loads(
                parent_geometry._mapping["geometry"]
            )
        except:
            # we may not have geometries for all sumlevs
            pass

        # get the child geometries and store for later
        result = db.session.execute(
            text(
                """SELECT geoid, ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,0.001), 5) as geometry
               FROM tiger2021.census_name_lookup
               WHERE full_geoid IN :geo_ids
               ORDER BY full_geoid;"""
            ),
            {"geo_ids": ic(tuple(child_geoid_list))},
        )
        child_geodata = result.fetchall()
        child_geodata_map = dict(
            [
                (
                    record._mapping["geoid"],
                    json.loads(record._mapping["geometry"]),
                )
                for record in child_geodata
            ]
        )

    # make the where clause and query the requested census data table
    # get parent data first...
    result = db.session.execute(
        text("SELECT * FROM %s_moe WHERE geoid=:geoid" % (validated_table_id)),
        {"geoid": parent_geoheader._mapping["geoid"]},
    )
    parent_data = dict(result.fetchone()._mapping)
    parent_data.pop("geoid", None)
    column_data = []
    column_moe = []
    sorted_data = list(sorted(parent_data.items(), key=lambda tup: tup[0]))

    for (k, v), (_, moe_v) in zip(sorted_data[:-1:2], sorted_data[1::2]):
        column_data.append((k.upper(), v))
        column_moe.append((k.upper(), moe_v))

    parent_geography["data"] = OrderedDict(column_data)
    parent_geography["error"] = OrderedDict(column_moe)

    if child_geoheaders:
        # ... and then children so we can loop through with cursor
        child_geoids = [child._mapping["geoid"] for child in child_geoheaders]
        result = db.session.execute(
            text(
                "SELECT * FROM %s_moe WHERE geoid IN :geo_ids"
                % (validated_table_id)
            ),
            {"geo_ids": tuple(child_geoids)},
        )

        # grab one row at a time
        for record in result:
            record = dict(record._mapping)
            child_geoid = record.pop("geoid")

            child_data = OrderedDict()
            this_geo_has_data = False

            # build the child item
            child_data["geography"] = OrderedDict()
            child_data["geography"]["name"] = child_geoid_names[child_geoid]
            child_data["geography"]["summary_level"] = child_summary_level

            column_data = []
            column_moe = []
            sorted_data = list(sorted(record.items(), key=lambda tup: tup[0]))
            for (k, v), (_, moe_v) in zip(
                sorted_data[:-1:2], sorted_data[1::2]
            ):
                if v is not None and moe_v is not None:
                    this_geo_has_data = True

                column_data.append((k.upper(), v))
                column_moe.append((k.upper(), moe_v))
            child_data["data"] = OrderedDict(column_data)
            child_data["error"] = OrderedDict(column_moe)

            if child_geodata_map:
                try:
                    child_data["geography"]["geometry"] = child_geodata_map[
                        child_geoid.split("US")[1]
                    ]
                except:
                    # we may not have geometries for all sumlevs
                    pass

            if this_geo_has_data:
                child_geographies[child_geoid] = child_data

            # TODO Do we really need this?
            comparison["results"] = len(child_geographies)
    else:
        comparison["results"] = 0

    return jsonify(
        comparison=comparison,
        table=table,
        parent_geography=parent_geography,
        child_geographies=child_geographies,
    )


@app.route("/healthcheck")
def healthcheck():
    app.logger.exception("Healthcheck was called!")
    return "OK"


@app.route("/robots.txt")
def robots_txt():
    response = make_response("User-agent: *\nDisallow: /\n")
    response.headers["Content-type"] = "text/plain"
    return response


@app.route("/")
def index():
    return redirect("https://www.datadrivendetroit.org")


@app.errorhandler(400)
@app.errorhandler(500)
@crossdomain(origin="*")
def jsonify_error_handler(error):
    if isinstance(error, ClientRequestValidationException):
        resp = jsonify(error=error.description, errors=error.errors)
        resp.status_code = error.code
    elif isinstance(error, HTTPException):
        resp = jsonify(error=error.description)
        resp.status_code = error.code
    else:
        resp = jsonify(error=error)
        resp.status_code = 500
    app.logger.exception("Handling exception %s, %s", error, error)

    return resp
