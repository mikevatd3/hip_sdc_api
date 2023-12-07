import io
import csv
import zipfile
import json
import datetime

from flask import send_file, make_response, jsonify

from .access import convert_row_to_dict, pack_tables, convert_row_to_dict
from .reference import ACS_NAMES


def table_metadata_to_rows(table_metadata: dict):
    rows = []
    for table_id, table_details in table_metadata.items():
        for column_id, column_details in table_details["columns"].items():
            rows.append(
                (
                    column_id,
                    table_id,
                    table_details["title"],
                    column_details["name"].rjust(
                        4 * int(column_details["indent"])
                        + len(
                            column_details["name"]
                        )  # This formats the column name to match the indent
                    ),
                    table_details["universe"],
                    table_details["denominator_column_id"],
                )
            )

    return rows


def geo_metadata_to_rows(geo_metadata: dict):
    return [
        (geo["geoid"], geo["display_name"], geo["sum_level"])
        for geo in geo_metadata.values()
    ]


def tup_rows_to_csv_io(rows, header):
    csv_file = io.StringIO()

    writer = csv.writer(csv_file)
    writer.writerow(header)

    for row in rows:
        writer.writerow(row)

    csv_file.seek(0)

    return csv_file


def zip_up_files(*file_buffers: tuple[str, io.IOBase]):
    pass


def prepare_csv_response(
    acs, table_metadata, geo_metadata, valid_geo_ids, data
):
    dicts = [convert_row_to_dict(row) for row in data]
    data_file = io.StringIO()
    zip_buffer = io.BytesIO()

    table_file = tup_rows_to_csv_io(
        table_metadata_to_rows(table_metadata),
        (
            "column_id",
            "table_id",
            "table_title",
            "column_name",
            "universe",
            "denominator_column_id",
        ),
    )

    geography_file = tup_rows_to_csv_io(
        geo_metadata_to_rows(geo_metadata),
        (
            "geoid",
            "name",
            "sumlevel",
        ),
    )

    fieldnames = dicts[0].keys()
    writer = csv.DictWriter(data_file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(dicts)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        data_file.seek(0)

        zipped.writestr("geography_metadata.csv", geography_file.getvalue())
        zipped.writestr("table_metadata.csv", table_file.getvalue())
        zipped.writestr("data_table.csv", data_file.getvalue())

    zip_buffer.seek(0)

    timestamp = datetime.datetime.now()

    filename = f"d3_download_{timestamp.strftime('%Y%m%d')}.zip"

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


def prepare_shape_response(
    acs, table_metadata, geo_metadata, valid_geo_ids, data
):
    pass


def create_geo_metadata_sheet(geo_metadata):
    pass


def create_table_metadata_sheet(table_metadata):
    pass


def prepare_excel_response(
    acs, table_metadata, geo_metadata, valid_geo_ids, data
):
    pass


def prepare_geojson_response(
    acs,
    table_metadata,
    geo_metadata,
    data,
    properties_factory=lambda row, _: convert_row_to_dict(row),
):

    
    result = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(row.geom),  # where do we get this?
                "properties": properties_factory(row, data),
            }
            for row in geo_metadata
        ],
        "metadata": {
            "release": acs
        }
    }

    if table_metadata is not None:
        result["metadata"]["tables"] = table_metadata

    return jsonify(result)


def prepare_json_response(
    acs, table_metadata, geo_metadata, valid_geo_ids, data
):
    release = ACS_NAMES[acs].copy()
    release["id"] = acs

    def pack_geography(geoid, geo_obj):
        if geo_obj is None:
            return {"name": geoid}
        return {"name": geo_obj["display_name"]}

    response = {
        "tables": table_metadata,
        "geography": {
            geoid: pack_geography(
                geoid, geo_metadata.get(geoid)
            )  # geo_metadata.get(geoid, {"geoid": geoid})
            for geoid in valid_geo_ids
        },
        "release": release,
        "data": {row.geoid: pack_tables(row) for row in data},
    }

    response = json.dumps(response)
    resp = make_response(response)

    resp.headers.set("Content-Type", "application/json")
    resp.headers.set("Cache-Control", "public,max-age=86400")  # 1 day

    return resp
