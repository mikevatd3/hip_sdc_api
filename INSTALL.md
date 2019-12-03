## Installation

#### Installation Used for State of Detroit Child
On Ubuntu 14.04 LTS:

```bash
git clone https://github.com/NiJeLorg/census-api.git
sudo apt-add-repository -y ppa:ubuntugis/ubuntugis-unstable
sudo add-apt-repository -y ppa:ubuntugis/ppa
sudo apt-get update
sudo apt upgrade
sudo apt-get -y install libgdal1-dev libmemcached-dev libpq-dev gdal-bin python-gdal python3-gdal python-pip python-dev postgresql postgresql-contrib postgresql-9.5-postgis-scripts
export CPLUS_INCLUDE_PATH=/usr/include/gdal
export C_INCLUDE_PATH=/usr/include/gdal
pip install -r requirements.txt

sudo -u postgres psql
postgres=# CREATE DATABASE census;
postgres=# CREATE EXTENSION postgis;
postgres=# CREATE USER census WITH PASSWORD '********';
postgres=# GRANT ALL PRIVILEGES ON DATABASE "census" to census;
postgres=# GRANT USAGE ON SCHEMA public TO census;
postgres=# \q
psql -d census -U postgres
census=# CREATE EXTENSION postgis;


apt-get install -q -y memcached
sed -i "s/^-m 64$/-m 1024 -I 10485760/g" /etc/memcached.conf
service memcached restart


Example wget and zcat commands:
wget "https://s3.amazonaws.com/census-backup/tiger/2017/tiger2017_backup.sql.gz"

zcat tiger2017_backup.sql.gz | psql -q -U census census

Commands to restore Census dump file
tar -zxvf census.dump.tar.gz
pg_restore --verbose -c --if-exists -d census -U postgres census.dump


Added Census metadata outside of the project directory:
git clone https://github.com/NiJeLorg/census-table-metadata.git
psql -d census -U census -f census_metadata_drop.sql
psql -d census -U census -f census_metadata.sql
sudo psql -U postgres -d census -f census_metadata_load.sql


```



*(This is a work in progress)*

Due to the size of the ACS data we're using, it's relatively difficult to run everything locally for development. In practice we develop by connecting to a remote database on the EC2 instance.

#### Install pre-requisites for local development

The GDAL dependency we rely on for data downloads is a bit complicated to install. We also rely on postgres, postgis and memcached.

##### Install build dependencies on a Ubuntu 14.04 machine:

```bash
sudo apt-add-repository -y ppa:ubuntugis/ubuntugis-unstable
sudo apt-get update
sudo apt-get -y install libgdal1-dev libmemcached-dev libpq-dev
export CPLUS_INCLUDE_PATH=/usr/include/gdal
export C_INCLUDE_PATH=/usr/include/gdal
```

##### Install supporting services on a Mac with Homebrew:

We haven't had a good opportunity to try this on a clean mac, so help us out and submit a pull request if you can make these better.

```bash
brew install gdal postgis
brew install memcached
brew install libmemcached
```

##### Complete Python dependency installation

```bash
mkvirtualenv --no-site-packages census-api
pip install -r requirements.txt
add2virtualenv .
```

On OS X, if installation of pylibmc fails, install it separately using this command:

    pip install pylibmc==1.2.3 --install-option="--with-libmemcached=/usr/local/"

(Solution found on http://stackoverflow.com/questions/14803310/error-when-install-pylibmc-using-pip)

#### Load some data

For a quick start, it will be easier to load data from our [SQL dumps](http://censusreporter.tumblr.com/post/73727555158/easier-access-to-acs-data) than to run the [census-postgres-scripts](https://github.com/censusreporter/census-postgres-scripts) import. 

Again, we haven't tried this on a clean machine in a while, but it should be enough to load the most recent 1-year data and TIGER data. You should skip the 5-year data unless you have a lot of disk space to spare.

To initialize your database according to the default development config settings:

```bash
createdb census
psql census -c "CREATE EXTENSION postgis;"
dropuser --if-exists census
psql -c "CREATE USER census WITH PASSWORD 'censuspassword';"
psql -c 'GRANT ALL PRIVILEGES ON DATABASE "census" to census;'
```

To load the data, it should be something like this:

```bash

gzcat  acs2014_1yr_backup.sql.gz | PGPASSWORD="censuspassword" psql -U census census
```

#### Set up remote instance

```bash
# Start an EC2 instance

# Set up software on remote instance
fab -i ~/.ssh/censusreporter.ec2_key.pem \
    -u ubuntu \
    -H api.censusreporter.org \
    install_packages initial_config deploy
```
