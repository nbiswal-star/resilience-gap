-- BLS CPI-U (series from ops.active_profile, default CUUR0000SA0), flattened from API JSON.
--  staging.cpi_monthly      one row per month; value_raw kept (e.g. October 2025 is not published:
--                           "Data unavailable due to the 2025 lapse in appropriations")
--  staging.cpi_annual       calendar-year index: the BLS-published annual average (period M13)
--                           when the API returned it, otherwise the mean of 12 monthly values;
--                           NULL if neither is available (never imputed)
--  staging.cpi_fiscal_year  federal fiscal year N = October N-1 .. September N, mean of 12 months
drop table if exists staging.cpi_monthly cascade;
drop table if exists staging.cpi_annual cascade;
drop table if exists staging.cpi_fiscal_year cascade;

create table staging.cpi_monthly as
select distinct on (year, month)
  r.series_id,
  (r.record->>'year')::int                                  as year,
  substring(r.record->>'period' from 2)::int                as month,
  r.record->>'value'                                        as value_raw,
  staging.try_numeric(r.record->>'value')                   as index_value,
  r.record->'footnotes'                                     as footnotes,
  r._object_key
from raw.bls_cpi_records r
cross join ops.active_profile p
where r.series_id = p.cpi_series_id
  and r.record->>'period' ~ '^M(0[1-9]|1[0-2])$'
order by year, month, r._loaded_at desc;
alter table staging.cpi_monthly add primary key (year, month);

create table staging.cpi_annual as
with published as (
  select distinct on ((r.record->>'year')::int)
         (r.record->>'year')::int as year, staging.try_numeric(r.record->>'value') as v
  from raw.bls_cpi_records r cross join ops.active_profile p
  where r.series_id = p.cpi_series_id and r.record->>'period' = 'M13'
  order by (r.record->>'year')::int, r._loaded_at desc
),
monthly as (
  select year, count(index_value) as months_available,
         case when count(index_value) = 12 then round(avg(index_value), 3) end as computed
  from staging.cpi_monthly group by year
)
select
  coalesce(m.year, pb.year)                                  as year,
  pb.v                                                       as published_annual_avg,
  m.computed                                                 as computed_annual_avg,
  coalesce(m.months_available, 0)                            as months_available,
  coalesce(pb.v, m.computed)                                 as cpi,
  case when pb.v is not null then 'bls_published_annual_average'
       when m.computed is not null then 'mean_of_12_months' end as cpi_basis
from monthly m
full outer join published pb on pb.year = m.year;
alter table staging.cpi_annual add primary key (year);

create table staging.cpi_fiscal_year as
select fy, count(index_value) as months_available,
       case when count(index_value) = 12 then round(avg(index_value), 3) end as cpi
from (
  select case when month >= 10 then year + 1 else year end as fy, index_value
  from staging.cpi_monthly
) t
group by fy;
alter table staging.cpi_fiscal_year add primary key (fy);
