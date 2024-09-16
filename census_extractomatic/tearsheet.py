from itertools import groupby
from urllib.parse import quote, unquote
from flask import render_template, request, jsonify, Blueprint, current_app
from flask_cors import CORS, cross_origin
from sqlalchemy import create_engine, text
import tomli

from census_extractomatic._api.download_data import pack_geojson_response

from .variable_organize import arrange_variable_hierarchy
from .access import Geography, Indicator, Tearsheet



tearsheet = Blueprint('tearsheet', __name__)

CORS(tearsheet)


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
        geographies = request.form.get("geographies", "").replace(", ", ",").split(",")
        indicators = request.form.get("indicators", "").replace(", ", ",").split(",")
        release = request.form.get("release", "acs2022_5yr")
        how = request.form.get("how")

    else:
        geographies = unquote(request.args.get("geographies", "")).replace(", ", ",").split(",")
        indicators = unquote(request.args.get("indicators", "")).replace(", ", ",").split(",")
        release = unquote(request.args.get("release", "acs2022_5yr"))
        how = request.args.get("how")
    
    url = f"{BASE_URL}?geographies={quote(','.join(geographies))}&indicators={quote(','.join(indicators))}&how=html"

    current_app.logger.warning(request.form if request.method == "POST" else request.args)

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
            return render_template("table.html", headings=headings, values=values, url=url)

        if how == "geojson":
            return jsonify(pack_geojson_response(tearsheet))

        if (how is not None) | (how != 'json'):
            print("WARNING: {how} is not a valid 'how', must be one of ('html', 'geojson', 'json'). Returning json.")

        return jsonify(tearsheet)

    except Exception as e:
        if how == "html":
            return render_template("error.html", error=e)
        else:
            return jsonify({"message": f"error with your request {e}"})


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
    with db_engine.connect() as db:
        result = Geography.search(unquote(request.args.get("query", "")), db)

        return render_template("geo_results.html", result=result)



@tearsheet.route("/varsearch")
def text_search():
    """
    We're prioritizing 3 search strategies:

    1. Search table descriptions
        - Returns only the table id, table description, and universe
          with the match in the description highlighted.

    2. Search variable descriptions
        - Returns only the table id, table description, variable
          description and universe with the match in the description
          highlighted.

    3. TODO: Search table names
        - This is the only sub-word level search possibility

    4. TODO: Search variable names
        - Oops, no, this is sub-word seach as well

    Tweaks:
    - Somehow represent the more often used tables higher in the
      index.
        - This might require some manual annotating.
    - The split between table-level hits and var level hits is good,
      but it would be better if you could match terms on either level.
    - Include / how to include nested type variables -- like if you want to find
      females involved in fishing, hunting and trapping -- how do you get to the variable level.
    - Collapse racial iterations from search -- link from page.
    - Provide semantic suggestions from the table detail page
        - semantic similarity on all variable columns and fields.
    """
    q = request.args.get("query")

    stmt = text(
        """
        WITH table_highlights AS (
            SELECT 1 AS lev,
                   t.id,
                   ts_headline(t.description, q, 'StartSel="<mark>", StopSel="</mark>"') AS highlighted_text,
                   t.universe,
                   '' as parent_id,
                   '' as parent_label,
                   ts_rank(to_tsvector('english', t.id || ' ' || t.description), q) AS rnk
            FROM censearch.acs_tables t
            CROSS JOIN to_tsquery('english', :q) AS q
            WHERE (to_tsvector('english', t.id || ' ' || t.description) @@ q)
            ORDER BY ts_rank(to_tsvector('english', t.id || ' ' || t.description), q) / LENGTH(t.description) DESC
        ),
             var_highlights AS (
            SELECT DISTINCT ON (v.table_id)
                   2 as lev,
                   t.id,
                   t.description || E'\n' || v.id || ': ' || ts_headline(v.label, q, 'StartSel="<mark>", StopSel="</mark>"') AS highlighted_text,
                   t.universe,
                   vb.id as parent_id,
                   vb.label as parent_label,
                   ts_rank(to_tsvector('english', v.id || ' ' || v.label), q) AS rnk
            FROM censearch.acs_tables t
            JOIN censearch.acs_variables v
                ON v.table_id = t.id
            LEFT JOIN censearch.acs_variables vb
                ON v.parent_id = vb.id
            CROSS JOIN to_tsquery('english', :q) AS q
            WHERE (to_tsvector('english', v.id || ' ' || v.label) @@ q)
            ORDER BY v.table_id, ts_rank(to_tsvector('english', v.id || ' ' || v.label), q) / LENGTH(v.label) DESC
        )
        SELECT DISTINCT ON (id) *
        from (
            SELECT *
            FROM table_highlights
            UNION
            SELECT *
            FROM var_highlights
            ORDER BY lev) combined;
    """
    )

    with db_engine.connect() as db:
        rows = db.execute(stmt, {"q": " & ".join(q.split())})  # type: ignore
        results = rows.fetchall()

    if not results:
        return render_template("no_results.html", query=q)

    hits = [
        {
            "id": row.id,
            "title": row.highlighted_text.split("\n")[0],
            "parent_id": row.parent_id,
            "parent_label": row.parent_label,
            "variable": row.highlighted_text.split("\n")[1]
            if (len(row.highlighted_text.split("\n")) > 1)
            else "",
            "universe": row.universe,
        }
        for row in results
    ]

    return render_template("search_results.html", results=hits)



@tearsheet.route("/passthrough")
def passthrough():

    query = request.args.get("query", "")

    return f"""
    <h3>The query you sent was {query}</h3>
    """
    # return render_template("var_results.html", result=result)



@tearsheet.route("/help")
def help():
    return render_template("help.html")

