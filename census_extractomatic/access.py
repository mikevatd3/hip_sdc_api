from enum import Enum, auto
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError as SQLProgrammingError
import tomli
from pypika import Query, Table, Column, Schema
import pandas as pd
from lesp.core import execute
from lesp.analyze import extract_variables
from .datatypes import Some, Empty, TearValue, serialize_maybes


"""
Classes in this file serve as rough module boundaries.

- Indicators are either variables, or 'cooked' formulas with lesp
- Variables are raw values from the db

"""


class TearsheetError(Exception):
    def __init__(self, message, details: dict | None = None) -> None:
        self.message = message
        self.details = details or {}


class IndicatorSyntaxError(TearsheetError):
    """400 Error"""


class GeographySyntaxError(TearsheetError):
    """400 Error"""


with open("config.toml", "rb") as f:
    conf = tomli.load(f)


class IndFlag(Enum):
    standard = auto()
    custom = auto()


class Indicator:
    special_variables = {
        "land_area": "aland",
        "water_area": "awater",
        "geom": "geom",
    }

    @classmethod
    def prep_ind_request(
        cls,
        indicators: list[str],
    ) -> tuple[list[tuple[IndFlag, str, str]], list[str]]:
        formulae = []
        result = []
        for ind in indicators:
            if "|" in ind:  # lesp strings have to be aliased
                title, function = ind.split("|")

                if title in cls.special_variables:
                    raise ValueError(
                        f"'{title}' is a reserved indicator name, choose something else."
                    )

                formulae.append((IndFlag.custom, title, function.lower()))
                result.extend(
                    [var.lower() for var in extract_variables(function)]
                )
            else:
                formulae.append((IndFlag.standard, ind, ind.lower()))
                result.append(
                    ind.lower()
                )  # TODO: This needs to be validated further!

        return formulae, result

    @classmethod
    def explain(*args, **kwargs):
        return {
            "apology": "It would be lovely if this feature were working, but it just isn't yet."
        }

    @staticmethod
    def run_formula(
        formula: tuple[IndFlag, str, str], namespace: pd.DataFrame
    ) -> pd.Series:
        flag, name, arithmetic = formula

        match flag:
            case IndFlag.custom:
                return execute(arithmetic, namespace).rename(name.lower())
            case IndFlag.standard:
                return namespace[arithmetic].rename(name.lower())

    @classmethod
    def wrap_values(
        cls, dataframe: pd.DataFrame, variables: list[str]
    ) -> pd.DataFrame:
        """
        Wrap up dataframe into combined estimate and moes for combination.
        """

        wrapped_rows = []
        for _, row in dataframe.iterrows():
            wrapped_row = {}
            wrapped_row["geoid"] = row["geoid"]
            wrapped_row["name"] = row["name"]
            for var in variables:
                if var in cls.special_variables:
                    # Special variables don't have errors associated with them
                    wrapped_row[var] = TearValue(
                        Some(row[cls.special_variables[var]]), Some(0)
                    )
                else:
                    value = row[var]
                    if (not value) or (value < -1000):
                        value = Empty()
                    else:
                        value = Some(value)

                    error = row[var + "_moe"]
                    if (not error) or (error < 0):
                        error = Empty()
                    else:
                        error = Some(error)

                    wrapped_row[var] = TearValue(value, error)

            wrapped_rows.append(wrapped_row)

        return pd.DataFrame(wrapped_rows)


    @classmethod
    def create_namespace(
        cls, prepared_geos: list[str], variables: list[str], db, release: str
    ):
        """
        There are two types of tables that can be pulled from the API --
        the 'standard' ACS tables that have an moe included and 'special'
        tables that describe the geography.
        """

        tables = set()
        standards = []
        specials = []

        for var in set(variables):
            if var in cls.special_variables:
                specials.append(var)
            else:
                tables.add(var[:-3])
                standards.append(Column(var.lower()))
                standards.append(Column(var.lower() + "_moe"))

        first_table = Table(tables.pop() + "_moe")
        geoheader = Table("geoheader")

        stmt = Query.from_(first_table).select(
            first_table.geoid, geoheader.name, *standards
        )

        if tables:
            for table in tables:
                table = Table(table.lower() + "_moe")
                stmt = stmt.join(table).on(table.geoid == first_table.geoid)

            if specials:
                tiger2021 = Schema("tiger2021")
                stmt = (
                    stmt.select(
                        *[
                            tiger2021.census_name_lookup[
                                cls.special_variables[var]
                            ]
                            for var in specials
                        ]
                    )
                    .join(tiger2021.census_name_lookup)
                    .on(
                        tiger2021.census_name_lookup.full_geoid
                        == first_table.geoid
                    )
                )

            stmt = (
                stmt.join(geoheader)
                .on(first_table.geoid == geoheader.geoid)
                .where(first_table.geoid.isin(prepared_geos))
            )

            try:
                db.execute(
                    text(
                        "SET search_path TO :acs, d3_2024, d3_present, public;"
                    ),
                    {"acs": release},
                )

            except SQLProgrammingError:
                raise IndicatorSyntaxError("Something wrong with special table.")

            return Indicator.wrap_values(
                pd.read_sql(text(str(stmt)), db), variables
            )

        elif specials:
            tiger2021 = Schema("tiger2021")
            Query.from_(tiger2021.census_name_lookup).select(
                *[
                    tiger2021.census_name_lookup[cls.special_variables[var]]
                    for var in specials
                ]
            ).where(first_table.geoid.isin(prepared_geos))

            try:
                db.execute(
                    text(
                        "SET search_path TO :acs, d3_2024, d3_present, public;"
                    ),
                    {"acs": release},
                )

            except SQLProgrammingError:
                raise IndicatorSyntaxError(
                    "Something wrong with special table."
                )

            return Indicator.wrap_values(
                pd.read_sql(text(str(stmt)), db), variables
            )

        else:
            raise IndicatorSyntaxError("The request was empty.")

    @staticmethod
    def compile(prepared_geos, formulae, variables, db, release):
        namespace = Indicator.create_namespace(
            prepared_geos, variables, db, release
        )

        calculated_rows = pd.concat(
            [namespace[["geoid", "name"]]]
            + [
                Indicator.run_formula(formula, namespace)
                for formula in formulae
            ],
            axis=1,
        )

        result = []
        for row in calculated_rows.to_dict(orient="records"):
            record = {}
            record["geoid"] = row["geoid"]
            record["name"] = row["name"]
            for formula in formulae:
                try:
                    record[formula[1]] = serialize_maybes(
                        row[formula[1].lower()].value
                    )
                    record[formula[1] + "_moe"] = serialize_maybes(
                        row[formula[1].lower()].error
                    )
                except AttributeError as e:
                    if isinstance(row[formula[1].lower()], bool):
                        record[formula[1]] = row[formula[1]]
                    else:
                        raise e

            result.append(record)

        return result

    @staticmethod
    def search(*args, **kwargs):
        return {
            "apology": "It would be lovely if this feature were working, but it just isn't yet."
        }


