def prepare_geojson_response(
    acs,
    table_metadata,
    geo_metadata: dict[str,dict],
    valid_geo_ids,
    data: list,
):
    ic(geo_metadata)
    result = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(geo_metadata[row.geoid].pop("geom")),  # nasty
                "properties": {
                    **geo_metadata[row.geoid],
                    **convert_row_to_dict(row)
                }
            }
            for row in data
        ],
        "metadata": {
            "release": acs
        }
    }

    if table_metadata is not None:
        result["metadata"]["tables"] = table_metadata

    return jsonify(result)


