-- Grain: one storm event x one affected county, within the active profile (years, states).
-- Dollar and casualty measures are allocated by allocation_weight so county sums reconcile
-- with event totals.
drop table if exists curated.fact_storm_event_county cascade;
create table curated.fact_storm_event_county as
select
  e.event_id,
  e.episode_id,
  b.county_fips,
  d.state_abbr,
  e.begin_local::date                                          as event_date,
  extract(year from e.begin_local)::int                        as event_year,
  e.event_type,
  coalesce(h.hazard_family, 'Unmapped')                        as hazard_family,
  coalesce(h.is_weather, true)                                 as is_weather,
  b.match_method,
  b.allocation_weight,
  (e.damage_property_usd is null and e.damage_crops_usd is null) as damage_not_reported,
  coalesce(e.damage_property_usd, 0) * b.allocation_weight     as damage_property_usd,
  coalesce(e.damage_crops_usd, 0) * b.allocation_weight        as damage_crops_usd,
  (coalesce(e.damage_property_usd, 0) + coalesce(e.damage_crops_usd, 0))
     * b.allocation_weight                                     as damage_total_usd,
  (e.deaths_direct + e.deaths_indirect) * b.allocation_weight  as deaths,
  (e.injuries_direct + e.injuries_indirect) * b.allocation_weight as injuries,
  -- constant base-year dollars (CPI-U annual average of the event year); NULL if CPI missing
  (coalesce(e.damage_property_usd, 0) + coalesce(e.damage_crops_usd, 0))
     * b.allocation_weight * infl.factor_to_base                as damage_total_real_usd,
  infl.factor_to_base                                          as cpi_factor_to_base,
  b.crosswalk_tier,
  b.snapshot_file                                              as crosswalk_version,
  -- unallocated event-level totals, used for the "significant event" rule
  coalesce(e.damage_property_usd, 0) + coalesce(e.damage_crops_usd, 0) as event_damage_total_usd,
  e.deaths_direct + e.deaths_indirect + e.injuries_direct + e.injuries_indirect as event_casualties
from staging.storm_events e
join staging.storm_event_county b using (event_id)
join curated.dim_county d on d.county_fips = b.county_fips
left join curated.dim_hazard h on h.source = 'noaa' and h.source_value = e.event_type
left join curated.dim_inflation infl
  on infl.period_type = 'calendar_year' and infl.period = extract(year from e.begin_local)
cross join ops.active_profile p
where e.begin_local is not null
  and extract(year from e.begin_local) between p.start_year and p.end_year
  and d.in_profile_scope;

alter table curated.fact_storm_event_county add primary key (event_id, county_fips);
alter table curated.fact_storm_event_county
  add constraint fk_storm_county foreign key (county_fips) references curated.dim_county;
create index on curated.fact_storm_event_county (county_fips, event_date);
