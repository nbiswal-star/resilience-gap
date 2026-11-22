-- Q1 + Q3. Grain: one county or county-equivalent in the active profile / primary key county_fips.
-- Denormalised for dashboards and BI tools.
--
-- Historical storm / declaration columns (2000-2025 profile years) and the geography break:
--   storm_declaration_geography_comparable  false for the 9 current Connecticut planning regions
--                                            (curated.dim_county): NOAA Storm Events and OpenFEMA
--                                            declarations record their history on the 8 legacy
--                                            counties. For these units storm_events, damage_*,
--                                            deaths, injuries, significant_events*,
--                                            significant_damage_usd, covered_damage_usd,
--                                            major_disasters, emergencies, damage_per_capita*,
--                                            coverage_rate, damage_pctile and is_high_loss are
--                                            NULL (not comparable), never zero; the units are in
--                                            no damage rank and not in the follow-up screen. SVI
--                                            and population are kept. Legacy facts are never
--                                            crosswalked or allocated to a planning region; the
--                                            legacy counties keep them (real pre-change history).
--   Elsewhere 0 means "no recorded weather event / declaration" for a comparable unit.
--
-- HMA columns cover every fiscal year of the profile ("all periods") and only the county-mapped
-- shares of subapplications (curated.fact_hma_allocation.area_level = 'county'). Statewide,
-- out-of-state, unresolved and unspecified shares stay at state level (serving.state_year_summary).
--   hma_geography_comparable                 false for the 9 current Connecticut planning regions
--                                            (curated.dim_county): HMA names the legacy counties
--   hma_activity_stage                       exactly one of
--       geography_not_comparable             current unit whose HMA geography is not comparable
--                                            (CT planning region). HMA is not attributed across
--                                            the 2022 geography break; the unit is excluded from
--                                            HMA stage denominators, per-capita ranks, medians and
--                                            the follow-up screen. Checked first.
--       not_observed                         no county-mapped subapplication in OpenFEMA for this
--                                            county. NOT evidence that the county never applied:
--                                            proposals screened out locally or by the state, or
--                                            covered only by a statewide or unparsed entry, are
--                                            invisible here
--       observed_no_positive_obligation      >= 1 county-mapped subapplication, none with a
--                                            recorded totalObligatedAmount > 0 (pending, withdrawn,
--                                            unsuccessful, not yet obligated, or a true zero)
--       obligated                            >= 1 county-mapped subapplication with a recorded
--                                            totalObligatedAmount > 0
--   hma_observed_subapplications             distinct county-mapped subapplications
--   hma_positive_obligation_subapplications  ... of which totalObligatedAmount > 0
--   hma_obligated_usd / _real_usd            recorded obligation: sum of the county's equal shares
--                                            of positive totalObligatedAmount (negative
--                                            de-obligation records excluded), nominal and constant
--                                            base-year dollars
--   hma_weather_obligated_usd / _real_usd    same, primary hazard in a weather hazard family
--   hma_status_mix                           {OpenFEMA status, verbatim: distinct subapplications}
--   hma_per_capita*, hma_to_damage_ratio, hma_pctile, hma_per_capita_real_median, is_low_obligation
--                                            NULL where hma_geography_comparable is false or the
--                                            population is unknown. hma_pctile and the median are
--                                            computed WITHIN each analysis universe: Puerto Rico's
--                                            is_low_obligation uses Puerto Rico's own median, not
--                                            the states + DC median. Island areas and legacy
--                                            geography have no population, hence no rank.
-- Follow-up screen (states + DC only, units with comparable storm/declaration history AND
-- comparable HMA geography only; NOT evidence of neglect, non-application or need unmet):
--   is_high_need_low_obligation = top-quartile constant-dollar damage per resident
--     (damage_pctile >= 0.75) AND national SVI quartile 4 AND constant-dollar recorded HMA
--     obligation per resident at or below the states + DC median (hma_pctile <= 0.5, which is
--     exactly hma_per_capita_real <= median; see DQ check high_need_low_obligation_flag_exact).
drop table if exists serving.county_resilience_profile cascade;
create table serving.county_resilience_profile as
with storms as (
  select county_fips,
         count(distinct event_id)                         as storm_events,
         sum(damage_total_usd)                            as damage_total_usd,
         sum(damage_total_real_usd)                       as damage_total_real_usd,
         sum(damage_property_usd)                         as damage_property_usd,
         sum(damage_crops_usd)                            as damage_crops_usd,
         sum(deaths)                                      as deaths,
         sum(injuries)                                    as injuries
  from curated.fact_storm_event_county
  where is_weather
  group by county_fips
),
coverage as (  -- significant event-county pairs of this county (serving.event_declaration_coverage)
  select county_fips,
         count(*)                                         as significant_events,
         count(*) filter (where covered_by_major_disaster) as significant_events_covered,
         sum(damage_total_usd)                            as significant_damage_usd,
         sum(damage_total_usd) filter (where covered_by_major_disaster) as covered_damage_usd
  from serving.event_declaration_coverage
  group by county_fips
),
decl as (
  select county_fips,
         count(distinct disaster_number) filter (where declaration_type = 'DR') as major_disasters,
         count(distinct disaster_number) filter (where declaration_type = 'EM') as emergencies
  from curated.fact_declaration_area
  where county_fips is not null and is_weather
  group by county_fips
),
hma as (
  select county_fips,
         count(distinct hma_id)                                         as hma_observed_subapplications,
         count(distinct hma_id) filter (where has_positive_obligation)  as hma_positive_obligation_subapplications,
         coalesce(sum(obligated_usd) filter (where has_positive_obligation), 0)      as hma_obligated_usd,
         coalesce(sum(obligated_real_usd) filter (where has_positive_obligation), 0) as hma_obligated_real_usd,
         coalesce(sum(obligated_usd) filter (where has_positive_obligation and is_weather), 0)
                                                                        as hma_weather_obligated_usd,
         coalesce(sum(obligated_real_usd) filter (where has_positive_obligation and is_weather), 0)
                                                                        as hma_weather_obligated_real_usd
  from curated.fact_hma_allocation
  where county_fips is not null
  group by county_fips
),
hma_status as (
  select county_fips, jsonb_object_agg(status_value, subapplications) as hma_status_mix
  from (select county_fips, coalesce(status, '(status not recorded)') as status_value,
               count(distinct hma_id) as subapplications
        from curated.fact_hma_allocation
        where county_fips is not null
        group by 1, 2) s
  group by county_fips
),
base as (
  select
    c.county_fips, c.county_name, c.state_abbr, c.state_name, c.fema_region, c.population_base,
    c.analysis_universe, c.svi_coverage,
    c.storm_declaration_geography_comparable, c.storm_declaration_geography_note,
    c.hma_geography_comparable, c.hma_geography_note,
    c.svi_overall, c.svi_quartile, c.svi_quartile_label,
    c.svi_pr_within_rank, c.svi_pr_within_quartile,
    c.svi_socioeconomic, c.svi_household, c.svi_minority_language, c.svi_housing_transport,
    c.pct_below_150_poverty, c.pct_mobile_homes,
    -- historical storm / declaration measures: NULL (not comparable), never 0, for current units
    -- whose state's history is recorded on legacy geography (CT planning regions)
    case when c.storm_declaration_geography_comparable then coalesce(s.storm_events, 0) end
                                                  as storm_events,
    case when c.storm_declaration_geography_comparable then coalesce(s.damage_total_usd, 0) end
                                                  as damage_total_usd,
    case when c.storm_declaration_geography_comparable then coalesce(s.damage_total_real_usd, 0) end
                                                  as damage_total_real_usd,
    case when c.storm_declaration_geography_comparable then coalesce(s.damage_property_usd, 0) end
                                                  as damage_property_usd,
    case when c.storm_declaration_geography_comparable then coalesce(s.damage_crops_usd, 0) end
                                                  as damage_crops_usd,
    case when c.storm_declaration_geography_comparable then coalesce(s.deaths, 0) end
                                                  as deaths,
    case when c.storm_declaration_geography_comparable then coalesce(s.injuries, 0) end
                                                  as injuries,
    case when c.storm_declaration_geography_comparable then coalesce(v.significant_events, 0) end
                                                  as significant_events,
    case when c.storm_declaration_geography_comparable
         then coalesce(v.significant_events_covered, 0) end
                                                  as significant_events_covered,
    case when c.storm_declaration_geography_comparable
         then coalesce(v.significant_damage_usd, 0) end
                                                  as significant_damage_usd,
    case when c.storm_declaration_geography_comparable then coalesce(v.covered_damage_usd, 0) end
                                                  as covered_damage_usd,
    case when c.storm_declaration_geography_comparable then coalesce(d.major_disasters, 0) end
                                                  as major_disasters,
    case when c.storm_declaration_geography_comparable then coalesce(d.emergencies, 0) end
                                                  as emergencies,
    case when not c.hma_geography_comparable then 'geography_not_comparable'
         when coalesce(h.hma_observed_subapplications, 0) = 0 then 'not_observed'
         when coalesce(h.hma_positive_obligation_subapplications, 0) = 0
           then 'observed_no_positive_obligation'
         else 'obligated' end                     as hma_activity_stage,
    coalesce(h.hma_observed_subapplications, 0)   as hma_observed_subapplications,
    coalesce(h.hma_positive_obligation_subapplications, 0)
                                                  as hma_positive_obligation_subapplications,
    coalesce(h.hma_observed_subapplications, 0)
      - coalesce(h.hma_positive_obligation_subapplications, 0)
                                                  as hma_no_positive_obligation_subapplications,
    coalesce(h.hma_obligated_usd, 0)              as hma_obligated_usd,
    coalesce(h.hma_obligated_real_usd, 0)         as hma_obligated_real_usd,
    coalesce(h.hma_weather_obligated_usd, 0)      as hma_weather_obligated_usd,
    coalesce(h.hma_weather_obligated_real_usd, 0) as hma_weather_obligated_real_usd,
    coalesce(hs.hma_status_mix, '{}'::jsonb)      as hma_status_mix
  from curated.dim_county c
  left join storms s using (county_fips)
  left join coverage v using (county_fips)
  left join decl d using (county_fips)
  left join hma h using (county_fips)
  left join hma_status hs using (county_fips)
  where c.in_profile_scope
    and (c.in_popest_2025 or s.county_fips is not null or d.county_fips is not null
         or h.county_fips is not null)
),
rates as (
  select b.*,
    -- damage and coverage rates: NULL where storm/declaration history is not comparable (the
    -- inputs are NULL there; the explicit guard keeps that true if the inputs ever change)
    case when b.storm_declaration_geography_comparable
         then b.damage_total_usd / nullif(b.population_base, 0) end   as damage_per_capita,
    case when b.storm_declaration_geography_comparable
         then b.damage_total_real_usd / nullif(b.population_base, 0) end as damage_per_capita_real,
    -- HMA rates only where HMA geography is comparable (NULL for CT planning regions)
    case when b.hma_geography_comparable
         then b.hma_obligated_usd / nullif(b.population_base, 0) end  as hma_per_capita,
    case when b.hma_geography_comparable
         then b.hma_obligated_real_usd / nullif(b.population_base, 0) end as hma_per_capita_real,
    case when b.hma_geography_comparable
         then b.hma_weather_obligated_real_usd / nullif(b.population_base, 0) end
                                                                      as hma_weather_per_capita_real,
    case when b.storm_declaration_geography_comparable
         then b.significant_events_covered::numeric / nullif(b.significant_events, 0) end
                                                                      as coverage_rate,
    case when b.hma_geography_comparable and b.storm_declaration_geography_comparable
         then b.hma_obligated_real_usd / nullif(b.damage_total_real_usd, 0) end
                                                                      as hma_to_damage_ratio
  from base b
),
medians as (  -- per analysis universe, over HMA-comparable counties with a population
  select analysis_universe,
         percentile_cont(0.5) within group (order by hma_per_capita_real)
                                                                      as hma_per_capita_real_median
  from rates
  where hma_per_capita_real is not null
  group by analysis_universe
),
ranked as (
  select r.*,
    m.hma_per_capita_real_median,
    -- ranks are computed within each analysis universe, on constant dollars; damage ranks use
    -- every unit with a population AND comparable storm history (CT planning regions have a NULL
    -- damage_per_capita_real, so they are outside the partition), HMA ranks only HMA-comparable
    -- units
    case when r.damage_per_capita_real is not null
         then percent_rank() over (partition by r.analysis_universe, r.damage_per_capita_real is not null
                                   order by r.damage_per_capita_real) end as damage_pctile,
    case when r.hma_per_capita_real is not null
         then percent_rank() over (partition by r.analysis_universe, r.hma_per_capita_real is not null
                                   order by r.hma_per_capita_real) end    as hma_pctile
  from rates r
  left join medians m using (analysis_universe)
)
select ranked.*,
  (damage_pctile >= 0.75)                                            as is_high_loss,
  (svi_quartile = 4)                                                 as is_high_vulnerability,
  (hma_pctile <= 0.5)                                                as is_low_obligation,
  -- follow-up screen; defined only where a national SVI exists (analysis_universe = states_dc),
  -- storm/declaration history is comparable (damage_pctile is NULL otherwise) and HMA geography
  -- is comparable (hma_pctile is NULL otherwise)
  coalesce(analysis_universe = 'states_dc' and storm_declaration_geography_comparable
           and hma_geography_comparable
           and damage_pctile >= 0.75 and svi_quartile = 4
           and hma_pctile <= 0.5, false)                             as is_high_need_low_obligation
