-- Grain: one HMA subapplication x one benefiting area / primary key (hma_id, part_ord).
-- Every row is a subapplication published in OpenFEMA HmaSubapplications, i.e. one that reached
-- FEMA; proposals that stopped at the local or state stage are not in the source.
-- Money is split equally across the areas a subapplication lists (documented assumption); the
-- shares of one subapplication sum to its totals (critical check hma_allocation_reconciles).
--
-- status                   OpenFEMA `status`, verbatim (a current snapshot such as "Submitted to
--                          FEMA", "Approved", "Obligated"; not a final outcome, never recoded)
-- obligation_amount_class  sign of the subapplication's totalObligatedAmount: positive | zero |
--                          negative (de-obligation adjustments) | not_reported (NULL in source)
-- has_positive_obligation  totalObligatedAmount > 0. Independent of status: a recorded positive
--                          obligation can sit under any status value
-- obligated_usd            this area's share of totalObligatedAmount (NULL -> 0; negatives kept,
--                          so shares reconcile to the source); positive-obligation totals filter
--                          on has_positive_obligation
-- is_weather               hazard family of primaryHazard is a weather hazard (hazard crosswalk);
--                          'Not specified' and 'Unmapped' are not
-- period_start_fy          fixed five-year fiscal-year period (curated.dim_hma_period)
drop table if exists curated.fact_hma_allocation cascade;
create table curated.fact_hma_allocation as
select
  h.hma_id,
  b.part_ord,
  h.subapplication_identifier,
  h.program,
  h.fiscal_year,
  staging.hma_period_start(h.fiscal_year)                      as period_start_fy,
  h.state_abbr,
  b.area_level,
  b.county_fips,
  b.raw_name                                                   as benefiting_area_raw,
  h.status,
  h.subapplication_type,
  h.primary_hazard,
  coalesce(hz.hazard_family, case when h.primary_hazard is null then 'Not specified'
                                  else 'Unmapped' end)        as hazard_family,
  coalesce(hz.is_weather, false)                               as is_weather,
  case when h.total_obligated_usd is null then 'not_reported'
       when h.total_obligated_usd > 0     then 'positive'
       when h.total_obligated_usd = 0     then 'zero'
       else 'negative' end                                     as obligation_amount_class,
  coalesce(h.total_obligated_usd, 0) > 0                       as has_positive_obligation,
  b.allocation_weight,
  coalesce(h.total_obligated_usd, 0) * b.allocation_weight     as obligated_usd,
  coalesce(h.federal_share_usd, 0) * b.allocation_weight       as federal_share_usd,
  coalesce(h.total_subapplication_usd, 0) * b.allocation_weight as requested_usd,
  -- constant base-year dollars (CPI-U averaged over the federal fiscal year)
  coalesce(h.total_obligated_usd, 0) * b.allocation_weight * infl.factor_to_base
                                                               as obligated_real_usd,
  infl.factor_to_base                                          as cpi_factor_to_base,
  h.benefit_cost_ratio,
  h.disaster_number
from staging.hma_subapplications h
join staging.hma_county_bridge b using (hma_id)
left join curated.dim_hazard hz on hz.source = 'hma' and hz.source_value = h.primary_hazard
left join curated.dim_inflation infl on infl.period_type = 'fiscal_year' and infl.period = h.fiscal_year
cross join ops.active_profile p
where h.fiscal_year between p.start_year and p.end_year
  and (p.states is null or h.state_abbr = any (p.states));

alter table curated.fact_hma_allocation add primary key (hma_id, part_ord);
alter table curated.fact_hma_allocation
  add constraint fk_hma_county foreign key (county_fips) references curated.dim_county;
alter table curated.fact_hma_allocation
  add constraint fk_hma_period foreign key (period_start_fy) references curated.dim_hma_period;
alter table curated.fact_hma_allocation
  add constraint hma_obligation_class_valid check (
    obligation_amount_class in ('positive', 'zero', 'negative', 'not_reported')
    and has_positive_obligation = (obligation_amount_class = 'positive'));
create index on curated.fact_hma_allocation (county_fips);
create index on curated.fact_hma_allocation (county_fips, period_start_fy);
comment on table curated.fact_hma_allocation is
  'One HMA subapplication that reached FEMA x one benefiting area (key hma_id, part_ord). status is OpenFEMA''s verbatim current status; has_positive_obligation = totalObligatedAmount > 0, independent of status.';
comment on column curated.fact_hma_allocation.status is
  'OpenFEMA HmaSubapplications status, verbatim; a snapshot, not a final outcome.';
comment on column curated.fact_hma_allocation.has_positive_obligation is
  'totalObligatedAmount > 0 for the whole subapplication (replaces the former is_funded).';
