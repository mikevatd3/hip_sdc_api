ALLOWED_ACS = [
    "d3_present",
    "acs2022_5yr",
    "acs2021_5yr",
    "acs2019_5yr",
    "acs2018_5yr",
    "acs2017_5yr",
    "acs2016_5yr",
    "acs2014_5yr",
    "acs2013_5yr",
    "acs2012_5yr",
    "acs2011_5yr",
    "d3_past",
]

PARENT_CHILD_CONTAINMENT = {
    "040": [
        "050",
        "060",
        "101",
        "140",
        "150",
        "500",
        "610",
        "620",
        "950",
        "960",
        "970",
    ],
    "050": ["060", "101", "140", "150"],
    "140": ["101", "150"],
    "150": ["101"],
}

UNOFFICIAL_CHILDREN = {
    "160": ["140", "150"],
    "310": ["160", "860"],
    "040": ["310", "860"],
    "050": [
        "160",
        "860",
        "950",
        "960",
        "970",
    ],
}

ACS_NAMES = {
    "acs2021_5yr": {"name": "ACS 2021 5-year", "years": "2017-2021"},
    "acs2019_5yr": {"name": "ACS 2019 5-year", "years": "2015-2019"},
    "acs2018_5yr": {"name": "ACS 2018 5-year", "years": "2014-2018"},
    "acs2017_5yr": {"name": "ACS 2017 5-year", "years": "2013-2017"},
    "acs2016_5yr": {"name": "ACS 2016 5-year", "years": "2012-2016"},
    "acs2014_5yr": {"name": "ACS 2014 5-year", "years": "2010-2014"},
    "acs2013_5yr": {"name": "ACS 2013 5-year", "years": "2009-2013"},
    "acs2012_5yr": {"name": "ACS 2012 5-year", "years": "2008-2012"},
    "acs2011_5yr": {"name": "ACS 2011 5-year", "years": "2007-2011"},
    "d3_present": {"name": "Data Driven Detroit", "years": "2018-2021"},
    "d3_past": {"name": "Data Driven Detroit", "years": "2015-2018"},
}


SUMLEV_NAMES = {
    "010": {"name": "nation", "plural": ""},
    "020": {"name": "region", "plural": "regions"},
    "030": {"name": "division", "plural": "divisions"},
    "040": {"name": "state", "plural": "states", "tiger_table": "state"},
    "050": {"name": "county", "plural": "counties", "tiger_table": "county"},
    "060": {
        "name": "county subdivision",
        "plural": "county subdivisions",
        "tiger_table": "cousub",
    },
    "101": {"name": "block", "plural": "blocks", "tiger_table": "tabblock"},
    "140": {
        "name": "census tract",
        "plural": "census tracts",
        "tiger_table": "tract",
    },
    "150": {
        "name": "block group",
        "plural": "block groups",
        "tiger_table": "bg",
    },
    "160": {"name": "place", "plural": "places", "tiger_table": "place"},
    "170": {
        "name": "consolidated city",
        "plural": "consolidated cities",
        "tiger_table": "concity",
    },
    "230": {
        "name": "Alaska native regional corporation",
        "plural": "Alaska native regional corporations",
        "tiger_table": "anrc",
    },
    "250": {
        "name": "native area",
        "plural": "native areas",
        "tiger_table": "aiannh250",
    },
    "251": {
        "name": "tribal subdivision",
        "plural": "tribal subdivisions",
        "tiger_table": "aits",
    },
    "252": {
        "name": "native area (reservation)",
        "plural": "native areas (reservation)",
        "tiger_table": "aiannh252",
    },
    "254": {
        "name": "native area (off-trust land)",
        "plural": "native areas (off-trust land)",
        "tiger_table": "aiannh254",
    },
    "256": {
        "name": "tribal census tract",
        "plural": "tribal census tracts",
        "tiger_table": "ttract",
    },
    "300": {"name": "MSA", "plural": "MSAs", "tiger_table": "metdiv"},
    "310": {"name": "CBSA", "plural": "CBSAs", "tiger_table": "cbsa"},
    "314": {
        "name": "metropolitan division",
        "plural": "metropolitan divisions",
        "tiger_table": "metdiv",
    },
    "330": {"name": "CSA", "plural": "CSAs", "tiger_table": "csa"},
    "335": {
        "name": "combined NECTA",
        "plural": "combined NECTAs",
        "tiger_table": "cnecta",
    },
    "350": {"name": "NECTA", "plural": "NECTAs", "tiger_table": "necta"},
    "364": {
        "name": "NECTA division",
        "plural": "NECTA divisions",
        "tiger_table": "nectadiv",
    },
    "400": {
        "name": "urban area",
        "plural": "urban areas",
        "tiger_table": "uac",
    },
    "500": {
        "name": "congressional district",
        "plural": "congressional districts",
        "tiger_table": "cd",
    },
    "610": {
        "name": "state senate district",
        "plural": "state senate districts",
        "tiger_table": "sldu",
    },
    "620": {
        "name": "state house district",
        "plural": "state house districts",
        "tiger_table": "sldl",
    },
    "795": {"name": "PUMA", "plural": "PUMAs", "tiger_table": "puma"},
    "850": {"name": "ZCTA3", "plural": "ZCTA3s"},
    "860": {"name": "ZCTA5", "plural": "ZCTA5s", "tiger_table": "zcta520"},
    "950": {
        "name": "elementary school district",
        "plural": "elementary school districts",
        "tiger_table": "elsd",
    },
    "960": {
        "name": "secondary school district",
        "plural": "secondary school districts",
        "tiger_table": "scsd",
    },
    "970": {
        "name": "unified school district",
        "plural": "unified school districts",
        "tiger_table": "unsd",
    },
}

