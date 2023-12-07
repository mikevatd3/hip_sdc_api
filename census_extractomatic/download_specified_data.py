from itertools import groupby
from collections import namedtuple
import os
import tempfile

from flask import abort
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, OperationalError
from returns.result import Result, Success, Failure


def grab_remaining_geoid_info(geoids: tuple[str, ...], db) -> Result:
    try:
        result = db.session.execute(
            text(
                """SELECT full_geoid,
                  population,
                  display_name
           FROM tiger2021.census_name_lookup
           WHERE full_geoid IN :geo_ids;"""
            ),
            {"geo_ids": geoids},
        )

        if result.count() == 0:
            return Failure("Query returned no results.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def check_table_requests(table_ids: tuple[str, ...], db) -> Result:
    try:
        result = db.execute(
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
            {"table_ids": table_ids},
        )

        if result.rowcount == 0:
            return Failure("Query returned no results.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def group_tables(table_ids):
    """
    This trying to save this awkward groupby. This fix for this
    loop with equivalent-name assignment might be a better query
    in the first place.
    """
    Table = namedtuple(
        "Table", "table_id table_title universe denominator_column_id"
    )
    valid_table_ids = []
    table_metadata = {}
    for table, columns in groupby(
        table_ids,
        lambda x: Table(
            x.table_id,
            x.table_title,
            x.universe,
            x.denominator_column_id,
        ),
    ):
        valid_table_ids.append(table.table_id)
        table_metadata[table.table_id] = {
            "title": table.table_title,
            "universe": table.universe,
            "denominator_column_id": table.denominator_column_id,
            "columns": {
                column.column_id: {
                    "name": column.column_title,
                    "indent": column.indent,
                }
                for column in columns
            },
        }

    return valid_table_ids, table_metadata


def drop_whitespace(text: str) -> str:
    return "\n".join([line.strip() for line in text.split("\n") if line.strip()])


def build_fetch_query(table_ids: list[str]):
    from_table, *join_tables = table_ids
    from_stmt = "%s_moe" % (from_table)

    join_clause = "\n".join(
        ["OUTER JOIN %s_moe USING (geoid)" % (table_id) for table_id in join_tables]
    )

    return drop_whitespace(
        f"""
        SELECT * 
        FROM {from_stmt}
        {join_clause}
        WHERE geoid IN :geo_ids;
        """
    )


def fetch_data(table_ids, geoids, db):
    try:
        sql = text(build_fetch_query(table_ids))
        result = db.session.execute(sql, {"geo_ids": geoids})
        if result.count() < 1:
            return Failure("Query returned no data.")

        return Success(result)

    except (ProgrammingError, OperationalError) as e:
        return Failure(e)


def column_prep_loop(data_iter):
    table_for_geoid = {}
    table_for_geoid["estimate"] = {}
    table_for_geoid["error"] = {}

    data_iter = list(data_iter)

    # The variables and moes are arranged consecutively, so use zip
    # and slice notation to iterate over two cols at a time.
    for (col_name, value), (_, moe_value) in zip(
        data_iter[:-1:2], data_iter[1::2]
    ):
        col_name = col_name.upper()

        table_for_geoid["estimate"][col_name] = value
        table_for_geoid["error"][col_name] = moe_value

    return table_for_geoid


def data_prep_loop(result):
    data = {}
    for row in result.fetchall():
        row = dict(row._mapping)
        geoid = row.pop("geoid")
        data_for_geoid = {}

        cols_iter = iter(sorted(row.items(), key=lambda tup: tup[0]))  # The key

        for table_id, data_iter in groupby(
            cols_iter, lambda x: x[0][:-3].upper()  # Grouping columns by table
        ):
            data_for_geoid[table_id] = column_prep_loop(data_iter)

        data[geoid] = data_for_geoid

    return data


def prep_temp_file(valid_table_ids, valid_geoids, format: str):
    temp_path = tempfile.mkdtemp() # side effect ?
    file_ident = f"{acs}_{next(iter(valid_table_ids))}_{next(iter(valid_geoids))}"            
    inner_path = os.path.join(temp_path, file_ident)
    os.mkdir(inner_path) # side effect !

    return os.path.join(inner_path, f"{file_ident}.{format}")



def prepare_download():
    try:
        valid_geoids, _ = expand_geoids(geoids, release="acs2021_5yr")

    except ShowDataException as e:
        return Failure((400, e.message))


    if (num_geoids := len(valid_geoids)) > current_app.config.get(
        "MAX_GEOIDS_TO_DOWNLOAD", 500
    ):
        abort(
            400,
            f"You requested {num_geoids} geoids which is beyond our limit of 500.",
        )

    geoid_result = grab_remaining_geoid_info(tuple(valid_geoids), db)

    match geoid_result:
        case Success(inner):
            geo_rows = inner

        case Failure(e):
            app.logger.error(f"The query is failing with error: {e}.")
            abort(400, "Query error due to requested geoids.")
    
    table_result = check_table_requests(table_ids, db)

    match table_result:
        case Success(inner):
            table_rows = inner

        case Failure(e):
            app.logger.error(f"The query is failing with error: {e}.")
            abort(400, "Query error due to requested table ids.")
