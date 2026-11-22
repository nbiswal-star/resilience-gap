-- Hazard dimension from the team-authored crosswalk (seeds/hazard_crosswalk.csv).
drop table if exists curated.dim_hazard cascade;
create table curated.dim_hazard as
select source, source_value, hazard_family, is_weather::boolean as is_weather
from staging.seed_hazard_crosswalk;
alter table curated.dim_hazard add primary key (source, source_value);

-- Which NOAA event hazard families a FEMA declaration of a given hazard family can cover
-- (seeds/hazard_match_rules.csv). Used when matching storm events to declarations (Q2) and when
-- summing NOAA damage inside a declaration's window (Q4).
--   same_family       the families are equal
--   companion_hazard  FEMA declarations of that incident type routinely name the other hazard
--                     too (e.g. "SEVERE STORMS, TORNADOES, AND FLOODING" is incidentType
--                     "Severe Storm"); the evidence is in docs/implementation-plan.md section 4
-- Pairs not listed never match (e.g. Heat or Drought events under a Severe Storm declaration,
-- Wildfire under Flood). Unknown families ('Unmapped', 'Other', 'Other Weather', 'Heat' on the
-- NOAA side) have no rule and therefore never match.
drop table if exists curated.dim_hazard_match cascade;
create table curated.dim_hazard_match as
select declaration_hazard_family, event_hazard_family, rule
from staging.seed_hazard_match_rules;
alter table curated.dim_hazard_match
  add primary key (declaration_hazard_family, event_hazard_family);
