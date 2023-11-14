# For real division instead of sometimes-integer
from __future__ import division

from dataclasses import dataclass
from flask import Flask
from flask import abort, request, g
from flask import make_response, current_app, send_file
from flask import jsonify, redirect
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from raven.contrib.flask import Sentry
from werkzeug.exceptions import HTTPException
from functools import update_wrapper
from itertools import groupby
import simplejson as json
from collections import OrderedDict
import operator
import math
from math import log10, log
from datetime import timedelta
import re
import os
import sys
import shutil
import tempfile
import zipfile
import pylibmc

# import mockcache
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from boto.exception import S3ResponseError
from census_extractomatic.validation import (
    qwarg_validate,
    NonemptyString,
    FloatRange,
    StringList,
    Bool,
    OneOf,
    ClientRequestValidationException,
)
import tomli

from census_extractomatic.exporters import supported_formats

with open('config.toml', 'rb') as f:
    config = tomli.load(f)


app = Flask(__name__)
app.config.from_object(
    os.environ.get(
        "EXTRACTOMATIC_CONFIG_MODULE", "census_extractomatic.config.Development"
    )
)
app.config["SECRET_KEY"] = config['sessions']['secret_key']

db = SQLAlchemy(app)
login = LoginManager(app)

# Set up metadata endpoint

from census_extractomatic.metadata_api.src import metadata_api
from census_extractomatic.metadata_api.admin import register_d3_metadata_admin
app.register_blueprint(metadata_api, url_prefix="/metadata")
register_d3_metadata_admin(app)

from census_extractomatic.auth import auth
app.register_blueprint(auth, url_prefix="/auth")


sentry = Sentry(app)

if not app.debug:
    import logging

    file_handler = logging.FileHandler(
        "/tmp/api.censusreporter.org.wsgi_error.log"
    )
    stream_handler = logging.StreamHandler(stream=sys.stderr)
    file_handler.setLevel(logging.WARNING)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(stream_handler)

try:
    app.s3 = S3Connection()
except Exception as e:
    app.s3 = None
    app.logger.warning("S3 Configuration failed.")


# Allowed ACS's in "best" order (newest and smallest range preferred)
allowed_acs = [
    "d3_present",
    "acs2021_5yr",
    "acs2019_5yr",
    "acs2018_5yr",
    "acs2017_5yr",
    "acs2016_5yr",
    "acs2014_5yr",
    "acs2013_5yr",
    "acs2012_5yr",
    "acs2011_5yr",
    "d3_past",
]
# When expanding a container geoid shorthand (i.e. 140|05000US12127),
# use this ACS. It should always be a 5yr release so as to include as
# many geos as possible.
release_to_expand_with = allowed_acs[1]
# When table searches happen without a specified release, use this
# release to do the table search.
default_table_search_release = allowed_acs[1]

# Allowed TIGER releases in newest order
allowed_tiger = [
    "tiger2021",
]

allowed_searches = ["table", "profile", "topic", "all"]

ACS_NAMES = {
    "acs2021_5yr": {"name": "ACS 2021 5-year", "years": "2017-2021"},
    "acs2019_5yr": {"name": "ACS 2019 5-year", "years": "2015-2019"},
    "acs2018_5yr": {"name": "ACS 2018 5-year", "years": "2014-2018"},
    "acs2017_5yr": {"name": "ACS 2017 5-year", "years": "2013-2017"},
    "acs2016_5yr": {"name": "ACS 2016 5-year", "years": "2012-2016"},
    "acs2014_5yr": {"name": "ACS 2014 5-year", "years": "2010-2014"},
    "acs2013_5yr": {"name": "ACS 2013 5-year", "years": "2009-2013"},
    "acs2012_5yr": {"name": "ACS 2012 5-year", "years": "2008-2012"},
    "acs2011_5yr": {"name": "ACS 2011 5-year", "years": "2007-2011"},
    "d3_present": {"name": "Data Driven Detroit", "years": "2018-2021"},
    "d3_past": {"name": "Data Driven Detroit", "years": "2015-2018"},
}

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

SUMLEV_NAMES = {
    "010": {"name": "nation", "plural": ""},
    "020": {"name": "region", "plural": "regions"},
    "030": {"name": "division", "plural": "divisions"},
    "040": {"name": "state", "plural": "states", "tiger_table": "state"},
    "050": {"name": "county", "plural": "counties", "tiger_table": "county"},
    "060": {
        "name": "county subdivision",
        "plural": "county subdivisions",
        "tiger_table": "cousub",
    },
    "101": {"name": "block", "plural": "blocks", "tiger_table": "tabblock"},
    "140": {
        "name": "census tract",
        "plural": "census tracts",
        "tiger_table": "tract",
    },
    "150": {
        "name": "block group",
        "plural": "block groups",
        "tiger_table": "bg",
    },
    "160": {"name": "place", "plural": "places", "tiger_table": "place"},
    "170": {
        "name": "consolidated city",
        "plural": "consolidated cities",
        "tiger_table": "concity",
    },
    "230": {
        "name": "Alaska native regional corporation",
        "plural": "Alaska native regional corporations",
        "tiger_table": "anrc",
    },
    "250": {
        "name": "native area",
        "plural": "native areas",
        "tiger_table": "aiannh250",
    },
    "251": {
        "name": "tribal subdivision",
        "plural": "tribal subdivisions",
        "tiger_table": "aits",
    },
    "252": {
        "name": "native area (reservation)",
        "plural": "native areas (reservation)",
        "tiger_table": "aiannh252",
    },
    "254": {
        "name": "native area (off-trust land)",
        "plural": "native areas (off-trust land)",
        "tiger_table": "aiannh254",
    },
    "256": {
        "name": "tribal census tract",
        "plural": "tribal census tracts",
        "tiger_table": "ttract",
    },
    "300": {"name": "MSA", "plural": "MSAs", "tiger_table": "metdiv"},
    "310": {"name": "CBSA", "plural": "CBSAs", "tiger_table": "cbsa"},
    "314": {
        "name": "metropolitan division",
        "plural": "metropolitan divisions",
        "tiger_table": "metdiv",
    },
    "330": {"name": "CSA", "plural": "CSAs", "tiger_table": "csa"},
    "335": {
        "name": "combined NECTA",
        "plural": "combined NECTAs",
        "tiger_table": "cnecta",
    },
    "350": {"name": "NECTA", "plural": "NECTAs", "tiger_table": "necta"},
    "364": {
        "name": "NECTA division",
        "plural": "NECTA divisions",
        "tiger_table": "nectadiv",
    },
    "400": {
        "name": "urban area",
        "plural": "urban areas",
        "tiger_table": "uac",
    },
    "500": {
        "name": "congressional district",
        "plural": "congressional districts",
        "tiger_table": "cd",
    },
    "610": {
        "name": "state senate district",
        "plural": "state senate districts",
        "tiger_table": "sldu",
    },
    "620": {
        "name": "state house district",
        "plural": "state house districts",
        "tiger_table": "sldl",
    },
    "795": {"name": "PUMA", "plural": "PUMAs", "tiger_table": "puma"},
    "850": {"name": "ZCTA3", "plural": "ZCTA3s"},
    "860": {"name": "ZCTA5", "plural": "ZCTA5s", "tiger_table": "zcta520"},
    "950": {
        "name": "elementary school district",
        "plural": "elementary school districts",
        "tiger_table": "elsd",
    },
    "960": {
        "name": "secondary school district",
        "plural": "secondary school districts",
        "tiger_table": "scsd",
    },
    "970": {
        "name": "unified school district",
        "plural": "unified school districts",
        "tiger_table": "unsd",
    },
}

