from enum import Enum, auto
from urllib.parse import quote
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base
import tomli

with open("config.toml", "rb") as f:
    config = tomli.load(f)

HOST, DBNAME, USERNAME, PASSWORD, PORT = config["db"].values()


connection_string = (
    f"postgresql+psycopg2://{USERNAME}:{quote(PASSWORD)}@{HOST}:{PORT}/{DBNAME}"
)


public_engine = create_engine(connection_string)


tiger_engine = create_engine(
    connection_string, connect_args={"options": "-csearch_path=tiger2021"}
)

metadata_engine = create_engine(
    connection_string, connect_args={"options": "-csearch_path=d3_metadata"}
)


class DataParadigm(Enum):
    D3 = auto()
    CR = auto()


Base.metadata.create_all(metadata_engine)

MetadataSession = sessionmaker(metadata_engine)
PublicSession = sessionmaker(public_engine)


def make_data_session(year: str, paradigm: DataParadigm):
    if paradigm == DataParadigm.CR:
        schema_string = f"acs{year}_5yr"
    elif paradigm == DataParadigm.D3:
        if year not in ["past", "present"]:
            raise ValueError(
                "You can only provide 'past' or 'present' as a D3 year"
            )
        schema_string = f"d3_{year}"
    else:
        raise ValueError("Invalid paradigm, please use DataParadigm enum.")

    engine = create_engine(
        f"postgresql+psycopg2://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}",
        connect_args={"options": f"-csearch_path={schema_string}"},
    )

    return sessionmaker(engine)
