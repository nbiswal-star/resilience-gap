-- Conformed county reference: Census Vintage 2025 counties (current geography, incl. the nine
-- Connecticut planning regions) UNION the 2020 Census county list (legacy geography, incl. the
-- eight pre-2022 Connecticut counties that older NOAA/FEMA records still use).
drop table if exists staging.county_ref cascade;
create table staging.county_ref as
with ref as (
  select statefp || countyfp                         as county_fips,
         statefp                                     as state_fips,
         state                                       as state_abbr,
         countyname                                  as county_name
  from raw.census_county_reference
  where statefp ~ '^[0-9]{2}$' and countyfp ~ '^[0-9]{3}$'
),
pop as (
  select lpad(state, 2, '0') || lpad(county, 3, '0') as county_fips,
         lpad(state, 2, '0')                         as state_fips,
         stname                                      as state_name,
         ctyname                                     as county_name,
         staging.try_int(popestimate2020)            as pop_2020,
         staging.try_int(popestimate2021)            as pop_2021,
         staging.try_int(popestimate2022)            as pop_2022,
         staging.try_int(popestimate2023)            as pop_2023,
         staging.try_int(popestimate2024)            as pop_2024,
         staging.try_int(popestimate2025)            as pop_2025
  from raw.census_popest_county
  where sumlev = '050'
  union all
  -- Puerto Rico municipios (separate Census file; YEAR 2..7 = July 1 of 2020..2025)
  select '72' || lpad(municipio, 3, '0'), '72', 'Puerto Rico', max(name),
         max(staging.try_int(popestimate)) filter (where year = '2'),
         max(staging.try_int(popestimate)) filter (where year = '3'),
         max(staging.try_int(popestimate)) filter (where year = '4'),
         max(staging.try_int(popestimate)) filter (where year = '5'),
         max(staging.try_int(popestimate)) filter (where year = '6'),
         max(staging.try_int(popestimate)) filter (where year = '7')
  from raw.census_popest_pr_municipio
  where sumlev = '050' and municipio ~ '^[0-9]{1,3}$'
  group by municipio
),
states as (
  select distinct statefp as state_fips, state as state_abbr from raw.census_county_reference
  where statefp ~ '^[0-9]{2}$'
),
state_names as (
  select distinct lpad(state, 2, '0') as state_fips, stname as state_name
  from raw.census_popest_county where sumlev = '040'
  union select '72', 'Puerto Rico'
)
select
  coalesce(p.county_fips, r.county_fips)                          as county_fips,
  coalesce(p.state_fips, r.state_fips)                            as state_fips,
  s.state_abbr,
  sn.state_name,
  coalesce(p.county_name, r.county_name)                          as county_name,
  staging.county_name_key(coalesce(p.county_name, r.county_name)) as name_key,
  p.county_fips is not null                                       as in_popest_2025,  -- incl. PR municipios
  r.county_fips is not null                                       as in_reference_2020,
  p.pop_2020, p.pop_2021, p.pop_2022, p.pop_2023, p.pop_2024, p.pop_2025
from pop p
full outer join ref r on r.county_fips = p.county_fips
left join states s on s.state_fips = coalesce(p.state_fips, r.state_fips)
left join state_names sn on sn.state_fips = coalesce(p.state_fips, r.state_fips);

alter table staging.county_ref add primary key (county_fips);
create index on staging.county_ref (state_abbr, name_key);