state_fips = {
    "01": "Alabama",
    "02": "Alaska",
    "04": "Arizona",
    "05": "Arkansas",
    "06": "California",
    "08": "Colorado",
    "09": "Connecticut",
    "10": "Delaware",
    "11": "District of Columbia",
    "12": "Florida",
    "13": "Georgia",
    "15": "Hawaii",
    "16": "Idaho",
    "17": "Illinois",
    "18": "Indiana",
    "19": "Iowa",
    "20": "Kansas",
    "21": "Kentucky",
    "22": "Louisiana",
    "23": "Maine",
    "24": "Maryland",
    "25": "Massachusetts",
    "26": "Michigan",
    "27": "Minnesota",
    "28": "Mississippi",
    "29": "Missouri",
    "30": "Montana",
    "31": "Nebraska",
    "32": "Nevada",
    "33": "New Hampshire",
    "34": "New Jersey",
    "35": "New Mexico",
    "36": "New York",
    "37": "North Carolina",
    "38": "North Dakota",
    "39": "Ohio",
    "40": "Oklahoma",
    "41": "Oregon",
    "42": "Pennsylvania",
    "44": "Rhode Island",
    "45": "South Carolina",
    "46": "South Dakota",
    "47": "Tennessee",
    "48": "Texas",
    "49": "Utah",
    "50": "Vermont",
    "51": "Virginia",
    "53": "Washington",
    "54": "West Virginia",
    "55": "Wisconsin",
    "56": "Wyoming",
    "60": "American Samoa",
    "66": "Guam",
    "69": "Commonwealth of the Northern Mariana Islands",
    "72": "Puerto Rico",
    "78": "United States Virgin Islands",
}


def get_from_cache(cache_key, try_s3=True):
    # Try memcache first
    cached = g.cache.get(cache_key)

    if not cached and try_s3 and current_app.s3 is not None:
        # Try S3 next
        b = current_app.s3.get_bucket(
            "embed.censusreporter.org", validate=False
        )
        k = Key(b)
        k.key = cache_key
        try:
            cached = k.get_contents_as_string()
        except S3ResponseError:
            cached = None

        # TODO Should stick the S3 thing back in memcache

    return cached


def put_in_cache(
    cache_key,
    value,
    memcache=True,
    try_s3=True,
    content_type="application/json",
):
    if memcache:
        g.cache.set(cache_key, value)

    if try_s3 and current_app.s3 is not None:
        b = current_app.s3.get_bucket(
            "embed.censusreporter.org", validate=False
        )
        k = Key(b, cache_key)
        k.metadata["Content-Type"] = content_type
        k.set_contents_from_string(value, policy="public-read")


def crossdomain(
    origin=None,
    methods=None,
    headers=None,
    max_age=21600,
    attach_to_all=True,
    automatic_options=True,
):
    if methods is not None:
        methods = ", ".join(sorted(x.upper() for x in methods))
    if headers is not None and not isinstance(headers, str):
        headers = ", ".join(x.upper() for x in headers)
    if not isinstance(origin, str):
        origin = ", ".join(origin)
    if isinstance(max_age, timedelta):
        max_age = max_age.total_seconds()

    def get_methods():
        if methods is not None:
            return methods

        options_resp = current_app.make_default_options_response()
        return options_resp.headers["allow"]

    def decorator(f):
        def wrapped_function(*args, **kwargs):
            if automatic_options and request.method == "OPTIONS":
                resp = current_app.make_default_options_response()
            else:
                resp = make_response(f(*args, **kwargs))
            if not attach_to_all and request.method != "OPTIONS":
                return resp

            h = resp.headers

            h["Access-Control-Allow-Origin"] = origin
            h["Access-Control-Allow-Methods"] = get_methods()
            h["Access-Control-Max-Age"] = str(max_age)
            if headers is not None:
                h["Access-Control-Allow-Headers"] = headers
            return resp

        f.provide_automatic_options = False
        f.required_methods = ["OPTIONS"]
        return update_wrapper(wrapped_function, f)

    return decorator


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


def maybe_int(i):
    return int(i) if i else i


def percentify(val):
    return val * 100


def rateify(val):
    return val * 1000


def moe_add(moe_a, moe_b):
    # From http://www.census.gov/acs/www/Downloads/handbooks/ACSGeneralHandbook.pdf
    return math.sqrt(moe_a ** 2 + moe_b ** 2)


def moe_ratio(numerator, denominator, numerator_moe, denominator_moe):
    # From http://www.census.gov/acs/www/Downloads/handbooks/ACSGeneralHandbook.pdf
    estimated_ratio = numerator / denominator
    return (
        math.sqrt(
            numerator_moe ** 2 + (estimated_ratio ** 2 * denominator_moe ** 2)
        )
        / denominator
    )


ops = {
    "+": operator.add,
    "-": operator.sub,
    "/": operator.truediv,
    "%": percentify,
    "%%": rateify,
}
moe_ops = {
    "+": moe_add,
    "-": moe_add,
    "/": moe_ratio,
    "%": percentify,
    "%%": rateify,
}


def value_rpn_calc(data, rpn_string):
    stack = []
    moe_stack = []
    numerator = None
    numerator_moe = None

    for token in rpn_string.split():
        if token in ops:
            b = stack.pop()
            b_moe = moe_stack.pop()

            if token in ("%", "%%"):
                # Single-argument operators
                if b is None:
                    c = None
                    c_moe = None
                else:
                    c = ops[token](b)
                    c_moe = moe_ops[token](b_moe)
            else:
                a = stack.pop()
                a_moe = moe_stack.pop()

                if a is None or b is None:
                    c = None
                    c_moe = None
                elif token == "/":
                    # Broken out because MOE ratio needs both MOE and estimates

                    # We're dealing with ratios, not pure division.
                    if a == 0 or b == 0:
                        c = 0
                        c_moe = 0
                    else:
                        c = ops[token](a, b)
                        c_moe = moe_ratio(a, b, a_moe, b_moe)
                    numerator = a
                    numerator_moe = round(a_moe, 1)
                else:
                    c = ops[token](a, b)
                    c_moe = moe_ops[token](a_moe, b_moe)
        elif token.startswith("b"):
            c = data[token]
            c_moe = data[token + "_moe"]
        else:
            c = float(token)
            c_moe = float(token)
        stack.append(c)
        moe_stack.append(c_moe)

    value = stack.pop()
    error = moe_stack.pop()

    return (value, error, numerator, numerator_moe)


def build_item(name, data, parents, rpn_string):
    val = OrderedDict(
        [
            ("name", name),
            ("values", dict()),
            ("error", dict()),
            ("numerators", dict()),
            ("numerator_errors", dict()),
        ]
    )

    for parent in parents:
        label = parent["relation"]
        geoid = parent["geoid"]
        data_for_geoid = data.get(geoid) if data else {}

        value = None
        error = None
        numerator = None
        numerator_moe = None

        if data_for_geoid:
            (value, error, numerator, numerator_moe) = value_rpn_calc(
                data_for_geoid, rpn_string
            )

        # provide 2 decimals of precision, let client decide how much to use
        if value is not None:
            value = round(value, 2)
            error = round(error, 2)

        if numerator is not None:
            numerator = round(numerator, 2)
            numerator_moe = round(numerator_moe, 2)

        val["values"][label] = value
        val["error"][label] = error
        val["numerators"][label] = numerator
        val["numerator_errors"][label] = numerator_moe

    return val


