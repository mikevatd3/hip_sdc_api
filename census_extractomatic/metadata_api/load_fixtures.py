from typing import Any
from pathlib import Path
import csv
import os

import pandas as pd

from models import D3TableMetadata, D3VariableMetadata, D3EditionMetadata
from connection import MetadataSession, DataParadigm, make_data_session, public_engine


fixtures_path =Path("../../fixtures")
data_path = fixtures_path / "data"
metadata_path = fixtures_path / "metadata"

# Load d3 metadata
def make_none_safe(obj: dict[str, Any]):
    return {key: val if val else None for key, val in obj.items()}


def add_moe_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    value_columns = [col for col in df.columns if col != "geoid"]
    for col in value_columns:
        df[col + "_moe"] = None

    return df[["geoid"] + [col for col in sorted(df.columns) if col != "geoid"]]

with MetadataSession() as db:
    with open(metadata_path / "d3" / "d3_table_metadata.csv") as f:
        reader = csv.DictReader(f)
        safe_objs = [make_none_safe(obj) for obj in reader]

        # db.bulk_insert_mappings(D3TableMetadata, safe_objs)

    with open(metadata_path / "d3" / "d3_variable_metadata.csv") as f:
        reader = csv.DictReader(f)
        safe_objs = [make_none_safe(obj) for obj in reader]

        # db.bulk_insert_mappings(D3VariableMetadata, safe_objs)

    with open(metadata_path / "d3" / "d3_edition_metadata.csv") as f:
        reader = csv.DictReader(f)
        safe_objs = [make_none_safe(obj) for obj in reader]

        db.bulk_insert_mappings(D3EditionMetadata, safe_objs)
        db.commit()

for year in ["2016", "2021"]:
    CRSession = make_data_session(year, DataParadigm.CR)

    with CRSession() as db:
        table_name = "census_column_metadata"
        df = pd.read_csv(metadata_path / "census_reporter" / f"acs{year}_5yr_census_column_metadata.csv")
        # df.to_sql(table_name, db.get_bind())

        table_name = "census_table_metadata"
        df = pd.read_csv(metadata_path / "census_reporter" / f"acs{year}_5yr_census_table_metadata.csv")
        # df.to_sql(table_name, db.get_bind())

        for table in os.listdir(data_path / f"acs{year}_5yr"):
            table_name = Path(table).stem
            df = pd.read_csv(data_path / f"acs{year}_5yr" / table)
            # df.to_sql(table_name, db.get_bind())
            # moe = add_moe_columns(df)
            # moe.to_sql(table_name + "_moe", db.get_bind())

CRSession = make_data_session('2021', DataParadigm.CR)
with CRSession() as db:
    table_name = "geoheader"
    df = pd.read_csv(metadata_path / "acs2021_5yr_geoheader.csv")
    df.to_sql(table_name, db.get_bind(), if_exists="replace")

df = pd.read_csv("../../fixtures/metadata/tiger2021_census_name_lookup.csv")
# df.to_sql("census_name_lookup", public_engine, schema="tiger2021")

for year in ["present", "past"]:
    D3Session = make_data_session(year, DataParadigm.D3)

    with D3Session() as db:
        table_name = "census_column_metadata"
        df = pd.read_csv(metadata_path / "census_reporter" / f"d3_{year}_census_column_metadata.csv")
        # df.to_sql(table_name, db.get_bind())

        table_name = "census_table_metadata"
        df = pd.read_csv(metadata_path / "census_reporter" / f"d3_{year}_census_table_metadata.csv")
        # df.to_sql(table_name, db.get_bind())

        for table in os.listdir(data_path / f"d3_{year}"):
            table_name = Path(table).stem
            df = pd.read_csv(data_path / f"d3_{year}" / table)
            # df.to_sql(table_name, db.get_bind())
            # moe = add_moe_columns(df)
            # moe.to_sql(table_name + "_moe", db.get_bind())

"""
