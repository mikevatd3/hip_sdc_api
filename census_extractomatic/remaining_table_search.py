"""
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
"""
