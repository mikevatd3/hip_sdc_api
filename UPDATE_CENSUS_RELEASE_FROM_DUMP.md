## Update Census Release from Database Dump
These instructions are for adding new Census releases to D3's Census API that powers SDC and HIP. These instructions assume that you have four database dump files from Amanda like `file_name.dump` one for each year and release (1-year and 5-year) to be added. 

#### Pull and Extract new TIGER Data
- Visit https://censusreporter.tumblr.com/post/73727555158/easier-access-to-acs-data and find the link to the latest `TIGER Geodata`. Save that link for future use in the `wget` command below. 
- Log in to D3's Census API Server

```bash
>>> sftp username@165.227.87.138
>>> cd /home/sdcapi/census-api/data
>>> wget "https://census-extracts.b-cdn.net/tigerYEAR_backup.sql.gz" <-- Example link
>>> zcat tigerYEAR_backup.sql.gz | psql -q -U census census
```

#### SFTP Dump Files to Server
SFTP dump files to D3's Census API Server

```bash
>>> sftp username@165.227.87.138
>>> cd /home/sdcapi/census-api/data
>>> put file_name1.dump
>>> put file_name2.dump
>>> put file_name3.dump
>>> put file_name4.dump
```

#### Extract Dump Files 
- Log in to D3's Census API Server

```bash
>>> ssh username@165.227.87.138
>>> cd /home/sdcapi/census-api/data
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name1.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name2.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name3.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name4.dump
```

#### Add Census Metadata
Metadata tables also need to be updated for the new releases to be usable. Please follow the instructions for updating that repo and updating metadata tables here:
https://github.com/NiJeLorg/census-table-metadata/blob/master/UPDATING_CENSUS_METADATA.md


#### Enable New ACS Release
- On your local machine, open `api.py` in `census_extractomatic`.
- In the array `allowed_acs`, add the name of the newest acs release (ex. `acs2019_5yr`). Add the newest release at the top, but after the `d3_present` release in the array.
- In the array `allowed_tiger` add the name of the newest TIGER release (ex. `tiger2019`). Add the newest release at the top.
- Once changed, push changes to github

```bash
>>> git commit -m 'message'
>>> git push origin master
```

Then pull in changes on the server:

```bash
>>> ssh username@165.227.87.138
>>> cd /home/sdcapi/census-api/
>>> git pull origin master
```

While still logged into the webserver, restart the webserver and cache services:

```bash
>>> service nginx restart
>>> service census-api restart
>>> service memcached restart
```

#### Potential Problem and Workaround
When updating the 2019 ACS, the database user `postgres` was assigned ownership over all the imported tables and views. This was causing a 500 error for the API, since it uses the `census` database user to perfrom database queries. Running the following in `psql` solved the problem:

For views:
```sql
DO $$
DECLARE 
  cmd text;
BEGIN
  FOR cmd in SELECT format('ALTER VIEW %I.%I.%I OWNER TO %I;', table_catalog, table_schema, table_name, 'census' ) FROM information_schema.views WHERE table_schema = 'acs2019_5yr'
  LOOP
    EXECUTE cmd;
  END LOOP;
END;
$$;
```

For tables:
```sql
DO $$
DECLARE 
  cmd text;
BEGIN
  FOR cmd in SELECT format('ALTER TABLE %I.%I.%I OWNER TO %I;', table_catalog, table_schema, table_name, 'census') FROM information_schema.tables WHERE table_schema = 'acs2019_5yr'
  LOOP
    EXECUTE cmd;
  END LOOP;
END;
$$;
```



