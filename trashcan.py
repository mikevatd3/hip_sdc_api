@app.route("/1.0/data/compare/<acs>/<table_id>")
@qwarg_validate(
    {
        "within": {"valid": NonemptyString(), "required": True},
        "sumlevel": {"valid": OneOf(SUMLEV_NAMES), "required": True},
        "geom": {"valid": Bool(), "default": False},
    }
)
@crossdomain(origin="*")
def data_compare_geographies_within_parent(acs, table_id):
    # fill in trashcan.py if necessary

    # make sure we support the requested ACS release
    if acs not in ALLOWED_ACS:
        abort(404, f"The {acs} release isn't supported.")
    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    parent_geoid = request.qwargs.within

    # create the containers we need for our response
    comparison = {}

    # add some basic metadata about the comparison and data table requested.
    comparison["child_summary_level"] = request.qwargs.sumlevel
    comparison["child_geography_name"] = SUMLEV_NAMES.get(
        request.qwargs.sumlevel, {}
    ).get("name")
    comparison["child_geography_name_plural"] = SUMLEV_NAMES.get(
        request.qwargs.sumlevel, {}
    ).get("plural")

    result = db.session.execute(
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
           WHERE table_id=:table_ids
           ORDER BY column_id;"""
        ),
        {"table_ids": table_id},
    )
    table_metadata = ic(result.fetchall())

    if not table_metadata:
        abort(404, f"Table {table_id} isn't available in the {acs} release.")

    validated_table_id = table_metadata[0].table_id

    # get the basic table record, and add a map of columnID -> column name
    table_record = convert_row_to_dict(table_metadata[0])
    column_map = {}
    for record in table_metadata:
        record = convert_row_to_dict(record)
        if record["column_id"]:
            column_map[record["column_id"]] = {}
            column_map[record["column_id"]]["name"] = record["column_title"]
            column_map[record["column_id"]]["indent"] = record["indent"]

    table = {}
    table["census_release"] = ACS_NAMES.get(acs).get("name")
    table["table_id"] = validated_table_id
    table["table_name"] = table_record["table_title"]
    table["table_universe"] = table_record["universe"]
    table["denominator_column_id"] = table_record["denominator_column_id"]
    table["columns"] = column_map

    # add some data about the parent geography
    result = db.session.execute(
        text("SELECT * FROM geoheader WHERE geoid=:geoid;"),
        {"geoid": parent_geoid},
    )
    parent_geoheader = result.fetchone()
    parent_sumlevel = "%03d" % parent_geoheader._mapping["sumlevel"]

    parent_geography = {}
    parent_geography["geography"] = OrderedDict()
    parent_geography["geography"]["name"] = parent_geoheader._mapping["name"]
    parent_geography["geography"]["summary_level"] = parent_sumlevel

    comparison["parent_summary_level"] = parent_sumlevel
    comparison["parent_geography_name"] = SUMLEV_NAMES.get(
        parent_sumlevel, {}
    ).get("name")
    comparison["parent_name"] = parent_geoheader._mapping["name"]
    comparison["parent_geoid"] = parent_geoid

    child_geoheaders = get_child_geoids(
        acs, parent_geoid, child_summary_level, db.session
    )

    # start compiling child data for our response
    child_geoid_names = {
        geoheader.geoid: geoheader.name for geoheader in child_geoheaders
    }

    # get geographical data if requested
    child_geodata_map = {}
    if request.qwargs.geom:
        # get the parent geometry and add to API response
        result = db.session.execute(
            text(
                """SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,0.001), 5) as geometry
               FROM tiger2021.census_name_lookup
               WHERE full_geoid=:geo_ids;"""
            ),
            {"geo_ids": parent_geoid},
        )
        parent_geometry = result.fetchone()

        parent_dict = ic(convert_row_to_dict(parent_geometry))

        try:
            parent_geography._mapping["geography"]["geometry"] = json.loads(
                parent_geometry["geometry"]
            )
        except:
            # we may not have geometries for all sumlevs
            pass

        # get the child geometries and store for later
        result = db.session.execute(
            text(
                """SELECT geoid, ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom,0.001), 5) as geometry
               FROM tiger2021.census_name_lookup
               WHERE full_geoid IN :geo_ids
               ORDER BY full_geoid;"""
            ),
            {"geo_ids": tuple(child_geodata_map.keys())},
        )
        child_geodata = result.fetchall()
        child_geodata_map = dict(
            [
                (
                    record._mapping["geoid"],
                    json.loads(record._mapping["geometry"]),
                )
                for record in child_geodata
            ]
        )

    # make the where clause and query the requested census data table
    # get parent data first...
    result = db.session.execute(
        text("SELECT * FROM %s_moe WHERE geoid=:geoid" % (validated_table_id)),
        {"geoid": parent_geoheader._mapping["geoid"]},
    )
    parent_data = dict(result.fetchone()._mapping)
    parent_data.pop("geoid", None)
    column_data = []
    column_moe = []
    sorted_data = list(sorted(parent_data.items(), key=lambda tup: tup[0]))

    for (k, v), (_, moe_v) in zip(sorted_data[:-1:2], sorted_data[1::2]):
        column_data.append((k.upper(), v))
        column_moe.append((k.upper(), moe_v))

    parent_geography["data"] = OrderedDict(column_data)
    parent_geography["error"] = OrderedDict(column_moe)

    if child_geoheaders:
        # ... and then children so we can loop through with cursor
        child_geoids = [child._mapping["geoid"] for child in child_geoheaders]
        result = db.session.execute(
            text(
                "SELECT * FROM %s_moe WHERE geoid IN :geo_ids"
                % (validated_table_id)
            ),
            {"geo_ids": tuple(child_geoids)},
        )

        # grab one row at a time
        for record in result:
            record = dict(record._mapping)
            child_geoid = record.pop("geoid")

            child_data = OrderedDict()
            this_geo_has_data = False

            # build the child item
            child_data["geography"] = OrderedDict()
            child_data["geography"]["name"] = child_geoid_names[child_geoid]
            child_data["geography"]["summary_level"] = child_summary_level

            column_data = []
            column_moe = []
            sorted_data = list(sorted(record.items(), key=lambda tup: tup[0]))
            for (k, v), (_, moe_v) in zip(
                sorted_data[:-1:2], sorted_data[1::2]
            ):
                if v is not None and moe_v is not None:
                    this_geo_has_data = True

                column_data.append((k.upper(), v))
                column_moe.append((k.upper(), moe_v))
            child_data["data"] = OrderedDict(column_data)
            child_data["error"] = OrderedDict(column_moe)

            if child_geodata_map:
                try:
                    child_data["geography"]["geometry"] = child_geodata_map[
                        child_geoid.split("US")[1]
                    ]
                except:
                    # we may not have geometries for all sumlevs
                    pass

            if this_geo_has_data:
                child_geographies[child_geoid] = child_data

            # TODO Do we really need this?
            comparison["results"] = len(child_geographies)
    else:
        comparison["results"] = 0
  
    return jsonify(
        comparison={},
        table={},
        parent_geography={},
        child_geographies={},
    )



@app.route("/1.0/data/compare/<acs>/<table_id>")
@qwarg_validate(
    {
        "within": {"valid": NonemptyString(), "required": True},
        "sumlevel": {"valid": OneOf(SUMLEV_NAMES), "required": True},
        "geom": {"valid": Bool(), "default": False},
    }
)
@crossdomain(origin="*")
def data_compare_geographies_within_parent(acs, table_id):
    if acs not in ALLOWED_ACS:
        abort(404, f"The {acs} release isn't supported.")

    db.session.execute(text("SET search_path TO :acs, public;"), {"acs": acs})

    
    