state_fips = {
    "01": "Alabama",
    "02": "Alaska",
    "04": "Arizona",
    "05": "Arkansas",
    "06": "California",
    "08": "Colorado",
    "09": "Connecticut",
    "10": "Delaware",
    "11": "District of Columbia",
    "12": "Florida",
    "13": "Georgia",
    "15": "Hawaii",
    "16": "Idaho",
    "17": "Illinois",
    "18": "Indiana",
    "19": "Iowa",
    "20": "Kansas",
    "21": "Kentucky",
    "22": "Louisiana",
    "23": "Maine",
    "24": "Maryland",
    "25": "Massachusetts",
    "26": "Michigan",
    "27": "Minnesota",
    "28": "Mississippi",
    "29": "Missouri",
    "30": "Montana",
    "31": "Nebraska",
    "32": "Nevada",
    "33": "New Hampshire",
    "34": "New Jersey",
    "35": "New Mexico",
    "36": "New York",
    "37": "North Carolina",
    "38": "North Dakota",
    "39": "Ohio",
    "40": "Oklahoma",
    "41": "Oregon",
    "42": "Pennsylvania",
    "44": "Rhode Island",
    "45": "South Carolina",
    "46": "South Dakota",
    "47": "Tennessee",
    "48": "Texas",
    "49": "Utah",
    "50": "Vermont",
    "51": "Virginia",
    "53": "Washington",
    "54": "West Virginia",
    "55": "Wisconsin",
    "56": "Wyoming",
    "60": "American Samoa",
    "66": "Guam",
    "69": "Commonwealth of the Northern Mariana Islands",
    "72": "Puerto Rico",
    "78": "United States Virgin Islands",
}

# When expanding a container geoid shorthand (i.e. 140|05000US12127),
# use this ACS. It should always be a 5yr release so as to include as
# many geos as possible.
release_to_expand_with = ALLOWED_ACS[1]
# When table searches happen without a specified release, use this
# release to do the table search.
default_table_search_release = ALLOWED_ACS[1]

# Allowed TIGER releases in newest order
ALLOWED_TIGER = [
    "tiger2021",
]

allowed_searches = ["table", "profile", "topic", "all"]
supported_formats = ['csv', 'geojson', 'shapefile', 'excel']