def add_metadata(dictionary, table_id, universe, acs_release):
    val = dict(
        table_id=table_id,
        universe=universe,
        acs_release=acs_release,
    )

    dictionary["metadata"] = val


def find_geoid(geoid, acs=None):
    "Find the best acs to use for a given geoid or None if the geoid is not found."

    if acs:
        if acs not in allowed_acs:
            abort(404, "We don't have data for that release.")
        acs_to_search = [acs]
    else:
        acs_to_search = allowed_acs

    for acs in acs_to_search:

        result = db.session.execute(
            """SELECT geoid
               FROM %s.geoheader
               WHERE geoid=:geoid"""
            % acs,
            {"geoid": geoid},
        )
        if result.rowcount == 1:
            result = result.first()
            return (acs, result["geoid"])
    return (None, None)


@app.before_request
def before_request():
    memcache_addr = app.config.get("MEMCACHE_ADDR")
    g.cache = pylibmc.Client(
        memcache_addr
    )  # if memcache_addr else mockcache.Client(memcache_addr)


def get_data_fallback(table_ids, geoids, acs=None):
    if type(geoids) != list:
        geoids = [geoids]

    if type(table_ids) != list:
        table_ids = [table_ids]

    from_stmt = "%%(acs)s.%s_moe" % (table_ids[0])
    if len(table_ids) > 1:
        from_stmt += " "
        from_stmt += " ".join(
            [
                "JOIN %%(acs)s.%s_moe USING (geoid)" % (table_id)
                for table_id in table_ids[1:]
            ]
        )

    sql = "SELECT * FROM %s WHERE geoid IN :geoids;" % (from_stmt,)

    # if acs is specified, we'll use that one and not go searching for data.
    if acs in allowed_acs:
        sql = sql % {"acs": acs}
        result = db.session.execute(
            sql,
            {"geoids": tuple(geoids)},
        )
        data = {}
        for row in result.fetchall():
            row = dict(row)
            geoid = row.pop("geoid")
            data[geoid] = dict([(col, val) for (col, val) in row.iteritems()])

        return data, acs

    else:
        # otherwise we'll start at the best/most recent acs and move down til we have the data we want
        for acs in allowed_acs:
            sql = sql % {"acs": acs}
            result = db.session.execute(
                sql,
                {"geoids": tuple(geoids)},
            )
            data = {}
            for row in result.fetchall():
                row = dict(row)
                geoid = row.pop("geoid")
                data[geoid] = dict(
                    [(col, val) for (col, val) in row.iteritems()]
                )

            # Check to see if this release has our data
            data_with_values = filter(
                lambda geoid_data: geoid_data.values()[0] is not None,
                data.values(),
            )
            if len(geoids) == len(data) and len(geoids) == len(
                data_with_values
            ):
                return data, acs
            else:
                # Doesn't contain data for all geoids, so keep going.
                continue

    return None, acs


def special_case_parents(geoid, levels):
    """
    Update/adjust the parents list for special-cased geographies.
    """
    if geoid == "16000US1150000":
        # compare Washington, D.C., to "parent" state of VA,
        # rather than comparing to self as own parent state

        target = (
            index
            for (index, d) in enumerate(levels)
            if d["geoid"] == "04000US11"
        ).next()
        levels[target].update(
            {"coverage": 0, "display_name": "Virginia", "geoid": "04000US51"}
        )

    # Louisville is not in Census 160 data but 170 consolidated city is equivalent
    # we could try to convert 160 L-ville into 170, but that would overlap with
    # 050 Jefferson  which should already be in there so we'll just pluck it out.
    levels = [
        level for level in levels if not level["geoid"] == "16000US2148000"
    ]

    # remove US as a parent -- this geoid does not exist in the SDC API
    levels = [level for level in levels if not level["geoid"] == "01000US"]

    return levels


def compute_profile_item_levels(geoid):
    levels = []
    geoid_parts = []

    if geoid:
        geoid = geoid.upper()
        geoid_parts = geoid.split("US")

    if len(geoid_parts) != 2:
        raise Exception("Invalid geoid")

    levels.append(
        {
            "relation": "this",
            "geoid": geoid,
            "coverage": 100.0,
        }
    )

    sumlevel = geoid_parts[0][:3]
    id_part = geoid_parts[1]

    if sumlevel in (
        "140",
        "150",
        "060",
        "310",
        "330",
        "350",
        "860",
        "950",
        "960",
        "970",
    ):
        result = db.session.execute(
            text(
                """SELECT * FROM tiger2021.census_geo_containment
               WHERE child_geoid=:geoid
               ORDER BY percent_covered ASC;
            """),
            {'geoid': geoid},
        )

        for row in result:
            parent_sumlevel_name = SUMLEV_NAMES.get(row._mapping['parent_geoid'][:3])['name']
            if row._mapping['parent_geoid'][:7] not in {'01000US', '31000US'}:
                levels.append({
                    'relation': parent_sumlevel_name,
                    'geoid': row._mapping['parent_geoid'],
                    'coverage': row._mapping['percent_covered'],
                })

    if sumlevel in ('060', '140', '150'):
        levels.append({
            'relation': 'county',
            'geoid': '05000US' + id_part[:5],
            'coverage': 100.0,
        })

    if sumlevel in ('050', '060', '140', '150', '160', '500', '610', '620', '795', '950', '960', '970'):
        levels.append({
            'relation': 'state',
            'geoid': '04000US' + id_part[:2],
            'coverage': 100.0,
        })

    if sumlevel in ('314'):
        levels.append({
            'relation': 'CBSA',
            'geoid': '31000US' + id_part[:5],
            'coverage': 100.0,
            })

    # if sumlevel != '010':
    #     levels.append({
    #         'relation': 'nation',
    #         'geoid': '01000US',
    #         'coverage': 100.0,
    #     })

    levels = special_case_parents(geoid, levels)

    return levels


def get_acs_name(acs_slug):
    if acs_slug in ACS_NAMES:
        acs_name = ACS_NAMES[acs_slug]["name"]
    else:
        acs_name = acs_slug
    return acs_name


## GEO LOOKUPS ##


def convert_row(row):
    data = dict()
    data["sumlevel"] = row["sumlevel"]
    data["full_geoid"] = row["full_geoid"]
    data["full_name"] = row["display_name"]
    if "geom" in row and row["geom"]:
        data["geom"] = json.loads(row["geom"])
    return data


