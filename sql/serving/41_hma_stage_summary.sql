-- Q3 stage distribution ("funnel"). Grain: analysis universe x SVI group x period / primary key
-- (analysis_universe, svi_quartile_label, period_label). period_scope = 'all_periods' (one row set
-- over every profile fiscal year, from serving.county_resilience_profile) or 'five_year_period'
-- (from serving.hma_county_period_activity). National Q1-Q4 rows contain states + DC only; Puerto
-- Rico, island areas and legacy geography are their own rows, never pooled into a quartile.
--
-- Unit: counties (county-equivalents). Each county is in exactly one stage per period, so
--   counties = counties_geography_not_comparable + counties_not_observed
--            + counties_observed_no_positive_obligation + counties_obligated
-- (critical check hma_stage_summary_reconciles). counties_geography_not_comparable are the
-- current Connecticut planning regions: HMA names the 8 legacy counties (their own
-- legacy_geography rows), so HMA is not attributed to the regions. They are excluded from every
-- share and per-capita rate:
--   counties_hma_comparable          counties - counties_geography_not_comparable
--   share_counties_observed          counties with >= 1 county-mapped subapplication / comparable
--   share_counties_obligated         counties with a recorded positive obligation / comparable
--   share_observed_counties_obligated  obligated / observed counties
--   obligated_real_per_capita        recorded obligation / population of comparable counties
-- county_subapplication_links counts a subapplication once per county it names;
-- distinct_*_subapplications count each subapplication once per row (a subapplication naming
-- counties in two SVI groups counts in both rows, so these do not add across rows).
-- storm_damage_real_usd: counties of the row with comparable storm history
-- (storm_declaration_geography_comparable; the CT planning regions, whose NOAA history is on the
-- legacy counties, contribute nothing, not zero); counties_storm_history_comparable counts them.
-- hma_comparable_storm_damage_real_usd: counties comparable for both HMA and storm history.
-- All-period rows = all profile years; period rows = calendar years numbered like the period's
-- fiscal years (context only).
-- Not observed is not "did not apply"; obligation is recorded federal obligation, not money
-- received by a county government.
drop table if exists serving.hma_stage_summary cascade;
create table serving.hma_stage_summary as
with county_rows as (
  select analysis_universe, svi_quartile_label, svi_quartile,
         'all_periods'::text                                          as period_scope,
         null::int                                                    as period_start_fy,
         'All profile fiscal years'::text                             as period_label,
         (select sum(fiscal_years_in_period) from curated.dim_hma_period)::int
                                                                      as fiscal_years_in_period,
         county_fips, population_base, storm_declaration_geography_comparable,
         hma_geography_comparable, hma_activity_stage,
         hma_observed_subapplications                                 as observed_subapplications,
         hma_obligated_usd                                            as obligated_usd,
         hma_obligated_real_usd                                       as obligated_real_usd,
         hma_weather_obligated_real_usd                               as weather_obligated_real_usd,
         damage_total_real_usd                                        as storm_damage_real_usd
  from serving.county_resilience_profile
  union all
  select analysis_universe, svi_quartile_label, svi_quartile,
         'five_year_period', period_start_fy, period_label, fiscal_years_in_period,
         county_fips, population_base, storm_declaration_geography_comparable,
         hma_geography_comparable, hma_activity_stage,
         observed_subapplications, obligated_usd, obligated_real_usd, weather_obligated_real_usd,
         storm_damage_real_usd_same_years
  from serving.hma_county_period_activity
),
distinct_subs as (  -- each subapplication once per group
  select c.analysis_universe, c.svi_quartile_label, 'All profile fiscal years'::text as period_label,
         count(distinct f.hma_id)                                         as subapplications,
         count(distinct f.hma_id) filter (where f.has_positive_obligation) as positive_subapplications
  from curated.fact_hma_allocation f
  join serving.county_resilience_profile c using (county_fips)
  group by 1, 2
  union all
  select c.analysis_universe, c.svi_quartile_label, p.period_label,
         count(distinct f.hma_id),
         count(distinct f.hma_id) filter (where f.has_positive_obligation)
  from curated.fact_hma_allocation f
  join serving.county_resilience_profile c using (county_fips)
  join curated.dim_hma_period p using (period_start_fy)
  group by 1, 2, 3
)
select
  r.analysis_universe, r.svi_quartile_label, r.svi_quartile,
  r.period_scope, r.period_start_fy, r.period_label, r.fiscal_years_in_period,
  count(*)                                                                 as counties,
  count(*) filter (where r.hma_geography_comparable)                      as counties_hma_comparable,
  sum(r.population_base)                                                   as population,
  sum(r.population_base) filter (where r.hma_geography_comparable)        as hma_comparable_population,
  count(*) filter (where r.hma_activity_stage = 'geography_not_comparable')
                                                                           as counties_geography_not_comparable,
  count(*) filter (where r.hma_activity_stage = 'not_observed')           as counties_not_observed,
  count(*) filter (where r.hma_activity_stage = 'observed_no_positive_obligation')
                                                                           as counties_observed_no_positive_obligation,
  count(*) filter (where r.hma_activity_stage = 'obligated')              as counties_obligated,
  (count(*) filter (where r.hma_activity_stage in ('observed_no_positive_obligation', 'obligated')))::numeric
    / nullif(count(*) filter (where r.hma_geography_comparable), 0)       as share_counties_observed,
  (count(*) filter (where r.hma_activity_stage = 'obligated'))::numeric
    / nullif(count(*) filter (where r.hma_geography_comparable), 0)       as share_counties_obligated,
  (count(*) filter (where r.hma_activity_stage = 'obligated'))::numeric
    / nullif(count(*) filter (where r.hma_activity_stage in ('observed_no_positive_obligation',
                                                              'obligated')), 0)
                                                                           as share_observed_counties_obligated,
  sum(r.observed_subapplications)                                          as county_subapplication_links,
  coalesce(d.subapplications, 0)                                           as distinct_observed_subapplications,
  coalesce(d.positive_subapplications, 0)                                  as distinct_positive_obligation_subapplications,
  sum(r.obligated_usd)                                                     as obligated_usd,
  sum(r.obligated_real_usd)                                                as obligated_real_usd,
  sum(r.weather_obligated_real_usd)                                        as weather_obligated_real_usd,
  sum(r.obligated_real_usd) filter (where r.hma_geography_comparable)
    / nullif(sum(r.population_base) filter (where r.hma_geography_comparable), 0)
                                                                           as obligated_real_per_capita,
  count(*) filter (where r.storm_declaration_geography_comparable)        as counties_storm_history_comparable,
  sum(r.storm_damage_real_usd) filter (where r.storm_declaration_geography_comparable)
                                                                           as storm_damage_real_usd,
  sum(r.storm_damage_real_usd) filter (where r.hma_geography_comparable
                                         and r.storm_declaration_geography_comparable)
                                                                           as hma_comparable_storm_damage_real_usd
from county_rows r
left join distinct_subs d
  on d.analysis_universe = r.analysis_universe
 and d.svi_quartile_label = r.svi_quartile_label
 and d.period_label = r.period_label
group by r.analysis_universe, r.svi_quartile_label, r.svi_quartile, r.period_scope,
         r.period_start_fy, r.period_label, r.fiscal_years_in_period,
         d.subapplications, d.positive_subapplications;

alter table serving.hma_stage_summary
  add primary key (analysis_universe, svi_quartile_label, period_label);
comment on table serving.hma_stage_summary is
  'Counties by HMA activity stage (geography_not_comparable / not_observed / observed_no_positive_obligation / obligated) per analysis universe, SVI group and period (all profile fiscal years or a five-year period). Stages describe county-mapped subapplications that reached FEMA; shares and per-capita rates exclude geography_not_comparable (CT planning regions).';
