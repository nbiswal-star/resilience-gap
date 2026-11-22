-- Q3 alignment with storm-damage burden. Grain: analysis universe x damage-burden quartile /
-- primary key (analysis_universe, damage_burden_quartile).
--
-- damage_burden_quartile: quartile of constant-dollar NOAA weather damage per resident over all
-- profile years, from serving.county_resilience_profile.damage_pctile (percent rank WITHIN the
-- analysis universe; fixed cut points 0.25 / 0.50 / 0.75, so quartile 4 is exactly is_high_loss).
-- Only units with a population (states + DC and Puerto Rico) AND comparable storm history
-- (storm_declaration_geography_comparable) have a damage rank; Puerto Rico is ranked against
-- Puerto Rico only and is never pooled with states + DC.
--
-- The current Connecticut planning regions have NO damage rank: NOAA records their 2000-2025
-- history on the legacy counties, so their damage is NULL (not comparable), not zero, and they are
-- in no quartile, numerator or denominator of this table (they are counted in
-- serving.svi_quartile_summary.counties_history_not_comparable). Before this rule their false
-- zero damage placed them in D1 and shifted every other unit's rank.
-- HMA stage columns use HMA-comparable units only; counties_excluded_geography_not_comparable
-- counts ranked units whose HMA geography is not comparable (0 today: the only such units, the CT
-- planning regions, also lack comparable storm history and so have no rank). Many units have no
-- recorded damage; tied at the bottom, they all fall in quartile 1.
--   counties                         ranked units of the quartile comparable for HMA
--   counties_not_observed / _observed_no_positive_obligation / _obligated   stage counts (sum =
--                                    counties; critical check hma_damage_burden_reconciles)
--   share_counties_observed / _obligated   stage shares of counties
--   obligated_real_usd, obligated_real_per_capita, obligated_per_damage_dollar   recorded HMA
--                                    obligation (county shares of positive amounts, constant
--                                    dollars) in total, per resident and per dollar of damage
--   median_obligated_real_per_capita county median of recorded obligation per resident
--   counties_svi_q4                  of which national SVI quartile 4 (states + DC only)
--   counties_high_need_low_obligation follow-up screen counties (quartile 4 only, states + DC)
-- Descriptive alignment only: a low obligation in a high-damage quartile is not evidence of
-- non-application or neglect (see the central limitation).
drop table if exists serving.hma_damage_burden_summary cascade;
create table serving.hma_damage_burden_summary as
with c as (
  select *,
         case when damage_pctile < 0.25 then 1 when damage_pctile < 0.50 then 2
              when damage_pctile < 0.75 then 3 else 4 end              as damage_burden_quartile
  from serving.county_resilience_profile
  where storm_declaration_geography_comparable and damage_pctile is not null
)
select
  analysis_universe,
  damage_burden_quartile,
  case damage_burden_quartile
    when 1 then 'D1 (lowest damage per resident)' when 2 then 'D2' when 3 then 'D3'
    else 'D4 (highest damage per resident)' end                         as damage_burden_label,
  count(*) filter (where hma_geography_comparable)                     as counties,
  count(*) filter (where not hma_geography_comparable)                 as counties_excluded_geography_not_comparable,
  sum(population_base) filter (where hma_geography_comparable)         as population,
  sum(damage_total_real_usd) filter (where hma_geography_comparable)   as damage_total_real_usd,
  min(damage_per_capita_real) filter (where hma_geography_comparable)  as min_damage_per_capita_real,
  max(damage_per_capita_real) filter (where hma_geography_comparable)  as max_damage_per_capita_real,
  count(*) filter (where hma_activity_stage = 'not_observed')         as counties_not_observed,
  count(*) filter (where hma_activity_stage = 'observed_no_positive_obligation')
                                                                       as counties_observed_no_positive_obligation,
  count(*) filter (where hma_activity_stage = 'obligated')            as counties_obligated,
  (count(*) filter (where hma_activity_stage in ('observed_no_positive_obligation', 'obligated')))::numeric
    / nullif(count(*) filter (where hma_geography_comparable), 0)     as share_counties_observed,
  (count(*) filter (where hma_activity_stage = 'obligated'))::numeric
    / nullif(count(*) filter (where hma_geography_comparable), 0)     as share_counties_obligated,
  coalesce(sum(hma_obligated_real_usd) filter (where hma_geography_comparable), 0)
                                                                       as obligated_real_usd,
  sum(hma_obligated_real_usd) filter (where hma_geography_comparable)
    / nullif(sum(population_base) filter (where hma_geography_comparable), 0)
                                                                       as obligated_real_per_capita,
  sum(hma_obligated_real_usd) filter (where hma_geography_comparable)
    / nullif(sum(damage_total_real_usd) filter (where hma_geography_comparable), 0)
                                                                       as obligated_per_damage_dollar,
  percentile_cont(0.5) within group (order by hma_per_capita_real)
    filter (where hma_geography_comparable)                            as median_obligated_real_per_capita,
  count(*) filter (where hma_geography_comparable and svi_quartile = 4) as counties_svi_q4,
  count(*) filter (where is_high_need_low_obligation)                  as counties_high_need_low_obligation
from c
group by analysis_universe, damage_burden_quartile;

alter table serving.hma_damage_burden_summary
  add primary key (analysis_universe, damage_burden_quartile);
comment on table serving.hma_damage_burden_summary is
  'Analysis universe x quartile of constant-dollar storm damage per resident (ranked within the universe, units with population and comparable storm history only): HMA stage counts and recorded obligation of HMA-comparable units. The CT planning regions have no comparable storm history, hence no damage rank and no row here; counties_excluded_geography_not_comparable counts ranked units whose HMA geography is not comparable. Descriptive, not causal.';