# Example: /1.0/geo/search?q=spok
# Example: /1.0/geo/search?q=spok&sumlevs=050,160
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
    lat = request.qwargs.lat
    lon = request.qwargs.lon
    q = request.qwargs.q
    sumlevs = request.qwargs.sumlevs
    with_geom = request.qwargs.geom

    if lat and lon:
        where = "ST_Intersects(geom, ST_SetSRID(ST_Point(:lon, :lat),4326))"
        where_args = {"lon": lon, "lat": lat}
    elif q:
        q = re.sub(r"[^a-zA-Z\,\.\-0-9]", " ", q)
        q = re.sub(r"\s+", " ", q)
        where = "lower(prefix_match_name) LIKE lower(:q)"
        q += "%"
        where_args = {"q": q}
    else:
        abort(400, "Must provide either a lat/lon OR a query term.")

    where += " AND lower(display_name) not like '%%not defined%%' "

    if sumlevs:
        where += " AND sumlevel IN :sumlevs"
        where_args["sumlevs"] = tuple(sumlevs)

    if with_geom:
        sql = text(
            """SELECT DISTINCT geoid,sumlevel,population,display_name,full_geoid,priority,ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,0.001), 5) as geom
            FROM tiger2021.census_name_lookup
            WHERE %s
            ORDER BY priority, population DESC NULLS LAST
            LIMIT 25;"""
            % (where)
        )
    else:
        sql = text(
            """SELECT DISTINCT geoid,sumlevel,population,display_name,full_geoid,priority
            FROM tiger2021.census_name_lookup
            WHERE %s
            ORDER BY priority, population DESC NULLS LAST
            LIMIT 25;"""
            % (where)
        )
    result = db.session.execute(sql, where_args)

    return jsonify(results=[convert_row(row._mapping) for row in result])


def num2deg(xtile, ytile, zoom):
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)


# Example: /1.0/geo/tiger2014/tiles/160/10/261/373.geojson
# Example: /1.0/geo/tiger2013/tiles/160/10/261/373.geojson
@app.route(
    "/1.0/geo/<release>/tiles/<sumlevel>/<int:zoom>/<int:x>/<int:y>.geojson"
)
@crossdomain(origin="*")
def geo_tiles(release, sumlevel, zoom, x, y):
    if release not in allowed_tiger:
        abort(404, "Unknown TIGER release")
    if sumlevel not in SUMLEV_NAMES:
        abort(404, "Unknown sumlevel")
    if sumlevel == "010":
        abort(400, "Don't support US tiles")

    (miny, minx) = num2deg(x, y, zoom)
    (maxy, maxx) = num2deg(x + 1, y + 1, zoom)

    tiles_across = 2 ** zoom
    deg_per_tile = 360.0 / tiles_across
    deg_per_pixel = deg_per_tile / 256
    tile_buffer = 10 * deg_per_pixel  # ~ 10 pixel buffer
    simplify_threshold = deg_per_pixel / 5

    result = db.session.execute(
        text(
            """SELECT
            ST_AsGeoJSON(ST_SimplifyPreserveTopology(
                ST_Intersection(ST_Buffer(ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326), %f, 'join=mitre'), geom),
                %f), 5) as geom,
            full_geoid,
            display_name
           FROM %s.census_name_lookup
           WHERE sumlevel=:sumlev AND ST_Intersects(ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326), geom)"""
            % (
                tile_buffer,
                simplify_threshold,
                release,
            )
        ),
        {
            "minx": minx,
            "miny": miny,
            "maxx": maxx,
            "maxy": maxy,
            "sumlev": sumlevel,
        },
    )

    results = []
    for row in result:
        results.append(
            {
                "type": "Feature",
                "properties": {
                    "geoid": row._mapping["full_geoid"],
                    "name": row._mapping["display_name"],
                },
                "geometry": json.loads(row._mapping["geom"])
                if row._mapping["geom"]
                else None,
            }
        )

    result = json.dumps(
        dict(type="FeatureCollection", features=results), separators=(",", ":")
    )

    resp = make_response(result)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day
    return resp


# Example: /1.0/geo/tiger2014/04000US53
# Example: /1.0/geo/tiger2013/04000US53
@app.route("/1.0/geo/<release>/<geoid>")
@qwarg_validate({"geom": {"valid": Bool(), "default": False}})
@crossdomain(origin="*")
def geo_lookup(release, geoid):
    if release not in allowed_tiger:
        abort(404, "Unknown TIGER release")

    geoid = geoid.upper() if geoid else geoid
    geoid_parts = geoid.split("US")
    if len(geoid_parts) != 2:
        abort(404, "Invalid GeoID")

    if request.qwargs.geom:
        result = db.session.execute(
            text(
                """SELECT display_name,simple_name,sumlevel,full_geoid,population,aland,awater,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00005), 6) as geom
               FROM %s.census_name_lookup
               WHERE full_geoid=:geoid
               LIMIT 1"""
                % (release,)
            ),
            {"geoid": geoid},
        )
    else:
        result = db.session.execute(
            text(
                """SELECT display_name,simple_name,sumlevel,full_geoid,population,aland,awater
               FROM %s.census_name_lookup
               WHERE full_geoid=:geoid
               LIMIT 1"""
                % (release,)
            ),
            {"geoid": geoid},
        )

    result = result.fetchone()

    if not result:
        abort(404, "Unknown GeoID")

    # Change result mapping to a vanilla dict
    result = dict(result._mapping.items())
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


# Example: /1.0/geo/tiger2014/04000US53/parents
# Example: /1.0/geo/tiger2013/04000US53/parents
@app.route("/1.0/geo/<release>/<geoid>/parents")
@crossdomain(origin="*")
def geo_parent(release, geoid):
    if release not in allowed_tiger:
        abort(404, "Unknown TIGER release")

    geoid = geoid.upper()

    # cache_key = str('%s/show/%s.parents.json' % (release, geoid))
    # cached = get_from_cache(cache_key)

    # if cached:
    #     resp = make_response(cached)
    # else:
    try:
        parents = compute_profile_item_levels(geoid)
    except Exception as e:
        abort(400, "Could not compute parents: " + e.message)
    parent_geoids = [p["geoid"] for p in parents if p["geoid"] != "01000US"]

    def build_item(p):
        return (
            p["full_geoid"],
            {
                "display_name": p["display_name"],
                "sumlevel": p["sumlevel"],
                "geoid": p["full_geoid"],
            },
        )

    if parent_geoids:
        result = db.session.execute(
            text(
                """SELECT display_name,sumlevel,full_geoid
                FROM %s.census_name_lookup
                WHERE full_geoid IN :geoids
                ORDER BY sumlevel DESC"""
                % (release,)
            ),
            {"geoids": tuple(parent_geoids)},
        )
        parent_list = dict(
            [
                build_item(p._mapping)
                for p in result
                if p._mapping["full_geoid"] != "01000US"
            ]
        )

        for parent in parents:
            if parent["geoid"] != "01000US":
                parent.update(parent_list.get(parent["geoid"], {}))

    result = json.dumps(dict(parents=parents))

    resp = make_response(result)
    # put_in_cache(cache_key, result)

    resp.headers.set("Content-Type", "application/json")
    # resp.headers.set('Cache-Control', 'public,max-age=%d' % int(3600*4))

    return resp


