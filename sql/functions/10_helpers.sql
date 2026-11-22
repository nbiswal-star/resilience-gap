-- Small, pure helper functions used by staging models. All are IMMUTABLE and never raise on bad
-- input: they return NULL so that data-quality checks can count the problem instead of crashing.

create or replace function staging.try_int(v text) returns integer
language sql immutable parallel safe as $$
  select case when btrim(v) ~ '^[+-]?[0-9]{1,9}$' then btrim(v)::integer end
$$;

create or replace function staging.try_numeric(v text) returns numeric
language sql immutable parallel safe as $$
  select case when btrim(v) ~ '^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$'
              then btrim(v)::numeric end
$$;

-- ISO-8601 strings from OpenFEMA ("2024-09-11T00:00:00.000Z") -> calendar date, taken literally
-- (casting via timestamptz would shift dates by the session time zone).
create or replace function staging.iso_date(v text) returns date
language sql immutable parallel safe as $$
  select case when v ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' then left(v, 10)::date end
$$;

-- NOAA Storm Events damage strings: '' (not reported) -> NULL, '0.00K' -> 0, '1.5M' -> 1500000,
-- '2B' -> 2e9, a bare number -> itself. Anything else -> NULL (counted by DQ check).
create or replace function staging.parse_damage(v text) returns numeric
language sql immutable parallel safe as $$
  select case
    when v is null or btrim(v) = '' then null
    when btrim(v) ~* '^([0-9]+\.?[0-9]*|\.[0-9]+)[KMB]?$' then
      regexp_replace(btrim(v), '[KkMmBb]$', '')::numeric
      * case upper(right(btrim(v), 1)) when 'K' then 1e3 when 'M' then 1e6 when 'B' then 1e9 else 1 end
    else null
  end
$$;

-- Storm Events local date/time parts (BEGIN_YEARMONTH=202408, BEGIN_DAY=9, BEGIN_TIME=1430).
-- Returns NULL for impossible values (e.g. day 31 in a 30-day month) instead of rolling over.
create or replace function staging.storm_ts(yyyymm text, dd text, hhmm text) returns timestamp
language sql immutable parallel safe as $$
  with p as (
    select staging.try_int(yyyymm) ym, staging.try_int(dd) d, coalesce(staging.try_int(hhmm), 0) t
  )
  select case
    when ym is null or d is null then null
    when ym % 100 not between 1 and 12 or d < 1 or t % 100 > 59 or t / 100 > 23 then null
    when d > extract(day from (make_date(ym / 100, ym % 100, 1) + interval '1 month - 1 day')) then null
    else make_timestamp(ym / 100, ym % 100, d, t / 100, t % 100, 0)
  end
  from p
$$;

-- County-name matching key used for entity resolution across Census, FEMA and NWS.
-- "St. Mary's County" / "SAINT MARYS" / "St Marys (County)" -> 'STMARYS';
-- "Richmond city" (independent city) stays distinct from "Richmond County": 'RICHMONDCITY' vs 'RICHMOND'.
-- Accents are folded to ASCII ("Añasco" = "Anasco", "Doña Ana" = "Dona Ana"). The Census 2020
-- reference file stores such names double-encoded ("AÃ±asco", "RÃ<U+00AD>o Grande"); those
-- lowercase Spanish sequences are repaired first, so FEMA's unaccented spellings still match.
-- Only replace()/translate() are used, so no input can raise an error.
create or replace function staging.county_name_key(v text) returns text
language sql immutable parallel safe as $$
  select nullif(
    replace(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            regexp_replace(
              upper(translate(
                replace(replace(replace(replace(replace(replace(replace(coalesce(v, ''),
                  'Ã¡', 'a'), 'Ã©', 'e'), 'Ã' || chr(173), 'i'), 'Ã³', 'o'), 'Ãº', 'u'),
                  'Ã¼', 'u'), 'Ã±', 'n'),
                'ÁÉÍÓÚÜÑáéíóúüñ.''’`', 'AEIOUUNaeiouun')),
              '\(INDEPENDENT CITY\)', ' CITY ', 'g'),
            '\m(CITY AND BOROUGH|CENSUS AREA|PLANNING REGION|MUNICIPALITY|MUNICIPIO|COUNTY|PARISH|BOROUGH)\M',
            ' ', 'g'),
          '\m(SAINTE?|STE)\M', 'ST', 'g'),
        '[^A-Z0-9]+', ' ', 'g'),
      ' ', ''),
    '')
$$;

