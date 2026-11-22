-- NOAA Storm Events: typed, damage strings parsed, one row per EVENT_ID (latest load wins).
drop table if exists staging.storm_events cascade;
create table staging.storm_events as
with typed as (
  select
    staging.try_int(event_id)::bigint                           as event_id,
    staging.try_int(episode_id)::bigint                         as episode_id,
    staging.try_int(state_fips)                                 as state_fips,
    upper(btrim(state))                                         as state_name,
    staging.try_int(year)                                       as data_year,
    btrim(event_type)                                           as event_type,
    upper(btrim(cz_type))                                       as cz_type,
    staging.try_int(cz_fips)                                    as cz_fips,
    cz_name,
    wfo,
    staging.storm_ts(begin_yearmonth, begin_day, begin_time)    as begin_local,
    staging.storm_ts(end_yearmonth, end_day, end_time)          as end_local,
    cz_timezone,
    coalesce(staging.try_int(injuries_direct), 0)               as injuries_direct,
    coalesce(staging.try_int(injuries_indirect), 0)             as injuries_indirect,
    coalesce(staging.try_int(deaths_direct), 0)                 as deaths_direct,
    coalesce(staging.try_int(deaths_indirect), 0)               as deaths_indirect,
    nullif(btrim(damage_property), '')                          as damage_property_raw,
    nullif(btrim(damage_crops), '')                             as damage_crops_raw,
    staging.parse_damage(damage_property)                       as damage_property_usd,
    staging.parse_damage(damage_crops)                          as damage_crops_usd,
    staging.try_numeric(magnitude)                              as magnitude,
    nullif(magnitude_type, '')                                  as magnitude_type,
    nullif(tor_f_scale, '')                                     as tor_f_scale,
    nullif(flood_cause, '')                                     as flood_cause,
    staging.try_numeric(begin_lat)                              as begin_lat,
    staging.try_numeric(begin_lon)                              as begin_lon,
    nullif(source, '')                                          as report_source,
    event_narrative,
    _partition_key,
    _object_key,
    _source_row,
    _loaded_at
  from raw.noaa_storm_events_details
)
select distinct on (t.event_id)
  t.*,
  -- Storm Events state codes differ from Census for territories (seed) and use 81-95 for
  -- marine / Great Lakes areas.
  coalesce(sc.census_state_fips::int, t.state_fips)              as census_state_fips,
  (sc.storm_state_fips is null and t.state_fips >= 80)            as is_marine_area
from typed t
left join staging.seed_storm_events_state_codes sc on sc.storm_state_fips::int = t.state_fips
where t.event_id is not null
order by t.event_id, t._loaded_at desc, t._object_key desc;

alter table staging.storm_events add primary key (event_id);
create index on staging.storm_events (state_fips, cz_type, cz_fips);
