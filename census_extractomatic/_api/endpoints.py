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
    pack_tables,
    fetch_data,
    expand_geoids,
    query_table_metadata,
    search_geos_by_point,
    search_geos_by_query,
    get_tabulation,
    show_col_builder,
    zip_data_with_geography,
)
from ..download_specified_data import check_table_requests
from .download_data import (
    prepare_csv_response,
    prepare_excel_response,
    prepare_shape_response,
    prepare_json_response,
    prepare_fake_geojson_response,
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
            with_geom=request.qwargs.geom,
            limit=limit,
            offset=offset,
            sumlevs=request.qwargs.sumlevs,
        )

    elif request.qwargs.q:
        result = search_geos_by_query(
            request.qwargs.q,
            db.session,
            with_geom=request.qwargs.geom,
            limit=limit,
            offset=offset,
            sumlevs=request.qwargs.sumlevs,
        )
    else:
        abort(400, "Must provide either a lat/lon OR a query term.")

    def prep_row(row, with_geom):
        result = {
            "full_geoid": row.full_geoid,
            "full_name": row.display_name,
            "geom": json.loads(row.geom),
            "sumlevel": row.sumlevel,
        }

        if with_geom:
            result["geom"] = json.loads(row.geom)

        return result

    return jsonify(
        results=[prep_row(row, request.qwargs.geom) for row in result]
    )


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
    db.session.execute(
        text("SET search_path TO :acs, public;"), {"acs": release}
    )
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
        release = {}
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
            table_record = convert_row_to_dict(table_record)
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

                release["results"] = acs_rowcount.count

        data[acs] = release

    json_string = json.dumps(data)
    resp = make_response(json_string)
    resp.headers.set("Content-Type", "application/json")

    return resp


def data_pull(table_ids, geoids, acs, db):
    max_geoids = current_app.config.get("MAX_GEOIDS_TO_SHOW", 1000)
    current_app.logger.warning(geoids)

    if acs not in ALLOWED_ACS:
        abort(404, f"The {acs} release isn't supported.")

    # valid_geo_ids only contains geos for which we want data
    try:
        valid_geo_ids, child_parent_map = expand_geoids(
            geoids, release=acs, db=db.session
        )
        current_app.logger.warning(valid_geo_ids)

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
        fetch_data(valid_table_ids, valid_geo_ids, acs, db.session),
    )


@app.route("/1.0/geo/show/<release>")
@qwarg_validate(
    {
        "geo_ids": {"valid": StringList(), "required": True},
    }
)
@crossdomain(origin="*")
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

    return prepare_fake_geojson_response(
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
    all_geoids, _ = expand_geoids(request.qwargs.geo_ids, acs, db.session)
    all_geoids = tuple(all_geoids)

    geo_metadata = get_geography_info(all_geoids, db.session)
    table_metadata = get_table_metadata(
        request.qwargs.table_ids, acs, db.session, include_columns=True
    )
    indicators = ic(
        fetch_data(request.qwargs.table_ids, all_geoids, acs, db.session)
    )

    return jsonify(
        {
            "data": {},
            "geography": {},
            "release": {"id": acs, **ACS_NAMES[acs]},
            "tables": {},
        }
    )


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
    if acs not in ALLOWED_ACS:
        abort(404, f"The {acs} release isn't supported.")

    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    table_metadata_rows = get_table_metadata(
        (table_id,), acs, db.session, include_columns=True
    )

    _, tables = group_tables(
        table_metadata_rows,
        col_strategy=show_col_builder,
        table_approach="medium",
    )

    parent = get_geography_info(
        (request.qwargs.within,),
        db.session,
        with_geom=request.qwargs.geom,
        fetchone=True,
    )

    child_list = tuple(
        row.geoid
        for row in get_child_geoids(
            acs, request.qwargs.within, request.qwargs.sumlevel, db.session
        )
    )

    children = get_geography_info(child_list, db.session, with_geom=True)

    parent_result = fetch_data(
        (table_id,), (parent.full_geoid,), acs, db.session
    )

    match parent_result:
        case Success(parent_data):
            packed_parent = pack_tables(
                parent_data[0], rename={"estimate": "data"}
            )

        case Failure(e):
            ic(e)
            abort(
                404, f"No data found for parent geoid {request.qwargs.within}."
            )

    child_result = fetch_data((table_id,), child_list, acs, db.session)

    match child_result:
        case Success(child_data):
            packed_children = {
                row.geoid: pack_tables(row, rename={"estimate": "data"})[
                    table_id
                ]
                for row in child_data
            }

            zipped_children = zip_data_with_geography(
                children,
                packed_children,
            )

        case Failure(e):
            ic(e)
            abort(
                404,
                f"No child data found for sumlevel {request.qwargs.sumlevel} "
                f"for parent {request.qwargs.within}.",
            )

    return jsonify(
        comparison={
            "child_geography_name": SUMLEV_NAMES.get(
                request.qwargs.sumlevel, {}
            ).get("name"),
            "child_geography_name_plural": SUMLEV_NAMES.get(
                request.qwargs.sumlevel, {}
            ).get("plural"),
            "child_summary_level": request.qwargs.sumlevel,
            "parent_geography_name": SUMLEV_NAMES.get(parent.sumlevel, {}).get(
                "name"
            ),
            "parent_geoid": parent.full_geoid,
            "parent_name": parent.display_name,
            "parent_summary_level": parent.sumlevel,
            "results": 276,
        },
        table={
            "census_release": ACS_NAMES.get(acs, {}).get("name"),
            **tables[table_id],
        },
        parent_geography={
            **packed_parent,
            "geography": {
                "name": parent.display_name,
                "summary_level": parent.sumlevel,
            },
        },
        child_geographies=zipped_children,
    )


@app.route("/custom")
def custom_calculation():
    pass



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