# Example: /1.0/geo/show/tiger2014?geo_ids=04000US55,04000US56
# Example: /1.0/geo/show/tiger2014?geo_ids=160|04000US17,04000US56
@app.route("/1.0/geo/show/<release>")
@qwarg_validate(
    {
        "geo_ids": {"valid": StringList(), "required": True},
    }
)
@crossdomain(origin="*")
def show_specified_geo_data(release):

    if release not in allowed_tiger:
        abort(404, "Unknown TIGER release")
    geo_ids, child_parent_map = expand_geoids(
        request.qwargs.geo_ids, release_to_expand_with
    )

    max_geoids = current_app.config.get("MAX_GEOIDS_TO_SHOW", 3000)
    if len(geo_ids) > max_geoids:
        abort(
            400,
            "You requested %s geoids. The maximum is %s. Please contact us for bulk data."
            % (len(geo_ids), max_geoids),
        )

    result = db.session.execute(
        text(
            """SELECT full_geoid,
            display_name,
            aland,
            awater,
            population,
            ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,ST_Perimeter(geom) / 2500)) as geom
           FROM %s.census_name_lookup
           WHERE geom is not null and full_geoid IN :geoids;"""
            % (release,)
        ),
        {"geoids": tuple(geo_ids)},
    )

    results = []
    valid_geo_ids = []
    for row in result:
        row = row._mapping
        valid_geo_ids.append(row["full_geoid"])
        results.append(
            {
                "type": "Feature",
                "properties": {
                    "geoid": row["full_geoid"],
                    "name": row["display_name"],
                    "aland": row["aland"],
                    "awater": row["awater"],
                    "2013_population_estimate": row["population"],
                },
                "geometry": json.loads(row["geom"]),
            }
        )

    invalid_geo_ids = set(geo_ids) - set(valid_geo_ids)
    if invalid_geo_ids:
        abort(404, "GeoID(s) %s are not valid." % (",".join(invalid_geo_ids)))

    resp_data = json.dumps({"type": "FeatureCollection", "features": results})

    resp = make_response(resp_data)
    resp.headers["Content-Type"] = "application/json"
    return resp


## TABLE LOOKUPS ##


def format_table_search_result(obj, obj_type, release):
    """internal util for formatting each object in `table_search` API response"""

    result = {
        "type": obj_type,
        "table_id": obj["table_id"],
        "table_name": obj["table_title"],
        "simple_table_name": obj["simple_table_title"],
        "topics": obj["topics"],
        "universe": obj["universe"],
        "release": release,
    }

    if obj_type == "table":
        result.update(
            {
                "id": obj["table_id"],
                "unique_key": obj["table_id"],
            }
        )
    elif obj_type == "column":
        result.update(
            {
                "id": obj["column_id"],
                "unique_key": "%s|%s" % (obj["table_id"], obj["column_id"]),
                "column_id": obj["column_id"],
                "column_name": obj["column_title"],
            }
        )

    return result



# Example: /1.0/table/search?q=norweg
# Example: /1.0/table/search?q=norweg&topics=age,sex
# Example: /1.0/table/search?topics=housing,poverty
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
    # allow choice of release, default to allowed_acs[0]
    acs = request.qwargs.acs
    q = request.qwargs.q
    topics = request.qwargs.topics

    if not (q or topics):
        abort(400, "Must provide a query term or topics for filtering.")

    data = []

    if re.match(r"^\w\d{2,}$", q, flags=re.IGNORECASE):
        # we need to search 'em all because not every table is in every release...
        # might be better to have a shared table like census_tabulation_metadata?
        table_id_acs = acs
        acs_to_search = allowed_acs[:]
        acs_to_search.remove(table_id_acs)
        ids_found = set()
        while table_id_acs:
            # Matching for table id
            db.session.execute(
                text("SET search_path TO :acs, public;"), {"acs": table_id_acs}
            )
            result = db.session.execute(
                text(
                    """SELECT tab.table_id,
                          tab.table_title,
                          tab.simple_table_title,
                          tab.universe,
                          tab.topics
                   FROM census_table_metadata tab
                   WHERE lower(table_id) like lower(:table_id)"""
                ),
                {"table_id": "{}%".format(q)},
            )
            for row in result:
                row = row._mapping
                if row["table_id"] not in ids_found:
                    data.append(
                        format_table_search_result(row, "table", table_id_acs)
                    )
                    ids_found.add(row["table_id"])
            try:
                table_id_acs = acs_to_search.pop(0)
            except IndexError:
                table_id_acs = None
        if data:
            data.sort(key=lambda x: x["unique_key"])
            return json.dumps(data)

    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})
    table_where_parts = []
    table_where_args = {}
    column_where_parts = []
    column_where_args = {}

    if q and q != "*":
        q = "%%%s%%" % q
        table_where_parts.append("lower(tab.table_title) LIKE lower(:query)")
        table_where_args["query"] = q
        column_where_parts.append("lower(col.column_title) LIKE lower(:query)")
        column_where_args["query"] = q

    if topics:
        table_where_parts.append("tab.topics @> :topics")
        table_where_args["topics"] = topics
        column_where_parts.append("tab.topics @> :topics")
        column_where_args["topics"] = topics

    if table_where_parts:
        table_where = " AND ".join(table_where_parts)
        column_where = " AND ".join(column_where_parts)
    else:
        table_where = "TRUE"
        column_where = "TRUE"

    # retrieve matching tables.
    result = db.session.execute(
        text(
            """SELECT tab.tabulation_code,
                  tab.table_title,
                  tab.simple_table_title,
                  tab.universe,
                  tab.topics,
                  tab.tables_in_one_yr,
                  tab.tables_in_three_yr,
                  tab.tables_in_five_yr
           FROM census_tabulation_metadata tab
           WHERE %s
           ORDER BY tab.weight DESC"""
            % (table_where)
        ),
        table_where_args,
    )
    for tabulation in result:
        tabulation = dict(tabulation)
        for tables_for_release_col in (
            "tables_in_one_yr",
            "tables_in_three_yr",
            "tables_in_five_yr",
        ):
            if tabulation[tables_for_release_col]:
                tabulation["table_id"] = tabulation[tables_for_release_col][0]
            else:
                continue
            break
        data.append(format_table_search_result(tabulation, "table", acs))

    # retrieve matching columns.
    if q != "*":
        # Special case for when we want ALL the tables (but not all the columns)
        result = db.session.execute(
            text(
                """SELECT col.column_id,
                      col.column_title,
                      tab.table_id,
                      tab.table_title,
                      tab.simple_table_title,
                      tab.universe,
                      tab.topics
               FROM census_column_metadata col
               LEFT OUTER JOIN census_table_metadata tab USING (table_id)
               WHERE %s
               ORDER BY char_length(tab.table_id), tab.table_id"""
                % (column_where)
            ),
            column_where_args,
        )
        data.extend(
            [
                format_table_search_result(column._mapping, "column", "")
                for column in result
            ]
        )

    json_string = json.dumps(data)
    resp = make_response(json_string)
    resp.headers.set("Content-Type", "application/json")

    return resp




# Example: /1.0/tabulation/01001
@app.route("/1.0/tabulation/<tabulation_id>")
@crossdomain(origin="*")
def tabulation_details(tabulation_id):
    result = db.session.execute(
        text(
            """SELECT *
           FROM census_tabulation_metadata
           WHERE tabulation_code=:tabulation"""
        ),
        {"tabulation": tabulation_id},
    )

    row = result.fetchone()

    if not row:
        abort(404, "Tabulation %s not found." % tabulation_id)

    row = dict(row._mapping)

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


