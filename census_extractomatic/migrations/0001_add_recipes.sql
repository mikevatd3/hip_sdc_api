CREATE SCHEMA tearsheet;

-- This table is necessary for the recipe feature
CREATE TABLE tearsheet.recipes (
    id       serial primary key,
    recipe   text not null
);
