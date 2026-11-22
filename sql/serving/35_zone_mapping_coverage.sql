-- How storm events were placed in counties, by event year (profile scope: years and states).
-- Exposes mapped / unmapped rates for zone-based events and the crosswalk tier used.
drop table if exists serving.zone_mapping_coverage cascade;
create table serving.zone_mapping_coverage as
with ev as (
  select e.event_id, extract(year from e.begin_local)::int as event_year, e.cz_type,
         e.is_marine_area, sa.state_abbr
  from staging.storm_events e
  left join (select distinct state_fips::int as state_fips, state_abbr from staging.county_ref
             where state_abbr is not null) sa on sa.state_fips = e.census_state_fips
  cross join ops.active_profile p
  where e.begin_local is not null
    and extract(year from e.begin_local) between p.start_year and p.end_year
    and (p.states is null or sa.state_abbr = any (p.states))
),
tiers as (
  select event_id, min(crosswalk_tier) as crosswalk_tier from staging.storm_event_county
  group by event_id
)
select
  ev.event_year,
  count(*) filter (where ev.cz_type = 'C' and not ev.is_marine_area)          as county_events,
  count(*) filter (where ev.cz_type = 'Z' and not ev.is_marine_area)          as zone_events,
  count(*) filter (where r.match_method = 'zone_effective_snapshot')          as zone_mapped_effective,
  count(*) filter (where r.match_method = 'zone_gap_snapshot_name_verified')  as zone_mapped_gap_name_verified,
  count(*) filter (where r.match_method = 'zone_nearest_snapshot_name_verified') as zone_mapped_nearest_name_verified,
  count(*) filter (where r.match_method is not null and t.crosswalk_tier = 'internet_archive')
                                                                              as zone_mapped_via_archive_version,
  count(*) filter (where ev.cz_type = 'Z' and not ev.is_marine_area and r.match_method is null)
                                                                              as zone_unmapped,
  round(count(*) filter (where r.match_method is not null)::numeric
        / nullif(count(*) filter (where ev.cz_type = 'Z' and not ev.is_marine_area), 0), 4)
                                                                              as zone_mapped_rate,
  count(*) filter (where r.unmapped_reason = 'zone_code_in_no_crosswalk_version') as unmapped_code_unknown,
  count(*) filter (where r.unmapped_reason = 'before_first_version_and_no_same_name_version')
                                                                              as unmapped_before_first_version,
  count(*) filter (where r.unmapped_reason = 'zone_not_in_effective_version_and_no_same_name_version')
                                                                              as unmapped_not_in_effective_version,
  count(*) filter (where r.unmapped_reason = 'gap_version_name_mismatch_and_no_same_name_version')
                                                                              as unmapped_gap_name_mismatch,
  count(*) filter (where ev.is_marine_area or ev.cz_type = 'M')               as marine_events_excluded
from ev
left join staging.storm_event_zone_resolution r using (event_id)
left join tiers t using (event_id)
group by ev.event_year
order by ev.event_year;

alter table serving.zone_mapping_coverage add primary key (event_year);

drop table if exists serving.inflation_reference cascade;
create table serving.inflation_reference as
select period as year, cpi, cpi_basis, base_year, base_cpi, factor_to_base
from curated.dim_inflation where period_type = 'calendar_year'
order by period;
