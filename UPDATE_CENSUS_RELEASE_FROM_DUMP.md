## Update Census Release from Database Dump
These instructions are for adding new Census releases to D3's Census API that powers SDC and HIP. These instructions assume that you have four database dump files from Amanda like `file_name.dump` one for each year and release (1-year and 5-year) to be added. 

#### SFTP Dump Files to Server
SFTP dump files to D3's Census API Server
```bash
sftp username@165.227.87.138
>>> cd /home/sdcapi/census-api/data
>>> put file_name1.dump
>>> put file_name2.dump
>>> put file_name3.dump
>>> put file_name4.dump
```

#### Extract Dump Files 
- Log in to D3's Census API Server

```bash
>>> cd /home/sdcapi/census-api/data
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name1.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name2.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name3.dump
>>> pg_restore --verbose -c --if-exists -d census -U postgres file_name4.dump
```

#### Add Census Metadata
Metadata tables also need to be updated for the new releases to be usable. Please follow the instructions for updating that repo and updating metadata tables here:
https://github.com/NiJeLorg/census-table-metadata/blob/master/UPDATING_CENSUS_METADATA.md


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



