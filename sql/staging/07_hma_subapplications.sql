-- Flatten OpenFEMA HmaSubapplications JSON, then resolve the free-text "benefitingCounties"
-- list (e.g. "BUCKS; DAUPHIN COUNTY; Statewide") to county FIPS codes.
drop table if exists staging.hma_subapplications cascade;
create table staging.hma_subapplications as
with recs as (
  select distinct on (record->>'id') record, _loaded_at
  from raw.openfema_records
  where dataset = 'HmaSubapplications'
  order by record->>'id', _loaded_at desc
)
select
  record->>'id'                                         as hma_id,
  record->>'subapplicationIdentifier'                   as subapplication_identifier,
  record->>'program'                                    as program,
  staging.try_int(record->>'fiscalYear')                as fiscal_year,
  upper(record->>'subapplicantStateAbbreviation')       as state_abbr,
  record->>'subapplicantName'                           as subapplicant_name,
  record->>'subapplicantType'                           as subapplicant_type,
  record->>'status'                                     as status,
  record->>'subapplicationType'                         as subapplication_type,
  nullif(btrim(record->>'benefitingCounties'), '')      as benefiting_counties,
  nullif(btrim(record->>'primaryHazard'), '')           as primary_hazard,
  record->>'primaryActivity'                            as primary_activity,
  staging.try_numeric(record->>'totalSubapplicationAmount') as total_subapplication_usd,
  staging.try_numeric(record->>'federalShareAmount')    as federal_share_usd,
  staging.try_numeric(record->>'totalObligatedAmount')  as total_obligated_usd,
  staging.try_numeric(record->>'totalPaymentAmount')    as total_payment_usd,
  staging.try_numeric(record->>'benefitCostRatio')      as benefit_cost_ratio,
  staging.try_int(record->>'disasterNumber')            as disaster_number,
  staging.iso_date(record->>'dateSubmittedToFema')      as date_submitted_to_fema,
  record->>'smallImpoverished'                          as small_impoverished
from recs;

alter table staging.hma_subapplications add primary key (hma_id);

drop table if exists staging.hma_county_bridge cascade;
create table staging.hma_county_bridge as
with parts as (
  select h.hma_id, h.state_abbr, btrim(p.part) as raw_name, p.ord
  from staging.hma_subapplications h
  cross join lateral regexp_split_to_table(h.benefiting_counties, '\s*[;,]\s*')
       with ordinality as p(part, ord)
  where h.benefiting_counties is not null and btrim(p.part) <> ''
),
dedup as (  -- the same county listed twice in one subapplication counts once
  select distinct on (hma_id, coalesce(staging.county_name_key(raw_name), raw_name))
         hma_id, state_abbr, raw_name, ord, staging.county_name_key(raw_name) as name_key
  from parts
  order by hma_id, coalesce(staging.county_name_key(raw_name), raw_name), ord
),
aliases as (
  select state_abbr, staging.county_name_key(alias) as name_key, county_fips
  from staging.seed_county_name_aliases
),
matched as (
  select d.*,
         case when d.name_key in ('STATEWIDE', 'ALL', 'ALLCOUNTIES') then null
              else coalesce(
                -- 1) exact name-key match within the applicant's state
                (select min(cr.county_fips) from staging.county_ref cr
                 where cr.state_abbr = d.state_abbr and cr.name_key = d.name_key),
                -- 2) explicit, documented alias
                (select min(a.county_fips) from aliases a
                 where a.state_abbr = d.state_abbr and a.name_key = d.name_key)) end
           as county_fips,
         d.name_key in ('STATEWIDE', 'ALL', 'ALLCOUNTIES') as is_statewide,
         -- 3) no in-state match, but the name exists elsewhere: a multi-state subapplication.
         --    Not guessed (e.g. SALINE exists in five states); classified separately.
         exists (select 1 from staging.county_ref cr
                 where cr.state_abbr <> d.state_abbr and cr.name_key = d.name_key)
           as name_exists_elsewhere
  from dedup d
),
with_missing as (  -- subapplications that list no county at all keep one 'unspecified' row
  select hma_id, state_abbr, raw_name, ord, name_key, county_fips, is_statewide,
         name_exists_elsewhere
  from matched
  union all
  select h.hma_id, h.state_abbr, null, 1, null, null, false, false
  from staging.hma_subapplications h
  where not exists (select 1 from matched m where m.hma_id = h.hma_id)
)
select hma_id, ord as part_ord, state_abbr, raw_name, name_key, county_fips,
  case when county_fips is not null then 'county'
       when is_statewide then 'statewide'
       when raw_name is null then 'unspecified'
       when name_exists_elsewhere then 'out_of_state'
       else 'unresolved' end                                   as area_level,
  1.0 / count(*) over (partition by hma_id)                    as allocation_weight
from with_missing;

alter table staging.hma_county_bridge add primary key (hma_id, part_ord);
