import os
import json

from flask import Flask, jsonify, make_response, abort
from flask import abort, request, g, current_app
from flask_sqlalchemy import SQLAlchemy
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
    get_details_for_geoids,
    convert_row_to_dict,
    get_table_metadata,
    group_tables,
    pack_tables,
    fetch_data,
    expand_geoids,
    search_geos_by_point,
    search_geos_by_query,
    get_tabulation,
)
from ._access.tables import search_tables
from .http_utils import crossdomain
from .reference import (
    SUMLEV_NAMES,
    ALLOWED_ACS,
    ACS_NAMES,
    default_table_search_release,
    supported_formats,
)


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
        dict(type="Feature", properties=result, geometry=geom),
        separators=(",", ":"),
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

    result = json.dumps({
        "parents": [
            {**level, **ic(details).get(level["geoid"], {})}
            for level in levels
        ]
    })

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

    data = get_table_metadata(
        request.qwargs.topics,
        request.qwargs.acs,
        db.session,
        include_columns=False,
    )

    if data:
        data.sort(key=lambda x: x["unique_key"])
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
    ic(request.qwargs.q)
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
    result = get_table_metadata((table_id,), result.qwargs.acs, db.session)
    row = result.fetchone()


@app.route("/2.0/table/<release>/<table_id>")
@crossdomain(origin="*")
def table_details_with_release(release, table_id):
    pass


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
    pass


@app.route("/1.0/data/show/<acs>")
@qwarg_validate(
    {
        "table_ids": {"valid": StringList(), "required": True},
        "geo_ids": {"valid": StringList(), "required": True},
    }
)
@crossdomain(origin="*")
def show_specified_data(acs):
    max_geoids = current_app.config.get("MAX_GEOIDS_TO_SHOW", 1000)

    app.logger.debug(request.qwargs.table_ids)
    app.logger.debug(request.qwargs.geo_ids)

    if acs not in ALLOWED_ACS:
        abort(404, "The {acs} release isn't supported.")

    # valid_geo_ids only contains geos for which we want data
    try:
        valid_geo_ids, child_parent_map = expand_geoids(
            request.qwargs.geo_ids, release=acs, db=db.session
        )
    except ShowDataException as e:
        abort(400, e)

    if not valid_geo_ids:
        abort(
            404,
            "None of the geo_ids specified were valid: %s"
            % ", ".join(request.qwargs.geo_ids),
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
    grouped_geo_ids = [item for item in request.qwargs.geo_ids if "|" in item]
    parents_of_groups = set(
        [item_group.split("|")[1] for item_group in grouped_geo_ids]
    )
    named_geo_ids = valid_geo_ids | parents_of_groups

    # Fill in the display name for the geos
    try:
        result = get_geography_info(named_geo_ids, db.session)
    except Exception as e:
        print(e)
        abort(400, f"There was an error processing your request.")

    geo_metadata = {}
    for row in result:
        geo_metadata[row.full_geoid] = {
            "name": row.display_name,
        }
        # let children know who their parents are to distinguish between
        # groups at the same summary level
        if row.full_geoid in child_parent_map:
            geo_metadata[row.full_geoid]["parent_geoid"] = child_parent_map[
                row.full_geoid
            ]

        db.session.execute(
            text("SET search_path TO :acs, public;"), {"acs": acs}
        )

    try:
        columns = get_table_metadata(request.qwargs.table_ids, request.qwargs.acs, db.session, include_columns=True)
    except (ProgrammingError, OperationalError) as e:
        raise e

    valid_table_ids, table_metadata  = group_tables(columns)
    result = fetch_data(valid_table_ids, valid_geo_ids, db.session)

    match result:
        case Failure(e):
            ic(e)
            abort(404, f"Unable to fetch data due to {e}")

        case Success(data):
            release = ACS_NAMES[acs].copy()
            release["id"] = acs

            response = {
                "tables": table_metadata,
                "geography": {geoid: geo_metadata[geoid] for geoid in valid_geo_ids},
                "release": release,
                "data": {row.geoid: pack_tables(row) for row in data},
            }

            response = json.dumps(response)
            resp = make_response(response)

            resp.headers.set("Content-Type", "application/json")
            resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day

            return resp


@app.route("/1.0/data/download/<acs>")
@qwarg_validate(
    {
        "table_ids": {"valid": StringList(), "required": True},
        "geo_ids": {"valid": StringList(), "required": True},
        "format": {"valid": OneOf(supported_formats), "required": True},
    }
)
@crossdomain(origin="*")
def download_specified_data(*args, **kwargs):
    pass


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
    resp = make_response(json.dumps({}))
    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day
    return resp


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