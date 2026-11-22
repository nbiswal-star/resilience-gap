-- Trend table. Grain: state x year / primary key (state_abbr, year). HMA uses fiscal year;
-- declarations and turned-down requests use the incident (begin) year. Turndowns are
-- weather-related Major Disaster requests whose OpenFEMA status is "Turndown"
-- (curated.fact_declaration_denial): government-level outcomes of presidential declaration
-- requests, unrelated to HMA subapplications. HMA columns count subapplications that reached
-- FEMA (any benefiting-area level, by the applicant's state) and their recorded obligation
-- (shares of positive totalObligatedAmount).
--
-- SCOPE: rows exist only for states, DC and territories of curated.dim_county. HMA subapplications
-- with NO applicant state (subapplicantStateAbbreviation empty) or an applicant outside that list
-- (e.g. FM, the Federated States of Micronesia) are in curated.fact_hma_allocation but have no row
-- here, so this table is NOT a national HMA total. The excluded subapplications and their recorded
-- obligation are counted by DQ check state_year_hma_out_of_scope (info) and reconciled exactly by
-- state_year_hma_reconciles (critical): state-year rows + excluded = curated fact.
drop table if exists serving.state_year_summary cascade;
create table serving.state_year_summary as
with years as (
  select generate_series(start_year, end_year) as year from ops.active_profile
),
states as (
  select distinct state_abbr, fema_region from curated.dim_county
  where in_profile_scope and state_abbr is not null
),
storms as (
  select state_abbr, event_year as year, count(distinct event_id) as storm_events,
         sum(damage_total_usd) as damage_total_usd,
         sum(damage_total_real_usd) as damage_total_real_usd, sum(deaths) as deaths
  from curated.fact_storm_event_county where is_weather group by 1, 2
),
decl as (
  select state_abbr, extract(year from incident_begin_date)::int as year,
         count(distinct disaster_number) filter (where declaration_type = 'DR') as major_disasters,
         count(distinct disaster_number) filter (where declaration_type = 'EM') as emergencies
  from curated.fact_declaration_area where is_weather group by 1, 2
),
denials as (
  select state_abbr, incident_year as year,
         count(*) filter (where declaration_type = 'DR') as major_disaster_turndowns
  from curated.fact_declaration_denial where is_weather group by 1, 2
),
hma as (  -- recorded obligation = shares of positive totalObligatedAmount; every area level
  select state_abbr, fiscal_year as year,
         count(distinct hma_id)                                              as hma_observed_subapplications,
         count(distinct hma_id) filter (where has_positive_obligation)       as hma_positive_obligation_subapplications,
         sum(obligated_usd) filter (where has_positive_obligation)           as hma_obligated_usd,
         sum(obligated_real_usd) filter (where has_positive_obligation)      as hma_obligated_real_usd,
         sum(obligated_usd) filter (where has_positive_obligation and area_level = 'county')
                                                                             as hma_county_obligated_usd,
         sum(obligated_usd) filter (where has_positive_obligation and area_level <> 'county')
                                                                             as hma_noncounty_obligated_usd
  from curated.fact_hma_allocation group by 1, 2
)
select s.state_abbr, s.fema_region, y.year,
  coalesce(st.storm_events, 0)               as storm_events,
  coalesce(st.damage_total_usd, 0)           as damage_total_usd,
  coalesce(st.damage_total_real_usd, 0)      as damage_total_real_usd,
  coalesce(st.deaths, 0)                     as deaths,
  coalesce(d.major_disasters, 0)             as major_disasters,
  coalesce(d.emergencies, 0)                 as emergencies,
  coalesce(dn.major_disaster_turndowns, 0)   as major_disaster_turndowns,
  coalesce(h.hma_observed_subapplications, 0) as hma_observed_subapplications,
  coalesce(h.hma_positive_obligation_subapplications, 0)
                                             as hma_positive_obligation_subapplications,
  coalesce(h.hma_obligated_usd, 0)           as hma_obligated_usd,
  coalesce(h.hma_obligated_real_usd, 0)      as hma_obligated_real_usd,
  coalesce(h.hma_county_obligated_usd, 0)    as hma_county_obligated_usd,
  coalesce(h.hma_noncounty_obligated_usd, 0) as hma_noncounty_obligated_usd
from states s
cross join years y
left join storms st on st.state_abbr = s.state_abbr and st.year = y.year
left join decl d on d.state_abbr = s.state_abbr and d.year = y.year
left join denials dn on dn.state_abbr = s.state_abbr and dn.year = y.year
left join hma h on h.state_abbr = s.state_abbr and h.year = y.year;

alter table serving.state_year_summary add primary key (state_abbr, year);
comment on table serving.state_year_summary is
  'State x year. HMA columns cover subapplications whose applicant state is in curated.dim_county; subapplications with no applicant state or another applicant (e.g. FM) are excluded and reconciled by DQ checks state_year_hma_out_of_scope / state_year_hma_reconciles. Not a national HMA total.';
