from urllib.parse import quote, unquote
from flask import render_template, request, jsonify, Blueprint, current_app
from sqlalchemy import create_engine
import tomli

from .access import Geography, Indicator, Tearsheet


tearsheet = Blueprint('tearsheet', __name__)


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
        geographies = request.form.get("geographies", "").split(",")
        indicators = request.form.get("indicators", "").split(",")
        release = request.form.get("release", "acs2022_5yr")
        html = request.form.get("html") == "yes"
    else:
        geographies = unquote(request.args.get("geographies", "")).split(",")
        indicators = unquote(request.args.get("indicators", "")).split(",")
        release = unquote(request.args.get("release", "acs2022_5yr"))
        html = request.args.get("html") == "yes"
    
    url = f"{BASE_URL}?geographies={quote(','.join(geographies))}&indicators={quote(','.join(indicators))}&html=yes"

    current_app.logger.warning(request.form if request.method == "POST" else request.args)
    
    with db_engine.connect() as db:
        tearsheet = Tearsheet.create(
            geographies, indicators, db, release=release
        )

    if html:
        first, *rest = tearsheet
        headings = first.keys()
        values = [[item for item in row.values()] for row in [first] + rest]
        return render_template("table.html", headings=headings, values=values, url=url)

    return jsonify(tearsheet)


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
def variable_search():
    with db_engine.connect() as db:
        result = Indicator.search(unquote(request.args.get("query", "")), db)

    return jsonify(result)


@tearsheet.route("/help")
def help():
    return render_template("help.html")