from ranked;

alter table serving.county_resilience_profile add primary key (county_fips);
alter table serving.county_resilience_profile
  add constraint profile_hma_stage_valid check (
    hma_activity_stage in ('geography_not_comparable', 'not_observed',
                           'observed_no_positive_obligation', 'obligated')
    and (hma_activity_stage = 'geography_not_comparable') = (not hma_geography_comparable));
alter table serving.county_resilience_profile
  add constraint profile_history_null_when_not_comparable check (
    storm_declaration_geography_comparable
    or (storm_events is null and damage_total_usd is null and damage_total_real_usd is null
        and damage_property_usd is null and damage_crops_usd is null
        and deaths is null and injuries is null
        and significant_events is null and significant_events_covered is null
        and significant_damage_usd is null and covered_damage_usd is null
        and major_disasters is null and emergencies is null
        and damage_per_capita is null and damage_per_capita_real is null
        and coverage_rate is null and damage_pctile is null and is_high_loss is null
        and hma_to_damage_ratio is null and not is_high_need_low_obligation));
comment on table serving.county_resilience_profile is
  'One county or county-equivalent in profile scope (key county_fips). Storm/declaration history columns are NULL (not comparable, never zero) for the CT planning regions (storm_declaration_geography_comparable = false: NOAA and OpenFEMA declarations use the legacy counties); those units keep SVI and population and are in no damage rank. HMA columns: county-mapped subapplications that reached FEMA, all profile fiscal years. not_observed means no county-mapped subapplication was observed, not that the county never applied. geography_not_comparable marks the CT planning regions (HMA uses the legacy counties). is_high_need_low_obligation is a follow-up screen, not evidence of neglect.';
