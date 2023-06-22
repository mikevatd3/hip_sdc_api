import os
import tomli

with open("config.toml", "rb") as f:
    config = tomli.load(f)


class Config(object):
    SENTRY_DSN = os.environ.get('SENTRY_DSN')
    MAX_GEOIDS_TO_SHOW = 3500
    MAX_GEOIDS_TO_DOWNLOAD = 3500
    CENSUS_REPORTER_URL_ROOT = 'https://censusreporter.org'


class Production(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'postgresql://census:censuspassword@censusreporter.c7wefhiuybfb.us-east-1.rds.amazonaws.com:5432/census')
    MEMCACHE_ADDR = [os.environ.get('MEMCACHE_HOST', '127.0.0.1')]
    JSONIFY_PRETTYPRINT_REGULAR = False


class Development(Config):
    # For local dev, tunnel to the DB first:
    # ssh -i ~/.ssh/censusreporter.ec2_key.pem -L 5432:censusreporter.c7wefhiuybfb.us-east-1.rds.amazonaws.com:5432 ubuntu@52.71.251.119
    db_name=config['db']['name']
    db_username=config['db']['username']
    db_password=config['db']['password']
    db_port=config['db']['port']
    db_host=config['db']['host']

    SQLALCHEMY_DATABASE_URI = f"postgresql://{db_username}:password@{db_host}:{db_port}/{db_name}?password={db_password}"

    # or if using a local database, use this:
    # SQLALCHEMY_DATABASE_URI = 'postgresql://census:censuspassword@localhost/census'

    # Maybe change for local dev:
    # CENSUS_REPORTER_URL_ROOT = 'http://localhost:8000'

    MEMCACHE_ADDR = ['127.0.0.1']
    JSONIFY_PRETTYPRINT_REGULAR = False
