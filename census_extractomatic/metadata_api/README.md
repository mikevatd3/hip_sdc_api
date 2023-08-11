# The D3 metadata system

This is the system that supports the aggregation tools that we use for SDC / HIP
though it could be used for anything.

# TODO

## BUILD
- [x] Build fixture loading script
- [x] Get API running locally
 
```bash
export FLASK_APP=censusextractomatic/api.py
flask run
```

- [x] Get app pulling data from the local API from fixtures
- [x] Get Flask printing all requests to stdout
- [x] Add census_name_lookup to new tiger2021 schema
- [x] Add moe tables to fixtures
- [x] Fix multiple pulls of same table
- [x] Get SHOW DATA running properly
- [x] Get handle nulls in new build_item correctly
- [ ] Get metadata response running locally with blueprint
- [ ] Check single Geo template runs from local API
- [ ] Figure out combining single Geo and custom Geo templates
- [ ] Figure out over-time template
- [ ] Move page structure into db models (WAGTAIL ?)
- [ ] Add comparison type stat-list
- [ ] Generalize overtime pie chart to allow for comparison

## REFACTOR
- [ ] Re-do fixtures to remove index column
- [ ] Figure out installing d3metadata as a local library
- [ ] Get flask-admin running locally
- [ ] Rebuild fixtures to only include Detroit, Wayne 
    - [ ] Then include entire structure
        - [ ] ACS
        - [ ] D3
        - [ ] TIGER
    - [ ] Remove Michigan filter from api_client in front-end