comment on column serving.county_resilience_profile.storm_declaration_geography_comparable is
  'false for the CT planning regions: storm, damage, casualty, coverage and declaration history (2000-2025) is recorded on the legacy counties, so these columns are NULL here, not zero.';
comment on column serving.county_resilience_profile.damage_pctile is
  'Percent rank of constant-dollar damage per resident within the analysis universe, over units with a population and comparable storm history. NULL otherwise (incl. CT planning regions).';
comment on column serving.county_resilience_profile.hma_activity_stage is
  'geography_not_comparable | not_observed | observed_no_positive_obligation | obligated, over all profile fiscal years; county-mapped subapplications only.';
comment on column serving.county_resilience_profile.is_low_obligation is
  'hma_pctile <= 0.5 within the unit''s own analysis universe (Puerto Rico uses its own median). NULL without population or HMA-comparable geography. The follow-up screen uses it for states + DC only.';
comment on column serving.county_resilience_profile.hma_obligated_real_usd is
  'Recorded obligation: county shares of positive totalObligatedAmount, constant base-year dollars.';
comment on column serving.county_resilience_profile.hma_status_mix is
  'Distinct county-mapped subapplications by OpenFEMA status, verbatim.';
comment on column serving.county_resilience_profile.is_high_need_low_obligation is
  'Follow-up screen (states + DC; units with comparable storm/declaration history and comparable HMA geography): top-quartile constant-dollar damage per resident, SVI quartile 4, constant-dollar recorded HMA obligation per resident <= states + DC median of comparable units. Not evidence of neglect or non-application.';
