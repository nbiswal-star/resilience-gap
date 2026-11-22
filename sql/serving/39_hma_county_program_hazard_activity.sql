-- Q3 by program and hazard family. Grain: one county x five-year fiscal-year period x HMA program
-- x hazard family of the subapplication's primary hazard, for OBSERVED combinations only /
-- unique key (county_fips, period_start_fy, program, hazard_family).
--
-- Sparse by design. A missing row is NOT a statement that the county had no subapplication for
-- that program or hazard: programs exist in different years (e.g. BRIC from FY2020, PDM ended),
-- a subapplication carries a single primary hazard although a project may address several, and
-- statewide or unparsed benefiting areas are not county-mapped. Absence ("not_observed") is
-- stated only at county x period grain (serving.hma_county_period_activity) and county x all
-- periods (serving.county_resilience_profile). hma_activity_stage here is therefore only
-- observed_no_positive_obligation or obligated. Rows exist only where a subapplication resolved to
-- the county, so the CT planning regions (HMA names the legacy counties) normally have none; the
-- legacy CT counties have rows (analysis_universe = legacy_geography).
--
-- Each subapplication has one program, one primary hazard and one fiscal year, so the rows of a
-- county and period add up exactly to serving.hma_county_period_activity (critical check
-- hma_program_hazard_reconciles). program and status are OpenFEMA values, verbatim.
drop table if exists serving.hma_county_program_hazard_activity cascade;
create table serving.hma_county_program_hazard_activity as
with obs as (
  select county_fips, period_start_fy, program, hazard_family,
         bool_and(is_weather)                                                as is_weather_hazard,
         count(distinct hma_id)                                              as observed_subapplications,
         count(distinct hma_id) filter (where has_positive_obligation)       as positive_obligation_subapplications,
         coalesce(sum(obligated_usd) filter (where has_positive_obligation), 0)      as obligated_usd,
         coalesce(sum(obligated_real_usd) filter (where has_positive_obligation), 0) as obligated_real_usd
  from curated.fact_hma_allocation
  where county_fips is not null
  group by county_fips, period_start_fy, program, hazard_family
),
status as (
  select county_fips, period_start_fy, program, hazard_family,
         jsonb_object_agg(status_value, subapplications) as status_mix
  from (select county_fips, period_start_fy, program, hazard_family,
               coalesce(status, '(status not recorded)') as status_value,
               count(distinct hma_id) as subapplications
        from curated.fact_hma_allocation
        where county_fips is not null
        group by 1, 2, 3, 4, 5) s
  group by county_fips, period_start_fy, program, hazard_family
)
select
  o.county_fips, c.county_name, c.state_abbr, c.analysis_universe, c.svi_quartile,
  c.svi_quartile_label, c.hma_geography_comparable,
  o.period_start_fy, p.period_label, p.fiscal_years_in_period, p.is_partial_period,
  o.program, o.hazard_family, o.is_weather_hazard,
  case when o.positive_obligation_subapplications > 0 then 'obligated'
       else 'observed_no_positive_obligation' end                  as hma_activity_stage,
  o.observed_subapplications,
  o.positive_obligation_subapplications,
  o.obligated_usd,
  o.obligated_real_usd,
  s.status_mix
from obs o
join curated.dim_county c using (county_fips)
join curated.dim_hma_period p using (period_start_fy)
join status s on s.county_fips = o.county_fips and s.period_start_fy = o.period_start_fy
             and s.program is not distinct from o.program
             and s.hazard_family = o.hazard_family;

-- unique key with a NULL program treated as one value (expression index: PostgreSQL 14+)
create unique index hma_program_hazard_key on serving.hma_county_program_hazard_activity
  (county_fips, period_start_fy, coalesce(program, '<NULL>'), hazard_family);
alter table serving.hma_county_program_hazard_activity
  add constraint hma_program_hazard_stage_observed check (
    hma_activity_stage in ('observed_no_positive_obligation', 'obligated'));
comment on table serving.hma_county_program_hazard_activity is
  'Observed county x period x program x hazard-family combinations only (sparse). A missing row is not a claim that no subapplication was attempted.';
