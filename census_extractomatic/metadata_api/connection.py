from enum import Enum, auto
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base
import tomli

with open("keys.toml", "rb") as f:
    config = tomli.load(f)

DBNAME, USERNAME, PASSWORD, HOST, PORT = config.values()

public_engine = create_engine(
    f"postgresql+psycopg2://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}",
)

metadata_engine = create_engine(
    f"postgresql+psycopg2://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}",
    connect_args={'options': '-csearch_path=d3_metadata'}
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
            raise ValueError("You can only provide 'past' or 'present' as a D3 year")
        schema_string = f"d3_{year}"
    else:
        raise ValueError("Invalid paradigm, please use DataParadigm enum.")

    engine = create_engine(
        f"postgresql+psycopg2://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}",
        connect_args={'options': f'-csearch_path={schema_string}'}
    )

    return sessionmaker(engine)
