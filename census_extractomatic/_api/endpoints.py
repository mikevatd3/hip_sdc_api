import os
import json

from flask import Flask, jsonify, make_response, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
import tomli

from ..validation import (
    qwarg_validate,
    NonemptyString,
    FloatRange,
    StringList,
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
    wrap_up_columns,
    fetch_data,
    expand_geoids,
)
from .http_utils import crossdomain
from .reference import SUMLEV_NAMES, allowed_acs

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
    }
)
@crossdomain(origin="*")
def geo_search():
    pass


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
        geoid, db.session, with_geom=request.qwargs.geom, fetchone=True
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

    result = json.dumps(
        {
            level["geoid"]: {**level, **details[level["geoid"]]}
            for level in levels
        }
    )

    response = make_response(result)
    response.headers.set("Content-Type", "application/json")

    return response


@app.route("/1.0/table/search")
@qwarg_validate(
    {
        "acs": {
            "valid": OneOf(allowed_acs),
            "default": default_table_search_release,
        },
        "q": {"valid": NonemptyString()},
        "topics": {"valid": StringList()},
    }
)
@crossdomain(origin="*")
def table_search():
    pass


@app.route("/1.0/tabulation/<tabulation_id>")
@crossdomain(origin="*")
def tabulation_details(tabulation_id):
    pass


@app.route("/1.0/table/<table_id>")
@qwarg_validate(
    {
        "acs": {
            "valid": OneOf(allowed_acs),
            "default": default_table_search_release,
        }
    }
)
@crossdomain(origin="*")
def table_details(table_id):
    pass


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

    if acs not in allowed_acs:
        abort(404, "The {acs} release isn't supported.")

    # valid_geo_ids only contains geos for which we want data
    try:
        valid_geo_ids, child_parent_map = expand_geoids(
            request.qwargs.geo_ids, release=acs, db=db
        )
    except ShowDataException as e:
        abort(400, e)

    if not valid_geo_ids:
        abort(
            404,
            "None of the geo_ids specified were valid: %s"
            % ", ".join(request.qwargs.geo_ids),
        )

    if (id_count := valid_geo_ids) > max_geoids:
        abort(
            400,
            f"You requested {id_count} geoids. The maximum is {max_geoids}. Please contact us for bulk data.",
        )

    # No idea what this means.
    # expand_geoids has validated parents of groups by getting children;
    # this will include those parent names in the reponse `geography` list
    # but leave them out of the response `data` list
    grouped_geo_ids = [item for item in request.qwargs.geoids if "|" in item]
    parents_of_groups = set(
        [item_group.split("|")[1] for item_group in grouped_geo_ids]
    )
    named_geo_ids = valid_geo_ids | parents_of_groups

    # Fill in the display name for the geos
    try:
        result = get_geography_info(named_geo_ids, db)
    except Exception as e:
        print(e)
        abort(400, f"There was an error processing your request.")

    geo_metadata = {}
    for full_geoid, _, display_name in result:
        geo_metadata[full_geoid] = {
            "name": display_name,
        }
        # let children know who their parents are to distinguish between
        # groups at the same summary level
        if full_geoid in child_parent_map:
            geo_metadata[full_geoid]["parent_geoid"] = child_parent_map[
                full_geoid
            ]

        db.session.execute(
            text("SET search_path TO :acs, public;"), {"acs": acs}
        )

    try:
        columns = get_table_data(request.kwargs.table_ids, db.session)
    except (ProgrammingError, OperationalError) as e:
        raise e

    table_metadata = wrap_up_columns(columns)
    data = fetch_data(list(table_ids.keys()), valid_geoids, db.session)

    table_metadata * 10
    data * 100


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
    pass


@app.route("/healthcheck")
def healthcheck():
    pass


@app.route("/robots.txt")
def robots_txt():
    pass


@app.route("/")
def index():
    pass


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