# Example: /1.0/table/B28001?release=acs2013_1yr
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
    release = request.qwargs.acs

    table_id = table_id.upper() if table_id else table_id
    db.session.execute(
        text("SET search_path TO :acs, public;"), {"acs": request.qwargs.acs}
    )

    result = db.session.execute(
        text(
            """SELECT *
           FROM census_table_metadata tab
           WHERE table_id=:table_id"""
        ),
        {"table_id": table_id},
    )
    row = result.fetchone()

    if not row:
        abort(
            404,
            "Table %s not found in release %s. Try specifying another release."
            % (table_id.upper(), release),
        )

    row = row._mapping

    data = OrderedDict(
        [
            ("table_id", row["table_id"]),
            ("table_title", row["table_title"]),
            ("simple_table_title", row["simple_table_title"]),
            ("subject_area", row["subject_area"]),
            ("universe", row["universe"]),
            ("denominator_column_id", row["denominator_column_id"]),
            ("topics", row["topics"]),
        ]
    )

    result = db.session.execute(
        text(
            """SELECT *
           FROM census_column_metadata
           WHERE table_id=:table_id"""
        ),
        {"table_id": row["table_id"]},
    )

    rows = []
    for row in result:
        row = row._mapping
        rows.append(
            (
                row["column_id"],
                dict(
                    column_title=row["column_title"],
                    indent=row["indent"],
                    parent_column_id=row["parent_column_id"],
                ),
            )
        )

    data["columns"] = dict(rows)

    result = json.dumps(data)

    resp = make_response(result)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=%d" % int(3600 * 4))

    return resp


# Example: /2.0/table/latest/B28001
@app.route("/2.0/table/<release>/<table_id>")
@crossdomain(origin="*")
def table_details_with_release(release, table_id):
    if release in allowed_acs:
        acs_to_try = [release]
    elif release == "latest":
        acs_to_try = list(allowed_acs)
    else:
        abort(404, "The %s release isn't supported." % get_acs_name(release))

    table_id = table_id.upper() if table_id else table_id

    for release in acs_to_try:
        db.session.execute(
            text("SET search_path TO :acs, public;"), {"acs": release}
        )

        result = db.session.execute(
            text(
                """SELECT *
               FROM census_table_metadata tab
               WHERE table_id=:table_id"""
            ),
            {"table_id": table_id},
        )
        row = result.fetchone()

        if not row:
            continue
        else:
            row = row._mapping

        data = dict(
            [
                ("table_id", row["table_id"]),
                ("table_title", row["table_title"]),
                ("simple_table_title", row["simple_table_title"]),
                ("subject_area", row["subject_area"]),
                ("universe", row["universe"]),
                ("denominator_column_id", row["denominator_column_id"]),
                ("topics", row["topics"]),
            ]
        )

        result = db.session.execute(
            text(
                """SELECT *
               FROM census_column_metadata
               WHERE table_id=:table_id
               ORDER By line_number"""
            ),
            {"table_id": row["table_id"]},
        )

        rows = []
        for row in result:
            row = row._mapping
            rows.append(
                (
                    row["column_id"],
                    dict(
                        column_title=row["column_title"],
                        indent=row["indent"],
                        parent_column_id=row["parent_column_id"],
                    ),
                )
            )

        data["columns"] = OrderedDict(rows)

        result = json.dumps(data)

        resp = make_response(result)

        resp.headers.set("Content-Type", "application/json")
        resp.headers.set("Cache-Control", "public,max-age=%d" % int(3600 * 4))

        return resp

    abort(
        404,
        "Table %s not found in releases %s. Try specifying another release."
        % (table_id, ", ".join(acs_to_try)),
    )


# Example: /1.0/table/compare/rowcounts/B01001?year=2011&sumlevel=050&within=04000US53
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
    years = request.qwargs.year.split(",")
    child_summary_level = request.qwargs.sumlevel
    parent_geoid = request.qwargs.within

    data = OrderedDict()

    releases = []
    for year in years:
        releases += [name for name in allowed_acs if year in name]
    releases = sorted(releases)

    for acs in releases:
        db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})
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
                acs, parent_geoid, child_summary_level
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


## COMBINED LOOKUPS ##

## DATA RETRIEVAL ##

# get geoheader data for children at the requested summary level
def get_child_geoids(release, parent_geoid, child_summary_level):
    parent_sumlevel = parent_geoid[0:3]
    if parent_sumlevel == "010":
        return get_all_child_geoids(release, child_summary_level)
    elif (
        parent_sumlevel in PARENT_CHILD_CONTAINMENT
        and child_summary_level in PARENT_CHILD_CONTAINMENT[parent_sumlevel]
    ):
        return get_child_geoids_by_prefix(
            release, parent_geoid, child_summary_level
        )
    elif parent_sumlevel == "160" and child_summary_level in ("140", "150"):
        return get_child_geoids_by_coverage(
            release, parent_geoid, child_summary_level
        )
    elif parent_sumlevel == "310" and child_summary_level in ("160", "860"):
        return get_child_geoids_by_coverage(
            release, parent_geoid, child_summary_level
        )
    elif parent_sumlevel == "040" and child_summary_level in ("310", "860"):
        return get_child_geoids_by_coverage(
            release, parent_geoid, child_summary_level
        )
    elif parent_sumlevel == "050" and child_summary_level in (
        "160",
        "860",
        "950",
        "960",
        "970",
    ):
        return get_child_geoids_by_coverage(
            release, parent_geoid, child_summary_level
        )
    else:
        return get_child_geoids_by_gis(
            release, parent_geoid, child_summary_level
        )


def get_all_child_geoids(release, child_summary_level):
    db.session.execute(text("SET search_path TO :acs,public;"), {"acs": release})
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


def get_child_geoids_by_coverage(release, parent_geoid, child_summary_level):
    # Use the "worst"/biggest ACS to find all child geoids
    db.session.execute(text("SET search_path TO :acs,public;"), {"acs": release})
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


def get_child_geoids_by_gis(release, parent_geoid, child_summary_level):
    parent_sumlevel = parent_geoid[0:3]
    child_geoids = []
    result = db.session.execute(
        text("""
            SELECT child_geoid
            FROM tiger2021.census_geo_containment parent
            WHERE parent_geoid = :parent_geoid
            AND child_geoid LIKE :child_sumlevel
            AND percent_covered > 10;
         """),
        {
            "child_sumlevel": child_summary_level + '%',
            "parent_geoid": parent_geoid,
        },
    )
    child_geoids = [r._mapping["full_geoid"] for r in result]

    if child_geoids:
        # Use the "worst"/biggest ACS to find all child geoids
        db.session.execute(
            text("SET search_path TO :acs,public;"), {"acs": release}
        )
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


def get_child_geoids_by_prefix(release, parent_geoid, child_summary_level):
    child_geoid_prefix = "%s00US%s%%" % (
        child_summary_level,
        parent_geoid.upper().split("US")[1],
    )

    # Use the "worst"/biggest ACS to find all child geoids
    db.session.execute(text("SET search_path TO :acs,public;"), {"acs": release})
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


