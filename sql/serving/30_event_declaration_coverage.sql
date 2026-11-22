-- Q2. Grain: one significant weather event-county pair (event_id, county_fips). A zone event that
-- touches three counties is three event-county pairs; every count and rate built on this table
-- is a count or rate of event-county pairs, not of distinct events.
-- "Significant": county-allocated damage in constant base-year dollars >= the configured
--   threshold (analysis.significant_damage_real_usd), OR the event caused casualties.
-- "Covered": a weather-related Major Disaster (DR) declaration
--   * designates that county, or is a literal "Statewide" designation for its state
--     (tribal-area designations never cover a county: they are not county designations), and
--   * has a hazard family compatible with the event's (curated.dim_hazard_match), and
--   * has an incident window containing the event date (+/- tolerance; open-ended windows capped).
drop table if exists serving.event_declaration_coverage cascade;
create table serving.event_declaration_coverage as
with sig as (
  select f.*, c.county_name, c.svi_overall, c.svi_quartile, c.svi_quartile_label,
         c.analysis_universe, c.fema_region
  from curated.fact_storm_event_county f
  join curated.dim_county c using (county_fips)
  cross join ops.active_profile p
  where f.is_weather
    and (f.damage_total_real_usd >= p.significant_damage_real_usd
         or f.event_casualties >= p.significant_casualties)
),
candidates as not materialized (  -- county or literal-statewide DR/EM, compatible hazard
  select d.*, m.event_hazard_family, m.rule as hazard_match_rule
  from curated.fact_declaration_area d
  join curated.dim_hazard_match m on m.declaration_hazard_family = d.hazard_family
  where d.is_weather and d.area_level in ('county', 'statewide')
)
select
  s.event_id, s.county_fips, s.county_name, s.state_abbr, s.fema_region, s.event_date,
  s.event_year, s.event_type, s.hazard_family, s.damage_total_usd, s.damage_total_real_usd,
  s.deaths, s.injuries,
  s.analysis_universe, s.svi_overall, s.svi_quartile, s.svi_quartile_label,
  m.disaster_number                            as matched_disaster_number,
  m.fema_declaration_string                    as matched_declaration,
  m.incident_type                              as matched_incident_type,
  m.hazard_family                              as matched_hazard_family,
  m.hazard_match_rule                          as matched_hazard_rule,
  m.area_level                                 as matched_area_level,
  m.declaration_date                           as matched_declaration_date,
  m.disaster_number is not null                as covered_by_major_disaster,
  m.declaration_date - s.event_date            as days_event_to_declaration,
  exists (
    select 1 from candidates d
    where d.declaration_type = 'EM' and d.event_hazard_family = s.hazard_family
      and (d.county_fips = s.county_fips or (d.area_level = 'statewide' and d.state_abbr = s.state_abbr))
      and s.event_date between d.incident_begin_date - p.match_days_before
          and coalesce(d.incident_end_date, d.incident_begin_date + p.open_incident_cap_days)
              + p.match_days_after
  )                                            as covered_by_emergency
from sig s
cross join ops.active_profile p
left join lateral (
  select d.*
  from candidates d
  where d.declaration_type = 'DR' and d.event_hazard_family = s.hazard_family
    and (d.county_fips = s.county_fips or (d.area_level = 'statewide' and d.state_abbr = s.state_abbr))
    and s.event_date between d.incident_begin_date - p.match_days_before
        and coalesce(d.incident_end_date, d.incident_begin_date + p.open_incident_cap_days)
            + p.match_days_after
  order by (d.area_level = 'county') desc, (d.hazard_match_rule = 'same_family') desc,
           d.declaration_date, d.disaster_number
  limit 1
) m on true;

alter table serving.event_declaration_coverage add primary key (event_id, county_fips);
