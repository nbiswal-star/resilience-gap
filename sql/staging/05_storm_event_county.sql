-- Entity resolution: each storm event -> one or more counties.
--  * CZ_TYPE = 'C': recorded against a county; FIPS = state_fips + cz_fips (territory codes
--    mapped to Census first).
--  * CZ_TYPE = 'Z': recorded against an NWS forecast zone. Zones are renumbered and redrawn over
--    time, so the mapping is DATE-EFFECTIVE (staging.storm_event_zone_resolution):
--      1. zone_effective_snapshot            the crosswalk version in effect on the event date
--                                             contains the zone (authoritative for that date)
--      2. zone_gap_snapshot_name_verified    as 1, but that version sits in a documented gap
--                                             (successor > gap_threshold_days later), so it is
--                                             accepted only if the zone NAME also agrees
--      3. zone_nearest_snapshot_name_verified no usable in-effect match; the nearest version (either
--                                             direction) holding the same zone code under the same
--                                             zone name is used
--      otherwise the event stays UNMAPPED with an explicit reason - a county is never guessed.
--    A zone can span several counties: damage/casualties are split equally
--    (allocation_weight = 1 / number of counties), so totals reconcile.
--  * CZ_TYPE = 'M' and marine pseudo-states (STATE_FIPS 81-95) have no county: excluded.
drop table if exists staging.storm_event_county cascade;
drop table if exists staging.storm_event_zone_resolution cascade;

create table staging.storm_event_zone_resolution as
with state_abbr as (
  select distinct state_fips::int as state_fips, state_abbr from staging.county_ref
  where state_abbr is not null
),
zev as (
  select e.event_id,
         s.state_abbr || lpad(e.cz_fips::text, 3, '0')  as state_zone,
         e.begin_local::date                            as event_date,
         e.cz_name,
         staging.zone_name_key(e.cz_name)               as name_key
  from staging.storm_events e
  join state_abbr s on s.state_fips = e.census_state_fips
  where e.cz_type = 'Z' and not e.is_marine_area and e.cz_fips is not null
    and e.begin_local is not null
),
in_effect as (       -- the version valid on the event date (at most one by construction)
  select z.event_id, sn.snapshot_file, sn.in_gap
  from zev z
  join staging.nws_zone_snapshot sn on z.event_date between sn.effective_date and sn.valid_to
),
eff_zone as (        -- the in-effect version contains the zone; record whether the name agrees
  select ie.event_id, ie.snapshot_file, ie.in_gap,
         bool_or(v.zone_name_key = z.name_key) as name_match
  from in_effect ie
  join zev z using (event_id)
  join staging.nws_zone_county_version v
    on v.snapshot_file = ie.snapshot_file and v.state_zone = z.state_zone
  group by ie.event_id, ie.snapshot_file, ie.in_gap
),
accepted_eff as (
  select * from eff_zone where not in_gap or name_match
),
nearest as (         -- fallback: nearest version with the same zone code AND the same zone name
  select distinct on (z.event_id) z.event_id, v.snapshot_file
  from zev z
  join staging.nws_zone_county_version v
    on v.state_zone = z.state_zone and v.zone_name_key = z.name_key
  join staging.nws_zone_snapshot sn on sn.snapshot_file = v.snapshot_file
  where not exists (select 1 from accepted_eff a where a.event_id = z.event_id)
  order by z.event_id, abs(sn.effective_date - z.event_date), sn.effective_date desc
),
zone_known as (
  select distinct state_zone from staging.nws_zone_county_version
)
select
  z.event_id,
  z.state_zone,
  z.event_date,
  z.cz_name,
  coalesce(a.snapshot_file, n.snapshot_file)                           as snapshot_file,
  case
    when a.event_id is not null and not a.in_gap then 'zone_effective_snapshot'
    when a.event_id is not null                   then 'zone_gap_snapshot_name_verified'
    when n.event_id is not null                   then 'zone_nearest_snapshot_name_verified'
  end                                                                  as match_method,
  ez.name_match                                                        as in_effect_name_match,
  case
    when a.event_id is not null or n.event_id is not null then null
    when zk.state_zone is null then 'zone_code_in_no_crosswalk_version'
    when ie.event_id is null   then 'before_first_version_and_no_same_name_version'
    when ez.event_id is null   then 'zone_not_in_effective_version_and_no_same_name_version'
    else 'gap_version_name_mismatch_and_no_same_name_version'
  end                                                                  as unmapped_reason
from zev z
left join in_effect ie   on ie.event_id = z.event_id
left join eff_zone ez    on ez.event_id = z.event_id
left join accepted_eff a on a.event_id = z.event_id
left join nearest n      on n.event_id = z.event_id
left join zone_known zk  on zk.state_zone = z.state_zone;

alter table staging.storm_event_zone_resolution add primary key (event_id);

create table staging.storm_event_county as
with county_based as (
  select e.event_id,
         lpad(e.census_state_fips::text, 2, '0') || lpad(e.cz_fips::text, 3, '0') as county_fips,
         'county_fips'::text as match_method,
         null::text          as snapshot_file
  from staging.storm_events e
  where e.cz_type = 'C' and not e.is_marine_area
    and e.census_state_fips is not null and e.cz_fips is not null
),
zone_based as (
  select r.event_id, v.county_fips, r.match_method, r.snapshot_file
  from staging.storm_event_zone_resolution r
  join staging.nws_zone_county_version v
    on v.snapshot_file = r.snapshot_file and v.state_zone = r.state_zone
  where r.match_method is not null
),
unioned as (
  select * from county_based
  union all
  select * from zone_based
)
select u.event_id, u.county_fips, u.match_method, u.snapshot_file,
       sn.provenance_tier                                   as crosswalk_tier,
       1.0 / count(*) over (partition by u.event_id)        as allocation_weight
from unioned u
left join staging.nws_zone_snapshot sn using (snapshot_file);

alter table staging.storm_event_county add primary key (event_id, county_fips);
