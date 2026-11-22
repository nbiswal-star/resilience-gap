-- OpenFEMA DeclarationDenials: declaration requests whose current status is "Turndown".
-- One row per request record. A turndown is FEMA's recorded decision on a state, territory or
-- tribal request; it does not say how much damage occurred, and a request granted later on
-- appeal or never submitted does not appear here.
drop table if exists staging.fema_declaration_denials cascade;
create table staging.fema_declaration_denials as
with recs as (
  select distinct on (record->>'id') record, _loaded_at, _object_key
  from raw.openfema_records
  where dataset = 'DeclarationDenials'
  order by record->>'id', _loaded_at desc
)
select
  record->>'id'                                         as denial_id,
  staging.try_int(record->>'declarationRequestNumber')  as declaration_request_number,
  upper(record->>'stateAbbreviation')                   as state_abbr,
  staging.try_int(record->>'region')                    as fema_region,
  (record->>'tribalRequest')::boolean                   as tribal_request,
  record->>'declarationRequestType'                     as request_type,
  record->>'incidentName'                               as incident_name,
  record->>'requestedIncidentTypes'                     as requested_incident_types,
  staging.iso_date(record->>'declarationRequestDate')   as request_date,
  staging.iso_date(record->>'requestedIncidentBeginDate') as incident_begin_date,
  staging.iso_date(record->>'requestedIncidentEndDate') as incident_end_date,
  record->>'currentRequestStatus'                       as request_status,
  staging.iso_date(record->>'requestStatusDate')        as status_date,
  (record->>'ihProgramRequested')::boolean              as ih_requested,
  (record->>'iaProgramRequested')::boolean              as ia_requested,
  (record->>'paProgramRequested')::boolean              as pa_requested,
  (record->>'hmProgramRequested')::boolean              as hm_requested,
  _object_key
from recs;

alter table staging.fema_declaration_denials add primary key (denial_id);
