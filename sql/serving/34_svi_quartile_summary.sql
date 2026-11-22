-- Headline comparison by analysis universe and SVI group. Grain: analysis universe x SVI group /
-- unique (analysis_universe, svi_quartile_label). National quartiles (Q1-Q4) contain only
-- states + DC; Puerto Rico (ranked within PR), island areas and legacy geography are separate
-- rows and are never pooled into a national quartile.
--
-- counties / population: EVERY unit of the row, incl. the 9 current Connecticut planning regions
-- (current social geography: SVI and population are current).
-- Q1 and Q2 columns (damage, declaration coverage) use only units with comparable storm and
-- declaration history (curated.dim_county.storm_declaration_geography_comparable). The CT
-- planning regions are excluded from numerators AND denominators: NOAA and OpenFEMA declarations
-- record their 2000-2025 history on the legacy counties, so including them would divide by
-- population with a false-zero history.
--   counties_history_not_comparable  current CT planning regions (shown for transparency)
--   counties_history_comparable      counties - counties_history_not_comparable
--   history_comparable_population    population of comparable units: the ONLY denominator of
--                                    damage_per_capita / damage_per_capita_real
--   damage_* / significant_event_counties / covered_event_counties / coverage_rate
--                                    comparable units only
-- Q2 measures declaration coverage (designation of the county for a declared incident), not
-- money.
--
-- Q3 columns separate the stages of the HMA record, over all profile fiscal years, and use only
-- HMA-comparable units (curated.dim_county.hma_geography_comparable):
--   counties_hma_geography_not_comparable      current CT planning regions: HMA names the legacy
--                                              counties, so HMA is not attributed to them. Shown
--                                              for transparency, excluded from every Q3 rate
--   counties_hma_comparable                    counties - counties_hma_geography_not_comparable
--                                              (the denominator of the Q3 shares)
--   counties_hma_not_observed                  no county-mapped subapplication observed (not
--                                              evidence that none was attempted)
--   counties_hma_observed_no_positive_obligation  observed, no recorded positive obligation
--   counties_hma_obligated                     >= 1 recorded positive obligation
--   hma_comparable_population                  population of HMA-comparable units (denominator of
--                                              the HMA per-capita rates)
--   hma_comparable_damage_real_usd             damage of units comparable for BOTH HMA and storm
--                                              history (denominator of hma_per_damage_dollar,
--                                              whose numerator uses the same units)
--   hma_observed_subapplications /
--   hma_positive_obligation_subapplications    distinct subapplications naming a county of the row
--   hma_obligated_* / hma_weather_obligated_*  recorded obligation (county shares of positive
--                                              totalObligatedAmount), nominal and constant dollars
--   high_need_low_obligation_counties          follow-up screen count (states + DC only)
drop table if exists serving.svi_quartile_summary cascade;
create table serving.svi_quartile_summary as
with subs as (  -- distinct subapplications per group (one can name counties in several groups)
  select c.analysis_universe, c.svi_quartile_label,
         count(distinct f.hma_id)                                          as hma_observed_subapplications,
         count(distinct f.hma_id) filter (where f.has_positive_obligation) as hma_positive_obligation_subapplications
  from curated.fact_hma_allocation f
  join serving.county_resilience_profile c using (county_fips)
  group by 1, 2
),
grp as (
  select
    analysis_universe,
    svi_quartile_label,
    svi_quartile,
    count(*)                                                       as counties,
    sum(population_base)                                           as population,
    -- Q1 / Q2: units with comparable storm and declaration history only (numerators and
    -- denominators); the CT planning regions are counted in counties_history_not_comparable
    count(*) filter (where not storm_declaration_geography_comparable)
                                                                   as counties_history_not_comparable,
    count(*) filter (where storm_declaration_geography_comparable) as counties_history_comparable,
    sum(population_base) filter (where storm_declaration_geography_comparable)
                                                                   as history_comparable_population,
    sum(damage_total_usd) filter (where storm_declaration_geography_comparable)
                                                                   as damage_total_usd,
    sum(damage_total_real_usd) filter (where storm_declaration_geography_comparable)
                                                                   as damage_total_real_usd,
    sum(damage_total_usd) filter (where storm_declaration_geography_comparable)
      / nullif(sum(population_base) filter (where storm_declaration_geography_comparable), 0)
                                                                   as damage_per_capita,
    sum(damage_total_real_usd) filter (where storm_declaration_geography_comparable)
      / nullif(sum(population_base) filter (where storm_declaration_geography_comparable), 0)
                                                                   as damage_per_capita_real,
    -- Q2 unit: significant event-county pairs (an event touching 3 counties counts 3 times)
    sum(significant_events) filter (where storm_declaration_geography_comparable)
                                                                   as significant_event_counties,
    sum(significant_events_covered) filter (where storm_declaration_geography_comparable)
                                                                   as covered_event_counties,
    (sum(significant_events_covered) filter (where storm_declaration_geography_comparable))::numeric
      / nullif(sum(significant_events) filter (where storm_declaration_geography_comparable), 0)
                                                                   as coverage_rate,
    -- Q3 stages (all profile fiscal years), HMA-comparable units only
    count(*) filter (where hma_activity_stage = 'geography_not_comparable')
                                                                   as counties_hma_geography_not_comparable,
    count(*) filter (where hma_geography_comparable)               as counties_hma_comparable,
    count(*) filter (where hma_activity_stage = 'not_observed')   as counties_hma_not_observed,
    count(*) filter (where hma_activity_stage = 'observed_no_positive_obligation')
                                                                   as counties_hma_observed_no_positive_obligation,
    count(*) filter (where hma_activity_stage = 'obligated')      as counties_hma_obligated,
    (count(*) filter (where hma_activity_stage in ('observed_no_positive_obligation', 'obligated')))::numeric
      / nullif(count(*) filter (where hma_geography_comparable), 0)
                                                                   as share_counties_hma_observed,
    (count(*) filter (where hma_activity_stage = 'obligated'))::numeric
      / nullif(count(*) filter (where hma_geography_comparable), 0)
                                                                   as share_counties_hma_obligated,
    sum(population_base) filter (where hma_geography_comparable)  as hma_comparable_population,
    sum(damage_total_real_usd) filter (where hma_geography_comparable
                                         and storm_declaration_geography_comparable)
                                                                   as hma_comparable_damage_real_usd,
    sum(hma_obligated_usd)                                         as hma_obligated_usd,
    sum(hma_obligated_real_usd)                                    as hma_obligated_real_usd,
    sum(hma_weather_obligated_usd)                                 as hma_weather_obligated_usd,
    sum(hma_weather_obligated_real_usd)                            as hma_weather_obligated_real_usd,
    sum(hma_obligated_real_usd) filter (where hma_geography_comparable)
      / nullif(sum(population_base) filter (where hma_geography_comparable), 0)
                                                                   as hma_per_capita_real,
    sum(hma_obligated_usd) filter (where hma_geography_comparable)
      / nullif(sum(population_base) filter (where hma_geography_comparable), 0)
                                                                   as hma_per_capita,
    sum(hma_weather_obligated_real_usd) filter (where hma_geography_comparable)
      / nullif(sum(population_base) filter (where hma_geography_comparable), 0)
                                                                   as hma_weather_per_capita_real,
    sum(hma_obligated_real_usd) filter (where hma_geography_comparable
                                          and storm_declaration_geography_comparable)
      / nullif(sum(damage_total_real_usd) filter (where hma_geography_comparable
                                                    and storm_declaration_geography_comparable), 0)
                                                                   as hma_per_damage_dollar,
    count(*) filter (where is_high_need_low_obligation)            as high_need_low_obligation_counties
  from serving.county_resilience_profile
  group by analysis_universe, svi_quartile_label, svi_quartile
)
select g.analysis_universe, g.svi_quartile_label, g.svi_quartile, g.counties, g.population,
       g.counties_history_not_comparable, g.counties_history_comparable,
       g.history_comparable_population,
       g.damage_total_usd, g.damage_total_real_usd, g.damage_per_capita, g.damage_per_capita_real,
       g.significant_event_counties, g.covered_event_counties, g.coverage_rate,
       g.counties_hma_geography_not_comparable, g.counties_hma_comparable,
       g.counties_hma_not_observed, g.counties_hma_observed_no_positive_obligation,
       g.counties_hma_obligated, g.share_counties_hma_observed, g.share_counties_hma_obligated,
       g.hma_comparable_population, g.hma_comparable_damage_real_usd,
       coalesce(s.hma_observed_subapplications, 0)            as hma_observed_subapplications,
       coalesce(s.hma_positive_obligation_subapplications, 0) as hma_positive_obligation_subapplications,
       g.hma_obligated_usd, g.hma_obligated_real_usd, g.hma_weather_obligated_usd,
       g.hma_weather_obligated_real_usd, g.hma_per_capita_real, g.hma_per_capita,
       g.hma_weather_per_capita_real, g.hma_per_damage_dollar,
       g.high_need_low_obligation_counties
from grp g
left join subs s using (analysis_universe, svi_quartile_label)
order by case g.analysis_universe when 'states_dc' then 1 when 'puerto_rico' then 2
              when 'island_areas' then 3 else 4 end, g.svi_quartile nulls last;

alter table serving.svi_quartile_summary
  add constraint svi_quartile_summary_key unique (analysis_universe, svi_quartile_label);
comment on table serving.svi_quartile_summary is
  'Analysis universe x SVI group. counties/population include every unit; damage, per-capita damage (denominator history_comparable_population) and declaration coverage use only units with comparable storm/declaration history, so the CT planning regions (counties_history_not_comparable) are in no damage or coverage numerator or denominator. Declaration coverage is designation, not money. HMA stages: not_observed / observed_no_positive_obligation / obligated (county-mapped subapplications that reached FEMA) over HMA-comparable units; CT planning regions are counted in counties_hma_geography_not_comparable and excluded from Q3 rates. high_need_low_obligation_counties is a follow-up screen count.';
