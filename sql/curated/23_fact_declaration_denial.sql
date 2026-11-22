-- Grain: one turned-down declaration request (OpenFEMA DeclarationDenials record), in profile
-- scope by requested incident begin year and state. State-level only: denial records carry no
-- county. A row means the request's current status in OpenFEMA is "Turndown"; it is not a
-- measure of damage, and requests granted on appeal or never submitted are not in this table.
drop table if exists curated.fact_declaration_denial cascade;
create table curated.fact_declaration_denial as
select
  d.denial_id,
  d.declaration_request_number,
  d.state_abbr,
  d.fema_region,
  d.tribal_request,
  d.request_type,
  case d.request_type when 'Major Disaster' then 'DR' when 'Emergency' then 'EM' end
                                                             as declaration_type,
  d.incident_name,
  d.requested_incident_types,
  coalesce(h.hazard_family, 'Unmapped')                      as hazard_family,
  coalesce(h.is_weather, false)                              as is_weather,
  d.incident_begin_date,
  d.incident_end_date,
  extract(year from d.incident_begin_date)::int              as incident_year,
  d.request_date,
  d.status_date,
  d.request_status,
  d.status_date - d.request_date                             as days_request_to_decision,
  d.ia_requested, d.pa_requested, d.hm_requested
from staging.fema_declaration_denials d
left join curated.dim_hazard h on h.source = 'fema' and h.source_value = d.requested_incident_types
cross join ops.active_profile p
where extract(year from d.incident_begin_date) between p.start_year and p.end_year
  and (p.states is null or d.state_abbr = any (p.states));

alter table curated.fact_declaration_denial add primary key (denial_id);
create index on curated.fact_declaration_denial (state_abbr, incident_year);
