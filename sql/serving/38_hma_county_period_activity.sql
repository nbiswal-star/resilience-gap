-- Q3 by period. Grain: one county of serving.county_resilience_profile x one five-year fiscal-year
-- period of curated.dim_hma_period / primary key (county_fips, period_start_fy).
--
-- Dense on purpose: every county in the profile appears in every period, so not_observed rows are
-- explicit. "not_observed" means only that no subapplication naming this county reached FEMA and
-- was published with a fiscal year in this period. It does not mean the county never applied:
-- proposals screened out locally or by the state, statewide or unparsed benefiting areas, and
-- subapplications in other fiscal years are not visible at this grain.
-- The 9 current Connecticut planning regions (hma_geography_comparable = false) stay in the grid
-- in EVERY period with stage 'geography_not_comparable': HMA names the 8 legacy counties, which
-- keep their own rows (analysis_universe = legacy_geography). HMA is never mapped across the break.
--
-- Stage, counts and amounts use the same definitions as serving.county_resilience_profile
-- (county-mapped shares only; recorded obligation = shares of positive totalObligatedAmount).
-- A subapplication has one fiscal year, so per-period counts and amounts add up exactly to the
-- all-period profile columns (critical check hma_period_reconciles_profile).
-- storm_damage_*_same_years: NOAA weather damage of the county in the CALENDAR years with the
-- same numbers as the period's fiscal years (context only; fiscal and calendar years are offset
-- by three months). NULL (not comparable, never zero) where the profile's
-- storm_declaration_geography_comparable is false: NOAA records the CT planning regions' history
-- on the legacy counties.
drop table if exists serving.hma_county_period_activity cascade;
create table serving.hma_county_period_activity as
with obs as (
  select county_fips, period_start_fy,
         count(distinct hma_id)                                              as observed_subapplications,
         count(distinct hma_id) filter (where has_positive_obligation)       as positive_obligation_subapplications,
         count(distinct program)                                             as programs_observed,
         sum(obligated_usd) filter (where has_positive_obligation)           as obligated_usd,
         sum(obligated_real_usd) filter (where has_positive_obligation)      as obligated_real_usd,
         sum(obligated_usd) filter (where has_positive_obligation and is_weather)
                                                                             as weather_obligated_usd,
         sum(obligated_real_usd) filter (where has_positive_obligation and is_weather)
                                                                             as weather_obligated_real_usd
  from curated.fact_hma_allocation
  where county_fips is not null
  group by county_fips, period_start_fy
),
status as (
  select county_fips, period_start_fy, jsonb_object_agg(status_value, subapplications) as status_mix
  from (select county_fips, period_start_fy, coalesce(status, '(status not recorded)') as status_value,
               count(distinct hma_id) as subapplications
        from curated.fact_hma_allocation
        where county_fips is not null
        group by 1, 2, 3) s
  group by county_fips, period_start_fy
),
storms as (
  select f.county_fips, p.period_start_fy,
         sum(f.damage_total_usd)      as storm_damage_usd_same_years,
         sum(f.damage_total_real_usd) as storm_damage_real_usd_same_years
  from curated.fact_storm_event_county f
  join curated.dim_hma_period p
    on f.event_year between p.first_fy_in_profile and p.last_fy_in_profile
  where f.is_weather
  group by f.county_fips, p.period_start_fy
)
select
  c.county_fips, c.county_name, c.state_abbr, c.fema_region, c.analysis_universe,
  c.svi_quartile, c.svi_quartile_label, c.population_base,
  c.storm_declaration_geography_comparable, c.hma_geography_comparable,
  p.period_start_fy, p.period_end_fy, p.first_fy_in_profile, p.last_fy_in_profile,
  p.period_label, p.fiscal_years_in_period, p.is_partial_period,
  case when not c.hma_geography_comparable then 'geography_not_comparable'
       when coalesce(o.observed_subapplications, 0) = 0 then 'not_observed'
       when coalesce(o.positive_obligation_subapplications, 0) = 0
         then 'observed_no_positive_obligation'
       else 'obligated' end                                         as hma_activity_stage,
  coalesce(o.observed_subapplications, 0)                           as observed_subapplications,
  coalesce(o.positive_obligation_subapplications, 0)                as positive_obligation_subapplications,
  coalesce(o.observed_subapplications, 0) - coalesce(o.positive_obligation_subapplications, 0)
                                                                    as no_positive_obligation_subapplications,
  coalesce(o.programs_observed, 0)                                  as programs_observed,
  coalesce(o.obligated_usd, 0)                                      as obligated_usd,
  coalesce(o.obligated_real_usd, 0)                                 as obligated_real_usd,
  coalesce(o.weather_obligated_usd, 0)                              as weather_obligated_usd,
  coalesce(o.weather_obligated_real_usd, 0)                         as weather_obligated_real_usd,
  case when c.hma_geography_comparable
       then coalesce(o.obligated_real_usd, 0) / nullif(c.population_base, 0) end
                                                                    as obligated_real_per_capita,
  coalesce(s.status_mix, '{}'::jsonb)                               as status_mix,
  case when c.storm_declaration_geography_comparable
       then coalesce(st.storm_damage_usd_same_years, 0) end         as storm_damage_usd_same_years,
  case when c.storm_declaration_geography_comparable
       then coalesce(st.storm_damage_real_usd_same_years, 0) end    as storm_damage_real_usd_same_years
from serving.county_resilience_profile c
cross join curated.dim_hma_period p
left join obs o on o.county_fips = c.county_fips and o.period_start_fy = p.period_start_fy
left join status s on s.county_fips = c.county_fips and s.period_start_fy = p.period_start_fy
left join storms st on st.county_fips = c.county_fips and st.period_start_fy = p.period_start_fy;

alter table serving.hma_county_period_activity add primary key (county_fips, period_start_fy);
alter table serving.hma_county_period_activity
  add constraint hma_period_stage_valid check (
    hma_activity_stage in ('geography_not_comparable', 'not_observed',
                           'observed_no_positive_obligation', 'obligated')
    and (hma_activity_stage = 'geography_not_comparable') = (not hma_geography_comparable));
create index on serving.hma_county_period_activity (period_start_fy, hma_activity_stage);
comment on table serving.hma_county_period_activity is
  'County x five-year fiscal-year period (key county_fips, period_start_fy); dense. Stage of county-mapped HMA subapplications that reached FEMA. not_observed = no county-mapped subapplication observed in the period, not proof that none was attempted. geography_not_comparable = CT planning region in every period (HMA uses the legacy counties).';
comment on column serving.hma_county_period_activity.status_mix is
  'Distinct county-mapped subapplications in the period by OpenFEMA status, verbatim.';
comment on column serving.hma_county_period_activity.storm_damage_real_usd_same_years is
  'NOAA weather damage, constant base-year dollars, calendar years numbered like the period fiscal years (context only). NULL for the CT planning regions (history on the legacy counties).';
