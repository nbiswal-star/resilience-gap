-- Q3 status mix. Grain: five-year fiscal-year period x HMA program x OpenFEMA status x county
-- mapping / unique key (period_start_fy, program, status, county_mapping), NULLs equal.
--
-- Unit: one SUBAPPLICATION in profile scope (each counted once, whatever its number of benefiting
-- areas), including those with no county-mapped area, so the table reconciles with every
-- subapplication in curated.fact_hma_allocation (critical check hma_status_summary_reconciles).
--   status          OpenFEMA status, verbatim (NULL kept as NULL). A current snapshot such as
--                   "Submitted to FEMA", "Approved", "Obligated", "Subaward Accepted"; never
--                   recoded. "Approved" appears only where the source says "Approved"
--   county_mapping  county_mapped (>= 1 benefiting area resolved to a county) or
--                   state_level_only (statewide, out-of-state, unresolved or unspecified areas)
--   obligated_*     recorded obligation: positive totalObligatedAmount of the subapplications
--                   (whole amounts, not county shares); county_attributed_obligated_* is the part
--                   allocated to county-mapped areas
-- Status and obligation are separate fields in the source: a positive obligation can appear under
-- any status, and a status such as "Approved" can carry no recorded obligation.
drop table if exists serving.hma_status_summary cascade;
create table serving.hma_status_summary as
with sub as (  -- one row per subapplication
  select hma_id, period_start_fy, program, status, obligation_amount_class,
         has_positive_obligation,
         bool_or(area_level = 'county')                                  as is_county_mapped,
         sum(obligated_usd)                                              as total_obligated_usd,
         sum(obligated_real_usd)                                         as total_obligated_real_usd,
         coalesce(sum(obligated_usd) filter (where area_level = 'county'), 0)
                                                                         as county_obligated_usd,
         coalesce(sum(obligated_real_usd) filter (where area_level = 'county'), 0)
                                                                         as county_obligated_real_usd
  from curated.fact_hma_allocation
  group by hma_id, period_start_fy, program, status, obligation_amount_class,
           has_positive_obligation
)
select
  s.period_start_fy, p.period_label, p.fiscal_years_in_period, p.is_partial_period,
  s.program,
  s.status,
  case when s.is_county_mapped then 'county_mapped' else 'state_level_only' end as county_mapping,
  count(*)                                                                   as subapplications,
  count(*) filter (where s.has_positive_obligation)                         as positive_obligation_subapplications,
  count(*) filter (where s.obligation_amount_class = 'zero')                as zero_obligation_subapplications,
  count(*) filter (where s.obligation_amount_class = 'negative')            as negative_obligation_subapplications,
  count(*) filter (where s.obligation_amount_class = 'not_reported')        as obligation_not_reported_subapplications,
  coalesce(sum(s.total_obligated_usd) filter (where s.has_positive_obligation), 0)
                                                                             as obligated_usd,
  coalesce(sum(s.total_obligated_real_usd) filter (where s.has_positive_obligation), 0)
                                                                             as obligated_real_usd,
  coalesce(sum(s.county_obligated_usd) filter (where s.has_positive_obligation), 0)
                                                                             as county_attributed_obligated_usd,
  coalesce(sum(s.county_obligated_real_usd) filter (where s.has_positive_obligation), 0)
                                                                             as county_attributed_obligated_real_usd
from sub s
join curated.dim_hma_period p using (period_start_fy)
group by s.period_start_fy, p.period_label, p.fiscal_years_in_period, p.is_partial_period,
         s.program, s.status, s.is_county_mapped;

-- unique key with NULL program/status treated as one value (expression index: PostgreSQL 14+)
create unique index hma_status_summary_key on serving.hma_status_summary
  (period_start_fy, coalesce(program, '<NULL>'), coalesce(status, '<NULL>'), county_mapping);
comment on table serving.hma_status_summary is
  'Subapplications in profile scope by period, program, verbatim OpenFEMA status and county mapping. Status is a snapshot; status and recorded obligation are independent source fields.';
comment on column serving.hma_status_summary.status is
  'OpenFEMA HmaSubapplications status, verbatim; NULL kept.';
