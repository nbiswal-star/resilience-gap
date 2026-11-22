-- County dimension: Census geography + population + CDC/ATSDR SVI, with an explicit analysis
-- universe so that areas without a comparable national SVI are never treated as "low
-- vulnerability" or mixed into national quartiles.
--
-- analysis_universe
--   states_dc         50 states + DC, current geography: national SVI percentile and quartile
--   puerto_rico       78 municipios: SVI ranked only within Puerto Rico (CDC stand-alone file),
--                     kept in svi_pr_within_rank / svi_pr_within_quartile; national columns NULL
--   island_areas      AS, GU, MP, UM, VI: CDC publishes no SVI and Census Vintage 2025 has no
--                     county-level population for them -> no per-capita or SVI measures
--   legacy_geography  areas in the 2020 reference but not in Vintage 2025 (the 8 pre-2022
--                     Connecticut counties): no population/SVI for the old geography
-- fema_region: FEMA region of the state (seeds/fema_regions.csv; checked against the OpenFEMA
-- region field by a critical DQ check). NULL for UM, which has no FEMA region in the data.
--
-- Geography comparability (the 2022 Connecticut geography break)
-- A CURRENT county-equivalent of a state whose county geography changed between the 2020
-- reference and Vintage 2025 (i.e. a state that also has legacy_geography units) cannot be
-- compared with sources that publish that state's history on the legacy counties. Today these
-- are exactly the 9 Connecticut planning regions (09110-09190); the legacy counties are
-- 09001-09015. No defensible county-to-planning-region crosswalk is used, so no legacy fact is
-- ever attributed, split or allocated to a planning region. Two flags, one per source domain,
-- both derived from the same data-driven rule (no hard-coded FIPS):
--   storm_declaration_geography_comparable
--                             false for such current units: NOAA Storm Events and OpenFEMA
--                             Disaster Declarations record 2000-2025 Connecticut history on the
--                             legacy counties, so the regions have no comparable storm-event,
--                             damage, casualty, significant-event coverage, major-disaster or
--                             emergency history. serving.county_resilience_profile sets those
--                             measures (and per-capita damage, damage rank, coverage rate) to
--                             NULL, not zero, and excludes the units from damage ranks, damage
--                             quartiles, SVI-quartile damage and coverage numerators and
--                             denominators, and the follow-up screen. SVI and population are kept,
--                             so the regions stay visible as current social geography.
--   hma_geography_comparable  false for the same units: OpenFEMA HMA benefitingCounties name the
--                             legacy counties, so the regions get the HMA stage
--                             'geography_not_comparable' and are excluded from HMA denominators,
--                             ranks, medians and the follow-up screen.
-- Both flags are true for every other unit, including the legacy counties themselves: their
-- storm, declaration and HMA records are real pre-change history and stay attributed to them, as a
-- separate universe (no population or SVI). Legacy-county storm and declaration facts may remain
-- in national historical event totals; only the current-geography HMA headline shares leave the
-- legacy counties out.
--   storm_declaration_geography_note / hma_geography_note   plain-language reasons, NULL for
--                             ordinary units.
-- Critical DQ checks (storm_declaration_geography_break_guard, hma_geography_break_guard) are
-- triggered by facts on legacy geography, independently of these flags, and fail if current units
-- of such a state are ever given ordinary historical metrics, ranks or HMA stages.
-- National SVI quartiles use fixed cut points on the national percentile, so a sample run and a
-- full run classify a county identically.
drop table if exists curated.dim_county cascade;
create table curated.dim_county as
with base as (
  select
    c.*,
    s.svi_universe,
    s.svi_total_pop, s.area_sqmi,
    s.svi_overall          as svi_rank_raw,
    s.svi_socioeconomic, s.svi_household, s.svi_minority_language, s.svi_housing_transport,
    s.pct_below_150_poverty, s.pct_uninsured, s.pct_age65_plus, s.pct_disability,
    s.pct_mobile_homes, s.pct_no_vehicle,
    case
      when c.state_fips in ('60', '66', '69', '74', '78') then 'island_areas'
      when c.state_fips = '72'                            then 'puerto_rico'
      when not c.in_popest_2025                           then 'legacy_geography'
      else 'states_dc'
    end as analysis_universe
  from staging.county_ref c
  left join staging.svi_county s using (county_fips)
),
geo as (  -- current unit of a state whose county geography changed (has legacy_geography units)
  select b.*,
         not (b.analysis_universe <> 'legacy_geography'
              and exists (select 1 from base l
                          where l.state_fips = b.state_fips
                            and l.analysis_universe = 'legacy_geography')) as geography_unchanged
  from base b
)
select
  b.county_fips, b.state_fips, b.state_abbr, b.state_name, b.county_name, b.name_key,
  b.in_popest_2025, b.in_reference_2020, b.analysis_universe,
  b.geography_unchanged                                                       as storm_declaration_geography_comparable,
  case
    when not b.geography_unchanged
      then 'Current planning region (2022 geography). NOAA Storm Events and OpenFEMA declarations '
           'record 2000-2025 history of ' || coalesce(b.state_abbr, 'this state')
           || ' on the legacy counties, so storm, damage, declaration and coverage history is not '
              'comparable here and is left NULL (not zero). SVI and population are current.'
    when b.analysis_universe = 'legacy_geography'
      then 'Legacy county (pre-2022 geography). Its storm and declaration records are real '
           'pre-change history and stay attributed here; no population or SVI.'
  end                                                                         as storm_declaration_geography_note,
  b.geography_unchanged                                                       as hma_geography_comparable,
  case
    when not b.geography_unchanged
      then 'Current planning region (2022 geography). OpenFEMA HMA names the legacy counties of '
           || coalesce(b.state_abbr, 'this state')
           || ', so HMA activity is not attributed across the geography break.'
    when b.analysis_universe = 'legacy_geography'
      then 'Legacy county (pre-2022 geography). HMA records are attributed here; kept separate '
           'from the current-geography HMA headline shares to avoid double counting.'
  end                                                                         as hma_geography_note,
  fr.fema_region::int                                                         as fema_region,
  b.pop_2020, b.pop_2022, b.pop_2025,
  case when b.analysis_universe in ('states_dc', 'puerto_rico')
       then coalesce(b.pop_2022, b.svi_total_pop) end                        as population_base,
  b.area_sqmi,
  case
    when b.svi_universe = 'united_states_national' and b.svi_rank_raw is not null
      then 'national_svi'
    when b.svi_universe = 'puerto_rico_only' and b.svi_rank_raw is not null
      then 'puerto_rico_svi_ranked_within_pr'
    when b.analysis_universe = 'island_areas'     then 'no_svi_published'
    when b.analysis_universe = 'legacy_geography' then 'no_svi_legacy_geography'
    else 'svi_missing'
  end                                                                         as svi_coverage,
  -- national percentile and themes: ONLY for the national ranking universe
  case when b.svi_universe = 'united_states_national' then b.svi_rank_raw end as svi_overall,
  case when b.svi_universe = 'united_states_national' then b.svi_socioeconomic end as svi_socioeconomic,
  case when b.svi_universe = 'united_states_national' then b.svi_household end as svi_household,
  case when b.svi_universe = 'united_states_national' then b.svi_minority_language end
                                                                             as svi_minority_language,
  case when b.svi_universe = 'united_states_national' then b.svi_housing_transport end
                                                                             as svi_housing_transport,
  case when b.svi_universe = 'united_states_national' then
    case when b.svi_rank_raw < 0.25 then 1 when b.svi_rank_raw < 0.50 then 2
         when b.svi_rank_raw < 0.75 then 3 else 4 end end                    as svi_quartile,
  case
    when b.svi_universe = 'united_states_national' then
      case when b.svi_rank_raw < 0.25 then 'Q1 (least vulnerable)'
           when b.svi_rank_raw < 0.50 then 'Q2'
           when b.svi_rank_raw < 0.75 then 'Q3'
           else 'Q4 (most vulnerable)' end
    when b.analysis_universe = 'puerto_rico'      then 'Puerto Rico (SVI ranked within PR)'
    when b.analysis_universe = 'island_areas'     then 'Island areas (no SVI published)'
    when b.analysis_universe = 'legacy_geography' then 'Legacy geography (no SVI)'
    else 'No SVI'
  end                                                                         as svi_quartile_label,
  -- Puerto Rico's own ranking (comparable only among PR municipios)
  case when b.svi_universe = 'puerto_rico_only' then b.svi_rank_raw end      as svi_pr_within_rank,
  case when b.svi_universe = 'puerto_rico_only' then
    case when b.svi_rank_raw < 0.25 then 1 when b.svi_rank_raw < 0.50 then 2
         when b.svi_rank_raw < 0.75 then 3 else 4 end end                    as svi_pr_within_quartile,
  b.pct_below_150_poverty, b.pct_uninsured, b.pct_age65_plus, b.pct_disability,
  b.pct_mobile_homes, b.pct_no_vehicle,
  (p.states is null or b.state_abbr = any (p.states))                       as in_profile_scope
from geo b
left join staging.seed_fema_regions fr on fr.state_abbr = b.state_abbr
cross join ops.active_profile p;

alter table curated.dim_county add primary key (county_fips);
comment on column curated.dim_county.storm_declaration_geography_comparable is
  'false for current units of a state whose county geography changed (the 9 Connecticut planning regions): NOAA Storm Events and OpenFEMA declarations record 2000-2025 history on the legacy counties, so storm, damage, declaration and coverage history is NULL (not zero) for these units and they are excluded from damage ranks, damage quartiles, SVI-quartile damage/coverage and the follow-up screen. SVI and population are kept.';
comment on column curated.dim_county.hma_geography_comparable is
  'false for current units of a state whose county geography changed (the 9 Connecticut planning regions): HMA names the legacy counties, so HMA is not attributed to these units.';
