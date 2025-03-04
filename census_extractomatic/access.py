
"""
Classes in this file serve as rough module boundaries.

- Indicators are either variables, or 'cooked' formulas with lesp
- Variables are raw values from the db

"""


from sqlalchemy import text
import tomli
from pypika import (
    Query,
    Table,
    Column,
    Schema,
    Parameter,
    AliasedQuery,
    CustomFunction,
)
from pypika import functions as fn
from collections import defaultdict
import pandas as pd
from lesp.core import execute
from lesp.analyze import extract_variables, validate_program, LespCompileError
from .datatypes import make_maybe, Empty, TearValue, serialize_maybes


DEFAULT_ACS_YEAR = "acs2022_5yr"
DEFAULT_D3_YEAR = "d3_2024"


with open("config.toml", "rb") as f:
    conf = tomli.load(f)


class Indicator:
    special_variables = {
        "land_area": "aland",
        "water_area": "awater",
        "geom": "geom",
    }

    acs_schema = Schema(DEFAULT_ACS_YEAR)
    d3_schema = Schema(DEFAULT_D3_YEAR)

    unified_table_q = unified_table_q = Query.from_(
        acs_schema.census_table_metadata
    ).select("*") + Query.from_(d3_schema.census_table_metadata).select(
        "*"
    )

    @staticmethod
    def validate_indicator(formula: str) -> tuple[bool, str]:
        try:
            validate_program(formula)
        except LespCompileError as e:
            return (False, e.args[0])

        return (True, "")

    @classmethod
    def prep_ind_request(
        cls,
        indicators: list[str],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        formulae = []
        variables = []
        for ind in indicators:
            if "|" in ind:  # lesp strings have to be aliased
                title, function = ind.split("|")

                if title in cls.special_variables:
                    raise ValueError(
                        f"'{title}' is a reserved indicator name, choose something else."
                    )

                formulae.append((title, function.lower()))
                variables.extend(
                    [var.lower() for var in extract_variables(function)]
                )
            else:
                formulae.append((ind, ind.lower()))
                variables.append(ind.lower())
                # TODO: This needs to be validated further!

        return formulae, variables


    @classmethod
    def preview(cls, variable_id, db):
        tree_q = text(f"""
        WITH RECURSIVE hits AS (
            SELECT *
            FROM {DEFAULT_ACS_YEAR}.census_column_metadata
            WHERE column_id = :variable_id

            UNION ALL

            SELECT cm.*
            FROM {DEFAULT_ACS_YEAR}.census_column_metadata cm
            INNER JOIN hits ON hits.parent_column_id = cm.column_id
        )
        SELECT *
        FROM hits
        ORDER BY indent;
        """)


        table_q = text("""
        SELECT *
        FROM {DEFAULT_ACS_YEAR}.census_table_metadata
        WHERE table_id = LEFT(:variable_id, 6);
        """)

        tree = db.execute(tree_q, {"variable_id": variable_id})
        table = db.execute(table_q, {"variable_id": variable_id})

        return tree.fetchall(), table.fetchone()


    @classmethod
    def explain(*args, **kwargs):
        return {
            "apology": "It would be lovely if this feature were working, but it just isn't yet."
        }

    @classmethod
    def wrap_values(
        cls, dataframe: pd.DataFrame, variables: list[str], geom=False
    ) -> pd.DataFrame:
        """
        Wrap up dataframe into combined estimate and moes for combination.
        """

        wrapped_rows = []
        for _, row in dataframe.iterrows():
            """
            Remember that we're fully iterating, so each row should be
            handled at the value level.
            """
            wrapped_row = {}
            wrapped_row["geoid"] = row["geoid"]
            wrapped_row["name"] = row["name"]

            if geom:
                wrapped_row["geom"] = row["geom"]

            for var in variables:
                if var in cls.special_variables:

                    # Get the special var real name if alias is provided
                    # otherwise use the name provided.
                    real_var_name = cls.special_variables.get(var, var)

                    # Special variables don't have errors
                    wrapped_row[var] = TearValue(
                        make_maybe(row[real_var_name]), make_maybe(0)
                    )
                else:
                    value = row[var]
                    # The census returns large negative values sometimes
                    if (not value) or (value < -1000):
                        value = Empty()
                    else:
                        value = make_maybe(value)

                    error = row[var + "_moe"]
                    # Negative errors don't make sense, so default to empty
                    if (not error) or (error < 0):
                        error = Empty()
                    else:
                        error = make_maybe(error)

                    wrapped_row[var] = TearValue(value, error)

            wrapped_rows.append(wrapped_row)

        return pd.DataFrame(wrapped_rows)

    @classmethod
    def create_namespace(
        cls,
        prepared_geos: list[str],
        variables: list[str],
        db,
        release: str,
        geom=False,
    ):
        st_asgeojson = CustomFunction("ST_AsGeoJSON", ["geom"])

        tables = {
            var[:-3] for var in variables if var not in cls.special_variables
        }
        geoheader = Table("geoheader")

        to_collect = []
        specials = []

        for var in set(variables):
            if var in cls.special_variables:
                specials.append(var)
            else:
                to_collect.append(Column(var.lower()))
                to_collect.append(Column(var.lower() + "_moe"))

        stmt = Query.from_(geoheader).select(
            geoheader.geoid, geoheader.name, *to_collect
        )

        for table in tables:
            table = Table(table.lower() + "_moe")
            stmt = stmt.left_join(table).on(table.geoid == geoheader.geoid)

        if geom | bool(specials):
            tiger2022 = Schema("tiger2022")
            stmt = stmt.join(tiger2022.census_name_lookup).on(
                tiger2022.census_name_lookup.full_geoid == geoheader.geoid
            )

            # I don't like this nesting
            if geom:
                stmt = stmt.select(
                    st_asgeojson(tiger2022.census_name_lookup.geom).as_("geom")
                )

            if specials:
                stmt = stmt.select(
                    *[
                        tiger2022.census_name_lookup[cls.special_variables[var]]
                        for var in specials
                    ]
                )

        stmt = stmt.where(geoheader.geoid.isin(prepared_geos))

        db.execute(
            text("SET search_path TO :acs, d3_2024, d3_present, public;"),
            {"acs": release},
        )

        return Indicator.wrap_values(
            pd.read_sql(text(str(stmt)), db), variables, geom=geom
        )

    @classmethod
    def identify_missing_tables(cls, variables, db, release=DEFAULT_ACS_YEAR):
        tables = {
            var[:-3].upper()
            for var in variables
            if var not in cls.special_variables
        }

        unified_tables = AliasedQuery("unified_tables")

        match_tables = (
            Query.with_(cls.unified_table_q, "unified_tables")
            .from_(unified_tables)
            .select(unified_tables.table_id)
            .where(fn.Upper(unified_tables.table_id).isin(Parameter(":tables")))
        )

        db.execute(
            text("SET search_path TO :acs, d3_2024, d3_present, public;"),
            {"acs": release},
        )

        rows = db.execute(text(str(match_tables)), {"tables": tuple(tables)})

        available_tables = {table.table_id for table in rows}

        missing_tables = (
            tables - available_tables
        )  # This set operations shows elements in a that aren't in b

        return missing_tables

    @staticmethod
    def compile(prepared_geos, formulae, variables, db, release, geom=False):
        namespace = Indicator.create_namespace(
            prepared_geos, variables, db, release, geom=geom
        )
        non_formula_vars = ["geoid", "name"]

        if geom:
            non_formula_vars.append("geom")

        calculated_rows = pd.concat(
            [namespace[non_formula_vars]]
            + [
                execute(arithmetic, namespace).rename(name.lower())
                for name, arithmetic in formulae
            ],
            axis=1,
        )

        result = []
        for row in calculated_rows.to_dict(orient="records"):
            record = {}
            record["geoid"] = row["geoid"]
            record["name"] = row["name"]

            if geom:
                record["geom"] = row["geom"]

            for formula in formulae:
                ind_name = formula[0]

                try:
                    record[ind_name] = serialize_maybes(
                        row[ind_name.lower()].value
                    )
                    record[ind_name + "_moe"] = serialize_maybes(
                        row[ind_name.lower()].error
                    )
                except AttributeError as e:
                    if isinstance(row[ind_name.lower()], bool):
                        record[ind_name] = row[ind_name]
                    else:
                        raise e

            result.append(record)

        return result

    @classmethod
    def search(cls, query: str, db):
        # db.execute(
        # text("SET search_path TO :acs, d3_2024, d3_present, public;"),
        # {"acs": release},
        # )

        # TODO: Get the first five tables, then join all variables
        # Use more complete text search
        # Add D3 tables
        #    - Use table title,

        unified_col_q = Query.from_(
            Schema(DEFAULT_ACS_YEAR).census_column_metadata
        ).select("*") + Query.from_(
            Schema(DEFAULT_D3_YEAR).census_column_metadata
        ).select(
            "*"
        )

        unified_tables = AliasedQuery("unified_tables")
        unified_columns = AliasedQuery("unified_columns")

        match_tables = (
            Query.with_(cls.unified_table_q, "unified_tables")
            .from_(unified_tables)
            .select(unified_tables.table_id)
            .where(
                fn.Lower(unified_tables.table_title).like(Parameter(":query"))
            )
            .limit(10)
            .offset(0)
        )

        stmt = (
            Query.with_(match_tables, "match_tables")
            .with_(cls.unified_table_q, "unified_tables")
            .with_(unified_col_q, "unified_columns")
            .from_(unified_tables)
            .select(
                unified_tables.table_id,
                unified_tables.table_title,
                unified_columns.column_id,
                unified_columns.column_title,
                unified_columns.indent,  # use this to indent the variables
            )
            .join(unified_columns)
            .on(unified_tables.table_id == unified_columns.table_id)
            .join(AliasedQuery("match_tables"))
            .on(
                AliasedQuery("match_tables").table_id == unified_tables.table_id
            )
            .orderby(unified_tables.table_id, unified_columns.column_id)
        )

        result = db.execute(text(str(stmt)), {"query": f"%{query.lower()}%"})

        return result.fetchall()


class CensusAPIIndicator:
    """
    This should handle the same data pull steps available on the class
    above but using the census API, not the census reporter database.
    """


class Tearsheet:
    @staticmethod
    def create(geographies, indicators, db, release=DEFAULT_ACS_YEAR, geom=False):
        prepared_geos = Geography.prep_geo_request(geographies, db)
        formulae, variables = Indicator.prep_ind_request(indicators)

        return Indicator.compile(
            prepared_geos, formulae, variables, db, release, geom=geom
        )

    @staticmethod
    def explain(geographies, indicators, db, release=DEFAULT_ACS_YEAR):
        prepared_geos = Geography.prep_geo_request(geographies, db)
        formulae, variables = Indicator.prep_ind_request(indicators)

        return Indicator.explain(
            prepared_geos, formulae, variables, db, release
        )


class Geography:
    sum_lev_aliases = {
        "zips": "860",
        "tracts": "140",
        "cosubs": "060",
        "places": "160",
        "counties": "050",
        "school_districts": "970",
    }

    rev_aliases = {v: k for k, v in sum_lev_aliases.items()}

    valid_sum_levs = {"040", "050", "060", "160", "140", "860", "970", "950"}

    @classmethod
    def find_within(cls, sumlev: str, geoid: str, db) -> list[str]:
        numlev = cls.sum_lev_aliases.get(sumlev, sumlev)

        if numlev not in cls.valid_sum_levs:
            raise ValueError(
                f"'{sumlev}' is not a valid summary level or alias."
            )

        geo_schema = Schema("tiger2022")

        stmt = (
            Query.from_(geo_schema.census_geo_containment)
            .select("child_geoid")
            .where(geo_schema.census_geo_containment.parent_geoid == geoid)
            .where(
                geo_schema.census_geo_containment.child_geoid.like(numlev + "%")
            )
        )

        result = db.execute(text(str(stmt)))

        return [item.child_geoid for item in result]

    @staticmethod
    def prep_geo_request(geographies: list[str], db) -> list[str]:
        result = []
        for geo in geographies:
            if "|" in geo:
                sumlev, geoid = geo.split("|")
                result.extend(Geography.find_within(sumlev, geoid, db))
            else:
                result.append(geo)

        return result

    @classmethod
    def search(cls, query, db):
        """
        Apply basic text, search, then pull out all available geographies
        for fill-in helpers.
        """
        search_q = text(
            """
            with michigan as (
                select display_name, full_geoid, population, name_vec, priority
                from tiger2022.census_name_lookup
                where state_fp = 26
            )
            select full_geoid, display_name
            from michigan
            where name_vec @@ to_tsquery(:query)
            and priority is not null
            order by priority::int asc, population desc
            limit 10;
            """
        )

        result = db.execute(search_q, {"query": " & ".join(query.split())})

        geographies = [
            {
                "full_geoid": row.full_geoid,
                "display_name": row.display_name,
            } for row in result.fetchall()
        ]

        children_q = text(
            """
            select distinct(left(child_geoid, 3)) as sum_lev, 
                   parent_geoid
            from tiger2022.census_geo_containment
            where parent_geoid in :geoids
            and not child_geoid like '150%'; -- no block groups!
            """
        )

        children_levs = db.execute(
            children_q, 
            {"geoids": tuple(row["full_geoid"] for row in geographies)}
        )

        available_child_levs = defaultdict(list)
        for row in children_levs.fetchall():
            available_child_levs[row.parent_geoid].append(row.sum_lev)

        for geography in geographies:
            geography["children"] = [
                cls.rev_aliases[child] 
                for child in available_child_levs[geography["full_geoid"]]
            ]

        return geographies

"""
The problem that we continue to face is that due to lack of consistant code 
standards, tool choice, and deployment procedure, often facing time constraints,
we tend to either fail to set up a dev server or set up one and then it gets 
repurposed for something else that comes up.

We need a set of agreed-upon tools that we know how to reliably deploy, 
a standard deployment procedure (including development servers and production 
servers, naming conventions, etc), and testing & git workflows. We need team-wide
understanding that that procedure exists and is the ONLY way we start web projects.
"""

