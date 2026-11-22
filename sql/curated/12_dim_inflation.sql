-- Deflators to restate nominal dollars in constant base-year dollars:
--   real = nominal x CPI(base_year annual average) / CPI(period of the amount)
-- period_type 'calendar_year' (storm damage, by event year) or 'fiscal_year' (FEMA HMA, by
-- federal fiscal year Oct-Sep). The base year comes from ops.active_profile.cpi_base_year.
drop table if exists curated.dim_inflation cascade;
create table curated.dim_inflation as
with base as (
  select p.cpi_base_year as base_year, a.cpi as base_cpi
  from ops.active_profile p
  left join staging.cpi_annual a on a.year = p.cpi_base_year
)
select 'calendar_year'::text as period_type, a.year as period, a.cpi, a.cpi_basis,
       b.base_year, b.base_cpi, b.base_cpi / a.cpi as factor_to_base
from staging.cpi_annual a cross join base b
where a.cpi is not null
union all
select 'fiscal_year', f.fy, f.cpi, 'mean_of_12_months_oct_sep', b.base_year, b.base_cpi,
       b.base_cpi / f.cpi
from staging.cpi_fiscal_year f cross join base b
where f.cpi is not null;

alter table curated.dim_inflation add primary key (period_type, period);