def expand_geoids(geoid_list, release=None):
    if not release:
        release = expand_geoids_with

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
                child_geoid._mapping["geoid"]
                for child_geoid in get_child_geoids(
                    release, parent_geoid, child_summary_level
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


class ShowDataException(Exception):
    pass


# Example: /1.0/data/show/acs2012_5yr?table_ids=B01001,B01003&geo_ids=04000US55,04000US56
# Example: /1.0/data/show/latest?table_ids=B01001&geo_ids=160|04000US17,04000US56
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

    if acs in allowed_acs:
        acs_to_try = [acs]
        expand_geoids_with = acs
    elif acs == "latest":
        acs_to_try = allowed_acs[:3]  # The first three releases
        expand_geoids_with = release_to_expand_with
    else:
        abort(404, "The %s release isn't supported." % get_acs_name(acs))

    # valid_geo_ids only contains geos for which we want data
    requested_geo_ids = request.qwargs.geo_ids
    try:
        valid_geo_ids, child_parent_map = expand_geoids(
            requested_geo_ids, release=expand_geoids_with
        )
    except ShowDataException as e:
        abort(400, str(e))

    if not valid_geo_ids:
        abort(
            404,
            "None of the geo_ids specified were valid: %s"
            % ", ".join(requested_geo_ids),
        )

    max_geoids = current_app.config.get("MAX_GEOIDS_TO_SHOW", 1000)
    if len(valid_geo_ids) > max_geoids:
        abort(
            400,
            "You requested %s geoids. The maximum is %s. Please contact us for bulk data."
            % (len(valid_geo_ids), max_geoids),
        )

    # expand_geoids has validated parents of groups by getting children;
    # this will include those parent names in the reponse `geography` list
    # but leave them out of the response `data` list
    grouped_geo_ids = [item for item in requested_geo_ids if "|" in item]
    parents_of_groups = set(
        [item_group.split("|")[1] for item_group in grouped_geo_ids]
    )
    named_geo_ids = valid_geo_ids | parents_of_groups

    # Fill in the display name for the geos
    try:
        result = db.session.execute(
            text(
                """SELECT full_geoid,population,display_name
               FROM tiger2021.census_name_lookup
               WHERE full_geoid IN :geoids;"""
            ),
            {"geoids": tuple(named_geo_ids)},
        )
    except Exception as e:
        print(e)

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

    for acs in acs_to_try:
        try:
            db.session.execute(
                text("SET search_path TO :acs, public;"), {"acs": acs}
            )
            # Check to make sure the tables requested are valid
            try:
                result = db.session.execute(
                    text(
                        """SELECT tab.table_id,          -- 0
                              tab.table_title,            -- 1
                              tab.universe,               -- 2
                              tab.denominator_column_id,  -- 3
                              col.column_id,              -- 4
                              col.column_title,           -- 5
                              col.indent                  -- 6
                       FROM census_column_metadata col
                       LEFT JOIN census_table_metadata tab USING (table_id)
                       WHERE table_id IN :table_ids
                       ORDER BY tab.table_id, col.column_id;"""
                    ),
                    {"table_ids": tuple(request.qwargs.table_ids)},
                )
            except Exception as e:
                print(e)

            valid_table_ids = []
            table_metadata = OrderedDict()

            for table, columns in groupby(
                result, lambda col: col[:4]
            ):  # groupby table_id
                valid_table_ids.append(table[0])
                table_metadata[table[0]] = {
                    "title": table[1],
                    "universe": table[2],
                    "denominator_column_id": table[3],
                    "columns": {
                        column[4]: {
                            "name": column[5],
                            "indent": column[6],
                        }
                        for column in columns
                    },
                }

            invalid_table_ids = set(request.qwargs.table_ids) - set(
                valid_table_ids
            )
            if invalid_table_ids:
                raise ShowDataException(
                    "The %s release doesn't include table(s) %s."
                    % (get_acs_name(acs), ",".join(invalid_table_ids))
                )

            # Now fetch the actual data
            from_stmt = "%s_moe" % (valid_table_ids[0])

            if len(valid_table_ids) > 1:
                from_stmt += " "
                from_stmt += " ".join(
                    [
                        "JOIN %s_moe USING (geoid)" % (table_id)
                        for table_id in valid_table_ids[1:]
                    ]
                )

            sql = text(
                "SELECT * FROM %s WHERE geoid IN :geoids;" % (from_stmt,)
            )

            resp = db.session.execute(sql, {"geoids": tuple(valid_geo_ids)})
            keys = resp.keys()
            result = [
                {key: value for value, key in zip(row, keys) if key != "index"}
                for row in resp
            ]
            data = {}

            # if result.rowcount != len(valid_geo_ids):
            #     returned_geo_ids = set([row['geoid'] for row in result])
            #     raise ShowDataException("The %s release doesn't include GeoID(s) %s." % (get_acs_name(acs), ','.join(set(valid_geo_ids) - returned_geo_ids)))

            for row in result:
                row = dict(row)
                geoid = row.pop("geoid")
                data_for_geoid = {}

                # If we end up at the 'most complete' release, we should include every bit of
                # data we can instead of erroring out on the user.
                # See https://www.pivotaltracker.com/story/show/70906084
                this_geo_has_data = False or acs == allowed_acs[-1]

                cols_iter = iter(
                    sorted(list(row.items()), key=lambda tup: tup[0])
                )
                for table_id, data_iter in groupby(
                    cols_iter, lambda x: x[0][:-3].upper()
                ):
                    table_for_geoid = OrderedDict()
                    table_for_geoid["estimate"] = OrderedDict()
                    table_for_geoid["error"] = OrderedDict()

                    for (col_name, value) in data_iter:
                        col_name = col_name.upper()
                        (moe_name, moe_value) = next(cols_iter)

                        if value is not None and moe_value is not None:
                            this_geo_has_data = True

                        table_for_geoid["estimate"][col_name] = value
                        table_for_geoid["error"][col_name] = moe_value

                    data_for_geoid[table_id] = table_for_geoid

                data[geoid] = data_for_geoid

            resp_data = json.dumps(
                {
                    "tables": table_metadata,
                    "geography": geo_metadata,
                    "data": data,
                    "release": {
                        "id": acs,
                        "years": ACS_NAMES[acs]["years"],
                        "name": ACS_NAMES[acs]["name"],
                    },
                }
            )
            resp = make_response(resp_data)
            resp.headers["Content-Type"] = "application/json"
            return resp
        except Exception as e:
            abort(400, str(e))
    abort(400, "Unspecified error.")


# Example: /1.0/data/download/acs2012_5yr?format=shp&table_ids=B01001,B01003&geo_ids=04000US55,04000US56
# Example: /1.0/data/download/latest?table_ids=B01001&geo_ids=160|04000US17,04000US56
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
    if acs in allowed_acs:
        acs_to_try = [acs]
        expand_geoids_with = acs
    elif acs == "latest":
        acs_to_try = allowed_acs[:3]  # The first three releases
        expand_geoids_with = release_to_expand_with
    else:
        app.logger.error(f"ACS release {acs} isn't supported")
        abort(404, "The %s release isn't supported." % get_acs_name(acs))

    try:
        valid_geo_ids, _ = expand_geoids(
            request.qwargs.geo_ids, release=expand_geoids_with
        )
    except ShowDataException as e:
        abort(400, e.message)

    max_geoids = current_app.config.get("MAX_GEOIDS_TO_DOWNLOAD", 1000)
    if len(valid_geo_ids) > max_geoids:
        abort(
            400,
            "You requested %s geoids. The maximum is %s. Please contact us for bulk data."
            % (len(valid_geo_ids), max_geoids),
        )

    # Fill in the display name for the geos
    try:
        result = db.session.execute(
            text(
                """SELECT full_geoid,
                      population,
                      display_name
               FROM tiger2021.census_name_lookup
               WHERE full_geoid IN :geo_ids;"""
            ),
            {"geo_ids": tuple(valid_geo_ids)},
        )

    except Exception as e:
        app.logger.error(f"The query is failing with error {e}.")
        abort(400, "Query error on geography query.")

    geo_metadata = OrderedDict()
    try:
        for geo in result:
            geo_metadata[geo._mapping["full_geoid"]] = {
                "name": geo._mapping["display_name"],
            }

    except KeyError as e:
        app.logger.error(f"Uable to get key from geo call, {e}.")
        abort(400, "Key error on geography query.")

    for acs in acs_to_try:
        try:
            db.session.execute(
                text("SET search_path TO :acs, public;"), {"acs": acs}
            )

            # Check to make sure the tables requested are valid
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
                {"table_ids": tuple(request.qwargs.table_ids)},
            )

            valid_table_ids = []
            table_metadata = OrderedDict()
            for table, columns in groupby(
                result,
                lambda x: (
                    x._mapping["table_id"],
                    x._mapping["table_title"],
                    x._mapping["universe"],
                    x._mapping["denominator_column_id"],
                ),
            ):
                valid_table_ids.append(table[0])
                table_metadata[table[0]] = dict(
                    [
                        ("title", table[1]),
                        ("universe", table[2]),
                        ("denominator_column_id", table[3]),
                        (
                            "columns",
                            dict(
                                [
                                    (
                                        column._mapping["column_id"],
                                        OrderedDict(
                                            [
                                                (
                                                    "name",
                                                    column._mapping[
                                                        "column_title"
                                                    ],
                                                ),
                                                (
                                                    "indent",
                                                    column._mapping["indent"],
                                                ),
                                            ]
                                        ),
                                    )
                                    for column in columns
                                ]
                            ),
                        ),
                    ]
                )

            invalid_table_ids = set(request.qwargs.table_ids) - set(
                valid_table_ids
            )
            if invalid_table_ids:
                raise ShowDataException(
                    "The %s release doesn't include table(s) %s."
                    % (get_acs_name(acs), ",".join(invalid_table_ids))
                )

            # Now fetch the actual data
            from_stmt = "%s_moe" % (valid_table_ids[0])
            if len(valid_table_ids) > 1:
                from_stmt += " "
                from_stmt += " ".join(
                    [
                        "JOIN %s_moe USING (geoid)" % (table_id)
                        for table_id in valid_table_ids[1:]
                    ]
                )

            sql = text(
                "SELECT * FROM %s WHERE geoid IN :geo_ids;" % (from_stmt,)
            )

            result = db.session.execute(sql, {"geo_ids": tuple(valid_geo_ids)})
            data = OrderedDict()

            if result.rowcount != len(valid_geo_ids):
                returned_geo_ids = {row._mapping["geoid"] for row in result}
                raise ShowDataException(
                    "The %s release doesn't include GeoID(s) %s."
                    % (
                        get_acs_name(acs),
                        ",".join(set(valid_geo_ids) - returned_geo_ids),
                    )
                )

            for row in result.fetchall():
                row = dict(row._mapping)
                geoid = row.pop("geoid")
                data_for_geoid = OrderedDict()

                cols_iter = iter(sorted(row.items(), key=lambda tup: tup[0]))

                for table_id, data_iter in groupby(
                    cols_iter, lambda x: x[0][:-3].upper()
                ):
                    table_for_geoid = OrderedDict()
                    table_for_geoid["estimate"] = OrderedDict()
                    table_for_geoid["error"] = OrderedDict()

                    data_iter = list(data_iter)

                    # The variables and moes are arranged consecutively, so use zip 
                    # and slice notation to iterate over two cols at a time.
                    for (col_name, value), (_, moe_value) in zip(
                        data_iter[:-1:2], data_iter[1::2]
                    ):
                        col_name = col_name.upper()

                        table_for_geoid["estimate"][col_name] = value
                        table_for_geoid["error"][col_name] = moe_value

                    data_for_geoid[table_id] = table_for_geoid

                data[geoid] = data_for_geoid

            temp_path = tempfile.mkdtemp()
            file_ident = "%s_%s_%s" % (
                acs,
                next(iter(valid_table_ids)),
                next(iter(valid_geo_ids)),
            )
            inner_path = os.path.join(temp_path, file_ident)
            os.mkdir(inner_path)
            out_filename = os.path.join(
                inner_path, "%s.%s" % (file_ident, request.qwargs.format)
            )
            format_info = supported_formats.get(request.qwargs.format)
            builder_func = format_info["function"]
            builder_func(
                app.config["SQLALCHEMY_DATABASE_URI"],
                data,
                table_metadata,
                valid_geo_ids,
                file_ident,
                out_filename,
                request.qwargs.format,
                logger=app.logger,
            )

            metadata_dict = {
                "release": {
                    "id": acs,
                    "years": ACS_NAMES[acs]["years"],
                    "name": ACS_NAMES[acs]["name"],
                },
                "tables": table_metadata,
            }
            json.dump(
                metadata_dict,
                open(os.path.join(inner_path, "metadata.json"), "w"),
                indent=4,
            )

            zfile_path = os.path.join(temp_path, file_ident + ".zip")
            zfile = zipfile.ZipFile(zfile_path, "w", zipfile.ZIP_DEFLATED)
            for root, dirs, files in os.walk(inner_path):
                for f in files:
                    zfile.write(
                        os.path.join(root, f), os.path.join(file_ident, f)
                    )
            zfile.close()

            resp = send_file(
                zfile_path,
                as_attachment=True,
                download_name=file_ident + ".zip",
            )

            shutil.rmtree(temp_path)

            return resp
        except ShowDataException as e:
            continue
    abort(400, "Unspecified error.")


