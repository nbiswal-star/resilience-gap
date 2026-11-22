-- HMA reporting periods. Grain: one fixed five-year federal fiscal-year period that overlaps the
-- active profile / primary key period_start_fy.
--
-- Boundaries come from staging.hma_period_start: FY2001-2005, FY2006-2010, ..., FY2021-2025, with
-- FY2000 in FY1996-2000. They are fixed, so a fiscal year falls in the same period in every
-- profile. Each period is clipped to the profile years (first_fy_in_profile..last_fy_in_profile);
-- a clipped period is flagged is_partial_period, and fiscal_years_in_period says how many fiscal
-- years it really holds (the full profile has a one-year FY2000 period; the fixture profile has a
-- one-year FY2024 period). Compare per-period totals only between periods of equal length.
drop table if exists curated.dim_hma_period cascade;
create table curated.dim_hma_period as
with years as (
  select fy, staging.hma_period_start(fy) as period_start_fy
  from ops.active_profile p, generate_series(p.start_year, p.end_year) fy
)
select
  period_start_fy,
  period_start_fy + 4                                              as period_end_fy,
  min(fy)                                                          as first_fy_in_profile,
  max(fy)                                                          as last_fy_in_profile,
  count(*)::int                                                    as fiscal_years_in_period,
  count(*) < 5                                                     as is_partial_period,
  'FY' || min(fy) || case when max(fy) > min(fy) then '-' || max(fy) else '' end
                                                                   as period_label
from years
group by period_start_fy;

alter table curated.dim_hma_period add primary key (period_start_fy);
alter table curated.dim_hma_period add constraint dim_hma_period_label_unique unique (period_label);
comment on table curated.dim_hma_period is
  'Fixed five-year federal fiscal-year periods (FY2001-2005 ... FY2021-2025; FY2000 in FY1996-2000) clipped to the active profile. Grain: one period; key period_start_fy.';
