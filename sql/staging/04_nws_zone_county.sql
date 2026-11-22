-- Versioned NWS zone -> county crosswalk.
--  staging.nws_zone_snapshot        one row per crosswalk version (bpDDmonYY = valid date)
--  staging.nws_zone_county_version  (version, zone, county) pairs exactly as published
-- A version is in effect from its valid date until the day before the next known version.
-- If the next known version is more than gap_threshold_days later, intermediate versions are
-- missing from every available source (NWS and the Internet Archive); such a version is flagged
-- in_gap and zone matches during its validity must be name-verified.
drop table if exists staging.nws_zone_county cascade;   -- replaced by the versioned tables
drop table if exists staging.nws_zone_county_version cascade;
drop table if exists staging.nws_zone_snapshot cascade;

create table staging.nws_zone_county_version as
select distinct on (snapshot_file, state_zone, county_fips)
  substring(_partition_key from 'file=(bp[0-9]{2}[a-z]{2}[0-9]{2})') as snapshot_file,
  upper(btrim(state_zone))                              as state_zone,
  btrim(fips)                                           as county_fips,
  upper(btrim(state))                                   as state_abbr,
  btrim(zone)                                           as zone,
  btrim(name)                                           as zone_name,
  staging.zone_name_key(name)                           as zone_name_key,
  btrim(county)                                         as county_name,
  _object_key
from raw.nws_zone_county
where btrim(fips) ~ '^[0-9]{5}$' and upper(btrim(state_zone)) ~ '^[A-Z]{2}[0-9]{3}$'
order by snapshot_file, state_zone, county_fips, _source_row;

alter table staging.nws_zone_county_version add primary key (snapshot_file, state_zone, county_fips);
create index on staging.nws_zone_county_version (state_zone, zone_name_key);

create table staging.nws_zone_snapshot as
with s as (
  select
    snapshot_file,
    staging.nws_file_date(snapshot_file)                                 as effective_date,
    case when bool_or(_object_key like '%/internet_archive/%') then 'internet_archive'
         else 'nws' end                                                  as provenance_tier,
    count(*)                                                             as zone_county_pairs,
    count(distinct state_zone)                                           as zones
  from staging.nws_zone_county_version
  group by snapshot_file
)
select s.*,
  lead(effective_date) over (order by effective_date)                    as next_effective_date,
  coalesce(lead(effective_date) over (order by effective_date) - 1,
           date '9999-12-31')                                            as valid_to,
  coalesce(lead(effective_date) over (order by effective_date) - effective_date
           > p.zone_gap_threshold_days, false)                           as in_gap
from s cross join ops.active_profile p;

alter table staging.nws_zone_snapshot add primary key (snapshot_file);
