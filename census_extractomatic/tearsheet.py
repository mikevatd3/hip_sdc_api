from urllib.parse import quote, unquote
import re
from itertools import groupby

from flask import render_template, request, jsonify, Blueprint, current_app
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from psycopg2.errors import UndefinedTable
import tomli

from lesp.analyze import extract_variables
from census_extractomatic._api.download_data import pack_geojson_response

from .access import Geography, Indicator, Tearsheet


tearsheet = Blueprint("tearsheet", __name__)

CORS(tearsheet)


RECIPES = {
    ":population": "B01001001",
    ":pop_density": "(/ B01001001 (/ land_area 2589988))",
    ":housing_units": "B25001001",
}


with open("config.toml", "rb") as f:
    conf = tomli.load(f)


BASE_URL = "https://sdcapi.datadrivendetroit.org/tearsheet/sheet"


HOST, DBNAME, USERNAME, PASSWORD, PORT = conf["db"].values()

connection_string = (
    f"postgresql+psycopg2://{USERNAME}:{quote(PASSWORD)}@{HOST}:{PORT}/{DBNAME}"
)

db_engine = create_engine(connection_string)


@tearsheet.route("/")
def index():
    return render_template("index.html")


@tearsheet.route("/sheet", methods=["GET", "POST"])
def sheet():
    if request.method == "POST":
        geographies = (
            request.form.get("geographies", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )
        indicators = (
            request.form.get("indicators", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )
        release = request.form.get("release", "acs2022_5yr")
        how = request.form.get("how")

    else:
        geographies = (
            unquote(request.args.get("geographies", ""))
            .strip()
            .replace(", ", ",")
            .split(",")
        )
        indicators = (
            unquote(request.args.get("indicators", ""))
            .strip()
            .replace(", ", ",")
            .split(",")
        )
        release = unquote(request.args.get("release", "acs2022_5yr"))
        how = request.args.get("how")

    url = f"sheet?geographies={quote(','.join(geographies))}&indicators={quote(','.join(indicators))}&how=html"
    geojsonurl = f"sheet?geographies={quote(','.join(geographies))}&indicators={quote(','.join(indicators))}&how=geojson"
    mapurl = f"sheet?geographies={quote(','.join(geographies))}&indicators={quote(','.join(indicators))}&how=map"

    current_app.logger.warning(
        request.form if request.method == "POST" else request.args
    )

    try:
        with db_engine.connect() as db:
            geom = how == "geojson"
            tearsheet = Tearsheet.create(
                geographies, indicators, db, release=release, geom=geom
            )

        if how == "html":
            first = tearsheet[0]
            headings = first.keys()
            values = [[item for item in row.values()] for row in tearsheet]
            return render_template(
                "table.html",
                headings=headings,
                values=values,
                url=url,
                geojsonurl=geojsonurl,
                mapurl=mapurl,
                method=request.method,
            )

        if how == "map":
            return render_template(
                "map.html",
                geojsonurl=geojsonurl,
            )

        if how == "geojson":
            return jsonify(pack_geojson_response(tearsheet))

        if (how is not None) | (how != "json"):
            print(
                "WARNING: {how} is not a valid 'how', must be one of ('html', 'geojson', 'json'). Returning json."
            )

        return jsonify(tearsheet)
    except (ProgrammingError, Exception) as e:
        if how == "html":
            match e:
                case ProgrammingError():
                    match e.orig:
                        case UndefinedTable():
                            return render_template(
                                "error.html",
                                e="The table you're requestiong doesn't exist. Make sure your variables are spelled correctly.",
                                error_type=type(e.orig),
                            )
                        case _:
                            return render_template(
                                "error.html",
                                e=e.statement,
                                error_type=type(e.orig),
                            )
                case AttributeError():
                    return render_template(
                        "error.html",
                        e="Something is wrong with one of your tearsheet equations. Revise and try again.",
                        error_type="equation error",
                    )
                case _:
                    return render_template(
                        "error.html", e=e, error_type=type(e)
                    )

        return jsonify({"message": f"there was an error with your request {e}"})


@tearsheet.route("/explain")
def explain():
    geographies = unquote(request.args.get("geographies", "").split(","))
    indicators = unquote(request.args.get("indicators", "").split(","))
    release = unquote(request.args.get("release", "acs2022_5yr"))

    with db_engine.connect() as db:
        the_fineprint = Tearsheet.explain(
            geographies, indicators, db, release=release
        )

    return jsonify(the_fineprint)


@tearsheet.route("/geosearch")
def geo_search():
    current_app.logger.warning(request.args.get("q", ""))
    with db_engine.connect() as db:
        results = list(Geography.search(unquote(request.args.get("q", "")), db))
        current_app.logger.warning(results)

        return render_template("geo_search_tool.html", results=results)


@tearsheet.route("/recipes", methods=["GET", "POST"])
def recipes():
    return render_template("recipes.html", recipes=[])


@tearsheet.route("/validate-test", methods=["GET", "POST"])
def validate_test():
    return render_template("validate_only.html")


@tearsheet.route("/validate-geography")
def validate_geo():
    # Before DB hit
    # 1. If parent-child, make sure the relationship makes sense
    # 2. Make sure the main geoid follows the basic

    valid_suffixes = {
        "040": ("state", r"04000US\d{2}$"),
        "050": ("county", r"05000US\d{5}$"),
        "060": ("county subdivision", r"06000US\d{10}$"),
        "140": ("tract", r"14000US\d{11}$"),
        "860": ("zip code", r"86000US\d{5}$"),
        "970": ("school district", r"97000US\d{7}$"),
    }

    valid_children = {
        "040": {"050", "060", "140", "860"},
        "050": {"140", "060", "860"},
        "060": {"140", "860"},
    }

    if request.method == "POST":
        geographies = (
            request.form.get("geographies", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )
    else:
        geographies = (
            request.args.get("geographies", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )

    helpers = []
    for geo_arg in geographies:
        *children, geography = geo_arg.split("|")
        readable, check_re = valid_suffixes[geography[:3]]

        matcher = re.compile(check_re)
        if not matcher.match(geography):
            helpers.append(
                f"'{geography}' is not a valid geoid for a {readable} (geoids beginning with {geography[:3]})"
            )

        if bool(children) and (
            children[0] not in valid_children[geography[:3]]
        ):
            helpers.append(
                f"'{children[0]}' is not a valid child for '{geography}'"
            )

    return render_template("validation.html", helpers=helpers)


@tearsheet.route("/validate-program", methods=["GET", "POST"])
def validate_lesp():
    if request.method == "POST":
        indicators = (
            request.form.get("indicators", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )

    else:
        indicators = (
            request.args.get("indicators", "")
            .strip()
            .replace(", ", ",")
            .split(",")
        )

    # Part 1. Validate LESP
    helpers = []
    for indicator in indicators:
        if indicator.startswith(":") and ("|" in indicator):
            helpers.append(
                "You cannot use a recipe name (starts with ':' as an indicator name in {indicator}."
            )

        name, *eq = indicator.split("|")
        if eq:
            success, message = Indicator.validate_indicator(eq[0])

            if not success:
                helpers.append(f"Error with indicator {name}. {message}")

    if helpers:
        # Parsing is fast, so if you're at this point with a failure
        # send the response.

        return render_template("validation.html", helpers=helpers)

    # Part 2. Make sure the indicators exist

    variables = set()
    for indicator in indicators:
        name, *eq = indicator.split("|")
        if eq:
            variables = variables | extract_variables(eq[0])
        else:
            variables.add(name)

    if variables:
        with db_engine.connect() as db:
            missing_tables = Indicator.identify_missing_tables(variables, db)

        helpers = [
            f"{table} isn't available in the ACS 5-year, check your variable spelling"
            for table in missing_tables
            if table.strip()  # Trying to handle weird edge case where '' is getting warned on.
        ]

        return render_template("validation.html", helpers=helpers)
    return render_template("validation.html")


@tearsheet.route("/varsearch")
def text_search():
    """
    There are two uses for this endpoint:
    1. Obvious, return the results from the query in results.
    2. Repeat the search and return the results and query box.
    """
    q = request.args.get("q")
    how = request.args.get("how", "html")

    stmt = text(
        """
        WITH params AS (
            SELECT ts_rewrite(
                websearch_to_tsquery(:q), 
                'SELECT expected_q, alias_q FROM censearch.category_aliases'
            ) AS prepped_q
        ),
             table_results AS (
            SELECT
               id as table_id,
               '' as variable_id,
               ts_headline(
                   'english', description, prepped_q, 
                   'MaxWords=200, StartSel="<mark>", StopSel="</mark>"'
               ) AS highlighted_table,
               '' as highlighted_variable,
               universe,
               ts_rank(
                   '{0.25, 0.5, 0.75, 1.0}',
                   setweight(to_tsvector(keyword), 'A')
                   || setweight(to_tsvector(unkeyed), 'C'),
                   params.prepped_q
               ) as rnk  
            FROM censearch.acs_tables, params
            WHERE (
                    to_tsvector(keyword) @@ params.prepped_q
                    OR to_tsvector(unkeyed) @@ params.prepped_q
                    OR id like :q || '%' -- Last resort
                )
                AND length(id) = 6
             ),
             variable_results AS (
            SELECT
               tab.id as table_id,
               var.id as variable_id,
               ts_headline(
                   'english', tab.description, prepped_q, 
                   'MaxWords=200, StartSel="<mark>", StopSel="</mark>"'
               ) AS highlighted_table,
               ts_headline(
                   'english', full_label, prepped_q, 
                   'MaxWords=200, StartSel="<mark>", StopSel="</mark>"'
               ) AS highlighted_variable,
               tab.universe,
               ts_rank(
                   '{0.25, 0.5, 0.75, 1.0}',
                   setweight(to_tsvector(tab.keyword), 'B')
                   || setweight(to_tsvector(tab.unkeyed), 'C')
                   || setweight(to_tsvector(full_label), 'D'),
                   params.prepped_q
               ) as rnk  
            FROM censearch.acs_variables AS var
            LEFT JOIN censearch.acs_tables AS tab
                ON tab.id = var.table_id
            CROSS JOIN params 
            WHERE to_tsvector(full_label) @@ params.prepped_q
                AND length(var.table_id) = 6
             )
        SELECT table_id, 
               variable_id, 
               highlighted_table, 
               highlighted_variable, 
               universe,
               rnk
        FROM (
            SELECT * 
            FROM table_results t
            UNION ALL
            SELECT *            
            FROM variable_results v
            WHERE NOT EXISTS (
                SELECT 1
                FROM table_results t
                WHERE t.table_id = v.table_id
            )
        ) AS everything
        ORDER BY rnk desc, table_id, variable_id
        LIMIT 10;
        """
    )

    with db_engine.connect() as db:
        rows = db.execute(stmt, {"q": q})  # type: ignore
        results = rows.fetchall()

    row_dicts = [
        {
            "table": row.highlighted_table,
            "table_id": row.table_id,
            "variable": row.highlighted_variable,
            "variable_id": row.variable_id,
            "universe": row.universe,
        }
        for row in results
    ]

    table_groups = groupby(row_dicts, key=lambda x: x["table_id"])

    hits = []

    for table in table_groups:
        """
        Loop over group results an capture mutliple hits from the same
        table and add variables as a list. If there is no variable data
        you can continue with a table-level result.
        """

        _, var_iter = table

        complete_table = None
        for row in var_iter:
            if not row["variable"]:
                # If you find a table-level result return it directly but
                # switch to list for a type match
                row["variable_id"] = []
                row["variable"] = []
                complete_table = row
                break

            if not complete_table:
                # If this is the first, row unpack evenything and switch
                # variable & variable_id to list
                complete_table = {**row}
                complete_table["variable_id"] = [row["variable_id"]]
                complete_table["variable"] = [row["variable"]]

            else:
                complete_table["variable_id"].append(row["variable_id"])
                complete_table["variable"].append(row["variable"])

        hits.append(complete_table)

    if how == "json":
        return jsonify(hits)

    return render_template("var_search_tool.html", results=hits, q=q)


@tearsheet.route("/clear")
def clear():
    return render_template("control_reset.html")


def convert_to_dicts(variable_list):
    return [
        {
            "table_id": item.table_id,
            "table_label": item.description,
            "universe": item.universe,
            "variable_id": item.id,
            "parent_id": item.parent_id,
            "variable_label": item.label,
            "children": None,
        }
        for item in variable_list
    ]


def nest_variables(variables, parent_id=None):
    """
    ACS variables are hierarchical. This will take a list of ACS variables and
    arrange them appropriately into a tree based on the parent column.

    Only use this on small data. It's n^2 in its current form.
    """
    tree = []

    for variable in variables:
        if variable["parent_id"] == parent_id:
            variable["children"] = nest_variables(
                variables, parent_id=variable["variable_id"]
            )

            tree.append(variable)

    return tree


@tearsheet.route("/varsearch/tables/<table_id>")
def table_detail_page(table_id):
    source_q = request.args.get("source_q", "")

    stmt = text(
        """
        SELECT t.id as table_id,
               t.description,
               t.universe,
               v.id,
               v.parent_id,
               v.label
        FROM censearch.acs_variables v
        JOIN censearch.acs_tables t
        ON v.table_id = t.id
        WHERE table_id = :table_id
        ORDER BY v.id;
    """
    )

    with db_engine.connect() as db:
        result = db.execute(stmt, {"table_id": table_id})
        rows = result.fetchall()

    nested_variables = nest_variables(convert_to_dicts(rows))
    head = nested_variables[0]

    table = {
        "table_id": head["table_id"],
        "table_label": head["table_label"],
        "universe": head["universe"],
    }

    return render_template(
        "table_detail.html", 
        table=table, 
        variables=nested_variables,
        source_q=source_q
    )


@tearsheet.route("/help")
def help():
    return render_template("help.html")