class Tearsheet:
    @staticmethod
    def create(geographies, indicators, db, release="acs2022_5yr"):
        prepared_geos = Geography.prep_geo_request(geographies, db)

        if not prepared_geos:
            raise GeographySyntaxError("No valid geographies in request.")

        formulae, variables = Indicator.prep_ind_request(indicators)

        if (not formulae) and (not variables):
            raise IndicatorSyntaxError("No valid indicators in request.")

        return Indicator.compile(
            prepared_geos, formulae, variables, db, release
        )

    @staticmethod
    def explain(geographies, indicators, db, release="acs2022_5yr"):
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

    valid_sum_levs = {"040", "050", "060", "160", "140", "860", "970", "950"}

    @classmethod
    def find_within(cls, sumlev: str, geoid: str, db) -> list[str]:
        numlev = cls.sum_lev_aliases.get(sumlev, sumlev)

        if numlev not in cls.valid_sum_levs:
            raise ValueError(
                f"'{sumlev}' is not a valid summary level or alias."
            )

        geo_schema = Schema("tiger2021")

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

    @staticmethod
    def search(query, db):
        stmt = text(
            """
            select *
            from geo_lookup
            where search_col @@ to_tsquery(:query)
            and priority is not null
            order by priority desc
            limit 5;
            """
        )

        result = db.execute(stmt, {"query": " & ".join(query.split())})

        return result.fetchall()