# Example: /1.0/data/compare/acs2012_5yr/B01001?sumlevel=050&within=04000US53
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
    if acs not in allowed_acs:
        abort(404, "The %s release isn't supported." % get_acs_name(acs))
    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    parent_geoid = request.qwargs.within
    child_summary_level = request.qwargs.sumlevel

    # create the containers we need for our response
    comparison = OrderedDict()
    table = OrderedDict()
    parent_geography = OrderedDict()
    child_geographies = OrderedDict()

    # add some basic metadata about the comparison and data table requested.
    comparison["child_summary_level"] = child_summary_level
    comparison["child_geography_name"] = SUMLEV_NAMES.get(
        child_summary_level, {}
    ).get("name")
    comparison["child_geography_name_plural"] = SUMLEV_NAMES.get(
        child_summary_level, {}
    ).get("plural")

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
           WHERE table_id=:table_ids
           ORDER BY column_id;"""
        ),
        {"table_ids": table_id},
    )
    table_metadata = result.fetchall()

    if not table_metadata:
        abort(
            404,
            "Table %s isn't available in the %s release."
            % (table_id.upper(), get_acs_name(acs)),
        )

    validated_table_id = table_metadata[0]._mapping["table_id"]

    # get the basic table record, and add a map of columnID -> column name
    table_record = table_metadata[0]._mapping
    column_map = OrderedDict()
    for record in table_metadata:
        record = record._mapping
        if record["column_id"]:
            column_map[record["column_id"]] = OrderedDict()
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

    child_geoheaders = get_child_geoids(acs, parent_geoid, child_summary_level)

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
            {"geo_ids": tuple(child_geoid_list)},
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
