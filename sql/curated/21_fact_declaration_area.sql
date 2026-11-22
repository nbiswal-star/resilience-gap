-- Grain: one FEMA declaration x one designated area (county, statewide, tribal, unresolved).
-- Only 'county' rows have a county_fips. 'statewide' rows are literal "Statewide" designations;
-- 'tribal' rows (reservations, tribal statistical areas, Alaska Native villages) keep their
-- designated_area text and place_code and are never expanded to counties.
drop table if exists curated.fact_declaration_area cascade;
create table curated.fact_declaration_area as
select
  f.declaration_area_id,
  f.disaster_number,
  f.fema_declaration_string,
  f.declaration_type,
  f.state_abbr,
  f.fema_region,
  f.area_level,
  f.county_fips,
  f.designated_area,
  f.place_code,
  coalesce(f.tribal_request, false)                          as tribal_request,
  f.incident_type,
  coalesce(h.hazard_family, 'Unmapped')                      as hazard_family,
  coalesce(h.is_weather, false)                              as is_weather,
  f.declaration_title,
  f.incident_begin_date,
  f.incident_end_date,
  f.declaration_request_date,
  f.declaration_date,
  f.ih_program, f.ia_program, f.pa_program, f.hm_program,
  f.declaration_date - f.incident_begin_date                 as days_incident_to_declaration,
  f.declaration_date - f.declaration_request_date            as days_request_to_declaration
from staging.fema_declarations f
left join curated.dim_hazard h on h.source = 'fema' and h.source_value = f.incident_type
cross join ops.active_profile p
where extract(year from f.incident_begin_date) between p.start_year and p.end_year
  and (p.states is null or f.state_abbr = any (p.states));

alter table curated.fact_declaration_area add primary key (declaration_area_id);
alter table curated.fact_declaration_area
  add constraint fk_decl_county foreign key (county_fips) references curated.dim_county;
alter table curated.fact_declaration_area
  add constraint ck_decl_county_only_for_county_rows
  check ((area_level = 'county') = (county_fips is not null));
create index on curated.fact_declaration_area (county_fips, incident_begin_date);
create index on curated.fact_declaration_area (state_abbr, area_level);
