# The D3 metadata system

This is the system that supports the aggregation tools that we use for SDC / HIP
though it could be used for anything.


## Suggestions from Arjan Codes - ways to break up your code
### Interface boundaries
- ABCs, protocols -- not using this enough currently.

### Package boundaries
- fix & publish d3_metadata
  - used in aggregation pipeline
  - used in metadata interface management
  - used in api to deliver metadata
       
### System boundaries
- Arguably this is the client-api split, but the separation of concerns here is very poorly done.

## Getting the thing to run 
```bash
    export FLASK_APP=censusextractomatic/api.py
    flask run
```

## A historical note
- CR before used reverse polish notation (hence 'rpn_string').

# TODO

## BUILD
- [x] Get all example data points showing
- [x] Figure out floating column chart issue
- [x] Get data drawer working for d3 datapoints
- [x] Fix stat-list year error for census datapoints
- [x] Double check updates to old api -- using old version of SQLAlchemy
- [x] Translator for old RPN strings to new lesp strings.
- [ ] Rebuild fixtures to only include 5114, Detroit, Wayne County, & Michigan
    - [ ] Include entire structure
        - [ ] ACS
        - [ ] D3
        - [ ] TIGER
- [ ] Add admin password
- [ ] Launch new api (?)
- [ ] Fix factoids
- [ ] Add the navigation back to the page with a loop
- [ ] Add grouped option to rows
- [ ] Figure out over-time template
- [ ] Figure out combining single Geo and custom Geo templates
- [ ] Fix d3_present default for acs table view 
- [ ] Go over errors from lesp again
    - [ ] Looks like there's something with MOEs that needs to be looked at further
- [ ] Figure out undefined data in the chart documentation string
- [ ] Fix the postgres timeout error
    - [ ] I can fix it acutely, but happens periodically
- [ ] Move page structure into db models (WAGTAIL ?)
- [ ] Break-out geoprofile into its own package
    - [ ] Think about how to register templates
- [ ] Generalize overtime pie chart to allow for comparison
- [ ] Add more tests for lesp.py
- [ ] Build on api_client ability to request / receive multiple table_ids -- properly parallel!
- [ ] Add UNDER CONSTRUCTION functionality
- [ ] Fix the data drawer showing 0 for errors when it should show empty string.
- [ ] Add comparison-type stat list

## REFACTOR

- [ ] Make the metadata api item the same on both client and api
- [ ] Fix build metadata function on client side to just consume api response as-is
- [ ] Add timeframe to build data request function to reuse the api metadata response

- [ ] General workspace clean-up
    - [ ] Client side
        - [x] Scrub a dub API client
        - [x] Remove unused files / code
        - [x] Find and remove print statements.
        - [x] Find a good name for rpn_string
    - [ ] API side
        - [ ] Remove unused files / code
        - [ ] Find and remove print statements.
- [ ] Clean up d3_past, d3_present editions confusion
    - [ ] Start duplicating schemas on API db
    - [ ] Once complete, then remove vestigial schema alias code
- [ ] Wrap the build_metadata_table on client side in Result
- [ ] Figure out installing d3metadata as a local library
    - [ ] Include the pydantic schemas
- [ ] Break-out d3_metadata into its own package
    - [ ] Have separate pydantic models for client-side TableMetadata and API-side TableMetadata
- [ ] Re-do fixtures to remove index column
    - [x] Then remove all 'local api' management code
- [ ] Rebuild custom geography code.


# DONE

- [x] Build fixture loading script
- [x] Get API running locally
- [x] Get app pulling data from the local API from fixtures
- [x] Get Flask printing all requests to stdout
- [x] Add census_name_lookup to new tiger2021 schema
- [x] Add moe tables to fixtures
- [x] Fix multiple pulls of same table
- [x] Get SHOW DATA running properly
- [x] Get handle nulls in new build_item correctly
- [x] Check single Geo template runs from local API
- [x] Get metadata response running locally with blueprint
- [x] Get flask-admin running locally
- [x] HACK Add present / past edition functionality to admin
- [x] Get API response providing present / past
- [x] Add present / past / undesignated to models
- [x] Add a d3 datapoint to the test template
- [x] Get client identifying correct timeframe from table response
- [x] Build TableMetadata factory
- [x] Fix the year showing schema-name thing
- [x] Make the past / present selection on Flask Admin nice
- [x] Fix the 'grab_primary_editions' function
- [x] Clean up the upper / lower on the different apis.
- [x] Remove duplicated TimeFrame enum (data_design.py, metadata.py)
- [x] Appropriately manage endpoint in the 'fill_metadata_pool' method
- [x] Remove Michigan filter from api_client in front-end
- [x] Build on api_client ability receive multiple table_ids
- [x] Carefully rework lesp.py to handle Nones
    - [x] Add tests for this.
- [x] Build on api ability to request multiple table_ids


