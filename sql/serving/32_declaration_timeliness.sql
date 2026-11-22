-- Q4. Grain: one weather-related Major Disaster declaration (disaster_number) in profile scope.
-- Descriptive only: lags come from FEMA dates; SVI, hazard family and FEMA region are attributes
-- used to group lags, not controls in a model.
--
-- svi_group: the group used for the SVI breakdown (one per disaster)
--   'Q1 (least vulnerable)'..'Q4 (most vulnerable)'  population-weighted national SVI of the
--                         designated counties (states + DC only), fixed national cut points
--   'Puerto Rico (SVI ranked within PR)', 'Island areas (no SVI published)'
--                       designated counties lie in that analysis universe
--   'No county designation'  only statewide and/or tribal-area designations: no county SVI
--   'No SVI'            designated counties without a usable SVI (e.g. legacy CT counties)
drop table if exists serving.declaration_timeliness cascade;
create table serving.declaration_timeliness as
with d as (
  select disaster_number,
         min(fema_declaration_string)          as fema_declaration_string,
         min(state_abbr)                       as state_abbr,
         min(fema_region)                      as fema_region,
         min(incident_type)                    as incident_type,
         min(hazard_family)                    as hazard_family,
         min(declaration_title)                as declaration_title,
         min(incident_begin_date)              as incident_begin_date,
         max(incident_end_date)                as incident_end_date,
         min(declaration_request_date)         as declaration_request_date,
         min(declaration_date)                 as declaration_date,
         bool_or(tribal_request)               as tribal_request,
         bool_or(area_level = 'statewide')     as has_statewide_designation,
         count(*) filter (where area_level = 'tribal')     as tribal_areas_designated,
         count(distinct county_fips)           as designated_counties
  from curated.fact_declaration_area
  where declaration_type = 'DR' and is_weather
  group by disaster_number
),
county_svi as (
  select f.disaster_number,
         sum(c.svi_overall * c.population_base) filter (where c.svi_overall is not null)
           / nullif(sum(c.population_base) filter (where c.svi_overall is not null), 0)
                                                                as pop_weighted_svi,
         max(c.svi_overall)                                     as max_county_svi,
         sum(c.population_base)                                 as designated_population,
         min(c.analysis_universe)                               as analysis_universe,
         count(distinct c.analysis_universe)                    as universes
  from (select distinct disaster_number, county_fips from curated.fact_declaration_area
        where declaration_type = 'DR' and is_weather and county_fips is not null) f
  join curated.dim_county c using (county_fips)
  group by f.disaster_number
),
noaa as (  -- NOAA-recorded damage in the designated counties during the incident window,
           -- counting only events whose hazard family the declaration can cover
  select f.disaster_number,
         sum(s.damage_total_usd)      as noaa_damage_in_window_usd,
         sum(s.damage_total_real_usd) as noaa_damage_in_window_real_usd,
         count(distinct s.event_id)   as noaa_events_in_window
  from (select distinct disaster_number, county_fips, hazard_family, incident_begin_date,
               coalesce(incident_end_date, incident_begin_date) as incident_end_date
        from curated.fact_declaration_area
        where declaration_type = 'DR' and is_weather and county_fips is not null) f
  join curated.dim_hazard_match m on m.declaration_hazard_family = f.hazard_family
  join curated.fact_storm_event_county s
    on s.county_fips = f.county_fips
   and s.hazard_family = m.event_hazard_family
   and s.event_date between f.incident_begin_date - 1 and f.incident_end_date + 1
  group by f.disaster_number
)
select d.*,
  d.declaration_date - d.incident_begin_date        as days_incident_to_declaration,
  d.declaration_date - d.declaration_request_date   as days_request_to_declaration,
  d.declaration_request_date - d.incident_begin_date as days_incident_to_request,
  extract(year from d.incident_begin_date)::int     as incident_year,
  s.pop_weighted_svi, s.max_county_svi, s.designated_population,
  case when s.universes = 1 then s.analysis_universe
       when s.universes > 1 then 'mixed' end        as analysis_universe,
  case when s.pop_weighted_svi is null or s.universes > 1
         or s.analysis_universe <> 'states_dc' then null
       when s.pop_weighted_svi < 0.25 then 1 when s.pop_weighted_svi < 0.5 then 2
       when s.pop_weighted_svi < 0.75 then 3 else 4 end as svi_quartile,
  case
    when d.designated_counties = 0                        then 'No county designation'
    when s.universes = 1 and s.analysis_universe = 'states_dc' and s.pop_weighted_svi is not null
      then case when s.pop_weighted_svi < 0.25 then 'Q1 (least vulnerable)'
                when s.pop_weighted_svi < 0.50 then 'Q2'
                when s.pop_weighted_svi < 0.75 then 'Q3'
                else 'Q4 (most vulnerable)' end
    when s.universes = 1 and s.analysis_universe = 'puerto_rico'
      then 'Puerto Rico (SVI ranked within PR)'
    when s.universes = 1 and s.analysis_universe = 'island_areas'
      then 'Island areas (no SVI published)'
    else 'No SVI'
  end                                               as svi_group,
  coalesce(n.noaa_damage_in_window_usd, 0)          as noaa_damage_in_window_usd,
  coalesce(n.noaa_damage_in_window_real_usd, 0)     as noaa_damage_in_window_real_usd,
  coalesce(n.noaa_events_in_window, 0)              as noaa_events_in_window
from d
left join county_svi s using (disaster_number)
left join noaa n using (disaster_number);

alter table serving.declaration_timeliness add primary key (disaster_number);