-- Zone-name key for verifying that a Storm Events zone (CZ_NAME) and an NWS crosswalk zone (NAME)
-- are the same zone. Only cosmetic variants are equated: case and punctuation; the words
-- COUNTY/CNTY; directional abbreviations (N = NORTH = NORTHERN, NE = NORTHEAST = NORTHEASTERN,
-- ...); ST = SAINT; MT = MOUNT; MTNS = MOUNTAINS. "NORTHERN COLLETON" vs "Inland Colleton" still
-- differ. Used only as evidence of zone identity; counties always come from the crosswalk.
create or replace function staging.zone_name_key(v text) returns text
language sql immutable parallel safe as $$
  select nullif(string_agg(
           case tok
             when 'NORTHERN' then 'N' when 'NORTH' then 'N'
             when 'SOUTHERN' then 'S' when 'SOUTH' then 'S'
             when 'EASTERN' then 'E' when 'EAST' then 'E'
             when 'WESTERN' then 'W' when 'WEST' then 'W'
             when 'NORTHEASTERN' then 'NE' when 'NORTHEAST' then 'NE'
             when 'NORTHWESTERN' then 'NW' when 'NORTHWEST' then 'NW'
             when 'SOUTHEASTERN' then 'SE' when 'SOUTHEAST' then 'SE'
             when 'SOUTHWESTERN' then 'SW' when 'SOUTHWEST' then 'SW'
             when 'SAINT' then 'ST' when 'MOUNT' then 'MT' when 'MOUNTAINS' then 'MTNS'
             else tok end, '' order by ord), '')
  from regexp_split_to_table(regexp_replace(upper(coalesce(v, '')), '[^A-Z0-9]+', ' ', 'g'), ' ')
       with ordinality as t(tok, ord)
  where tok <> '' and tok not in ('COUNTY', 'CNTY')
$$;

-- NWS correlation file name -> valid date: 'bp16ap26' -> 2026-04-16. NWS uses two-letter month
-- codes (ja fe mr ap my jn jl au se oc nv de), which to_date() cannot parse.
create or replace function staging.nws_file_date(v text) returns date
language sql immutable parallel safe as $$
  with m as (select regexp_match(v, 'bp([0-9]{2})([a-z]{2})([0-9]{2})') as g)
  select case when g is null then null else make_date(
    2000 + g[3]::int,
    array_position(array['ja','fe','mr','ap','my','jn','jl','au','se','oc','nv','de'], g[2]),
    g[1]::int) end
  from m
$$;

-- OpenFEMA designatedArea is the authoritative description of a declared area. Only the literal
-- value "Statewide" (case, surrounding and repeated whitespace ignored) means the whole state.
create or replace function staging.is_statewide_designation(designated_area text) returns boolean
language sql immutable parallel safe as $$
  select coalesce(lower(btrim(regexp_replace(designated_area, '\s+', ' ', 'g'))) = 'statewide',
                  false)
$$;

-- Classify one OpenFEMA DisasterDeclarationsSummaries record (one declared area):
--   statewide   designatedArea is literally "Statewide" (checked first; authoritative)
--   tribal      fipsCountyCode '000' for a named non-statewide area: a reservation, tribal
--               statistical area (OTSA/TDSA/SDTSA), Alaska Native village (ANV/ANVSA) or other
--               tribal area, identified by a non-zero placeCode or tribalRequest. Kept as its own
--               area; it is NEVER expanded to the counties of the state.
--               Also: a tribal request whose county code is not a known county.
--   county      state + county FIPS found in the Census county reference
--   unresolved  anything else (e.g. retired county FIPS); kept, never expanded
-- The order matters: code '000' alone does not mean "statewide" (1,102 of the 1,122 code-000
-- rows of 2000-2025 are tribal areas).
create or replace function staging.fema_area_level(
  designated_area text, fips_county_code text, place_code text, tribal_request boolean,
  county_known boolean
) returns text
language sql immutable parallel safe as $$
  select case
    when staging.is_statewide_designation(designated_area)                    then 'statewide'
    when fips_county_code = '000'
         and (coalesce(tribal_request, false)
              or coalesce(nullif(ltrim(place_code, '0'), ''), '') <> '')      then 'tribal'
    when fips_county_code = '000'                                             then 'unresolved'
    when coalesce(county_known, false)                                        then 'county'
    when coalesce(tribal_request, false)                                      then 'tribal'
    else 'unresolved'
  end
$$;

-- Federal fiscal year -> first fiscal year of its fixed five-year HMA reporting period.
-- Periods end in fiscal years divisible by 5: FY2001-2005, FY2006-2010, ..., FY2021-2025; FY2000
-- belongs to FY1996-2000. Fixed boundaries (like the fixed SVI cut points) classify a fiscal year
-- identically in every profile; curated.dim_hma_period clips each period to the profile years.
-- Federal fiscal year N runs from 1 October of year N-1 to 30 September of year N.
create or replace function staging.hma_period_start(fiscal_year integer) returns integer
language sql immutable parallel safe as $$
  select (5 * floor((fiscal_year - 1) / 5.0) + 1)::integer
$$;
