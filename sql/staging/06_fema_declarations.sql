-- Flatten OpenFEMA DisasterDeclarationsSummaries JSON (one record per declared area).
-- area_level: county | statewide (designatedArea literally "Statewide") | tribal (reservations,
-- tribal statistical areas and Alaska Native villages, mostly fipsCountyCode '000') | unresolved.
-- designated_area is kept verbatim; only 'county' rows carry a county_fips.
drop table if exists staging.fema_declarations cascade;
create table staging.fema_declarations as
with recs as (
  select distinct on (record->>'id') record, _loaded_at, _object_key
  from raw.openfema_records
  where dataset = 'DisasterDeclarationsSummaries'
  order by record->>'id', _loaded_at desc
),
flat as (
  select
    record->>'id'                                           as declaration_area_id,
    staging.try_int(record->>'disasterNumber')              as disaster_number,
    record->>'femaDeclarationString'                        as fema_declaration_string,
    record->>'declarationType'                              as declaration_type,
    upper(record->>'state')                                 as state_abbr,
    staging.iso_date(record->>'declarationDate')            as declaration_date,
    staging.iso_date(record->>'declarationRequestDate')     as declaration_request_date,
    staging.iso_date(record->>'incidentBeginDate')          as incident_begin_date,
    staging.iso_date(record->>'incidentEndDate')            as incident_end_date,
    staging.iso_date(record->>'disasterCloseoutDate')       as closeout_date,
    record->>'incidentType'                                 as incident_type,
    record->>'declarationTitle'                             as declaration_title,
    (record->>'ihProgramDeclared')::boolean                 as ih_program,
    (record->>'iaProgramDeclared')::boolean                 as ia_program,
    (record->>'paProgramDeclared')::boolean                 as pa_program,
    (record->>'hmProgramDeclared')::boolean                 as hm_program,
    (record->>'tribalRequest')::boolean                     as tribal_request,
    record->>'fipsStateCode'                                as fips_state_code,
    record->>'fipsCountyCode'                               as fips_county_code,
    record->>'placeCode'                                    as place_code,
    record->>'designatedArea'                               as designated_area,
    staging.try_int(record->>'region')                      as fema_region,
    -- "4,F" -> {4,F}: multi-valued field flattened to an array
    string_to_array(nullif(record->>'designatedIncidentTypes', ''), ',') as designated_incident_codes,
    _object_key
  from recs
)
select f.*,
  -- statewide only when designatedArea is literally "Statewide"; code-000 tribal areas stay
  -- tribal and are never expanded to counties (see staging.fema_area_level)
  staging.fema_area_level(f.designated_area, f.fips_county_code, f.place_code,
                          f.tribal_request, cr.county_fips is not null)   as area_level,
  case when staging.fema_area_level(f.designated_area, f.fips_county_code, f.place_code,
                                    f.tribal_request, cr.county_fips is not null) = 'county'
       then cr.county_fips end                                     as county_fips
from flat f
left join staging.county_ref cr
  on cr.county_fips = f.fips_state_code || f.fips_county_code;

alter table staging.fema_declarations add primary key (declaration_area_id);
create index on staging.fema_declarations (county_fips);
create index on staging.fema_declarations (state_abbr, area_level);
