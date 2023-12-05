from itertools import groupby

from sqlalchemy import text
from pypika import Table, Query, Order

from .ts_custom_functions import (
    ts_unindexed,
    ts_rank_cd,
    to_tsvector,
    to_tsquery,
    prep_q_for_text_search,
)


def wrap_up_columns(column_rows):
    table_metadata = {}
    valid_table_ids = []
    for table, columns in groupby(
        column_rows, lambda col: col[:4]
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

    return table_metadata, valid_table_ids


def format_table_search_result(obj, include_columns, release):
    """internal util for formatting each object in `table_search` API response"""

    result = {
        "table_id": obj.table_id,
        "table_name": obj.table_title,
        "simple_table_name": obj.simple_table_title,
        "topics": obj.topics,
        "universe": obj.universe,
        "release": release,
    }

    if include_columns:
        result.update(
            {
                "id": obj.column_id,
                "type": "column",
                "unique_key": f"{obj.table_id}|{obj.column_id}",
                "column_id": obj.column_id,
                "column_name": obj.column_title,
            }
        )

        return result

    result.update(
        {
            "id": obj.table_id,
            "type": "table",
            "unique_key": obj.table_id,
        }
    )

    return result


def search_tables(q, release, db, limit=5, offset=0):
    census_table_metadata = Table("census_table_metadata", schema=release)
    stmt = (
        Query.from_(census_table_metadata)
        .select("*")
        .where(
            ts_unindexed(
                census_table_metadata.table_title, prep_q_for_text_search(q)
            )
        )
        .orderby(
            ts_rank_cd(
                to_tsvector(census_table_metadata.table_title),
                to_tsquery(prep_q_for_text_search(q)),
            ),
            order=Order.desc,
        )
        .limit(limit)
        .offset(offset)
    )

    result = db.execute(text(stmt.get_sql()))

    return result


def get_column_metadata(table_id, release, db):
    census_column_metadata = Table("census_column_metadata", schema=release)

    stmt = (
        Query.from_(census_column_metadata)
        .select("*")
        .where(census_column_metadata.table_id == table_id)
    )
    result = db.execute(text(stmt.get_sql()))

    return result


def get_table_metadata(table_ids, release, db, include_columns=False):
    census_table_metadata = Table("census_table_metadata", schema=release)
    census_column_metadata = Table("census_column_metadata", schema=release)

    stmt = (
        Query.select(
            census_table_metadata.table_id,
            census_table_metadata.table_title,
            census_table_metadata.universe,
            census_table_metadata.denominator_column_id,
        )
        .from_(census_table_metadata)
        .orderby(census_table_metadata.table_id)
    )

    if include_columns:
        stmt = (
            stmt.select(
                census_column_metadata.column_id,
                census_column_metadata.column_title,
                census_column_metadata.column_indent,
            )
            .join(census_column_metadata)
            .using("table_id")
            .orderby(census_column_metadata.column_id)
        )

    result = db.session.execute(
        text(str(stmt)), {"table_ids": tuple(table_ids)}
    )

    data = []
    for row in result:
        data.append(format_table_search_result(row, include_columns, release))

    return data


def get_tabulation(tabulation_id, db):
    return db.execute(
        text(
            """SELECT *
            FROM census_tabulation_metadata
            WHERE tabulation_code=:tabulation
            """
        ),
        {"tabulation": tabulation_id},
    ).fetchone()


if __name__ == "__main__":
    from urllib.parse import quote
    from sqlalchemy import create_engine
    import tomli

    with open("config.toml", "rb") as f:
        config = tomli.load(f)

    HOST, DBNAME, USERNAME, PASSWORD, PORT = config["db"].values()
    connection_string = f"postgresql+psycopg2://{USERNAME}:{quote(PASSWORD)}@{HOST}:{PORT}/{DBNAME}"
    public_engine = create_engine(connection_string)

    with public_engine.connect() as db:
        while True:
            inlet = input("Please enter search query: ")

            result = list(search_tables(inlet, "acs2021_5yr", db))
            if not result:
                print("No results for that query.")
                continue
            for i, table in enumerate(result, 1):
                print(f"{i}. {table.table_id}: {table.table_title}")

            choice = input(
                "Please choose a table to see available columns (or n! to find another table): "
            )
            columns = get_column_metadata(
                result[int(choice) - 1].table_id, "acs2021_5yr", db
            )

            for column in columns:
                print(f"{column.column_id}: {column.column_title}")
