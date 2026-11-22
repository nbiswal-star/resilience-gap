-- Q2/Q4 context: weather-related declaration requests that were declared vs turned down.
-- Grain: state x incident year x declaration type (DR, EM) x hazard family; only combinations
-- with at least one declaration or turndown are present.
--   declared     distinct declarations (disaster numbers) in curated.fact_declaration_area
--   turned_down  OpenFEMA DeclarationDenials records whose current status is "Turndown"
--   turndown_share = turned_down / (declared + turned_down): the share of *recorded decisions*
--                that were turndowns. It is not a denial rate of all damaging events (most
--                events never lead to a request), requests granted on appeal appear only as
--                declared, and a turndown says nothing about the damage itself.
drop table if exists serving.declaration_request_outcomes cascade;
create table serving.declaration_request_outcomes as
with declared as (
  select state_abbr, extract(year from incident_begin_date)::int as incident_year,
         declaration_type, hazard_family,
         count(distinct disaster_number)                                  as declared,
         count(distinct disaster_number) filter (where tribal_request)    as tribal_declared
  from curated.fact_declaration_area
  where is_weather and declaration_type in ('DR', 'EM')
  group by 1, 2, 3, 4
),
turned as (
  select state_abbr, incident_year, declaration_type, hazard_family,
         count(*)                                      as turned_down,
         count(*) filter (where tribal_request)        as tribal_turned_down,
         percentile_cont(0.5) within group (order by days_request_to_decision)
                                                       as median_days_request_to_turndown
  from curated.fact_declaration_denial
  where is_weather and declaration_type in ('DR', 'EM')
  group by 1, 2, 3, 4
)
select
  coalesce(d.state_abbr, t.state_abbr)                 as state_abbr,
  r.fema_region::int                                   as fema_region,
  coalesce(d.incident_year, t.incident_year)           as incident_year,
  coalesce(d.declaration_type, t.declaration_type)     as declaration_type,
  coalesce(d.hazard_family, t.hazard_family)           as hazard_family,
  coalesce(d.declared, 0)                              as declared,
  coalesce(t.turned_down, 0)                           as turned_down,
  coalesce(d.tribal_declared, 0)                       as tribal_declared,
  coalesce(t.tribal_turned_down, 0)                    as tribal_turned_down,
  coalesce(t.turned_down, 0)::numeric
    / nullif(coalesce(d.declared, 0) + coalesce(t.turned_down, 0), 0) as turndown_share,
  t.median_days_request_to_turndown
from declared d
full join turned t
  on t.state_abbr = d.state_abbr and t.incident_year = d.incident_year
 and t.declaration_type = d.declaration_type and t.hazard_family = d.hazard_family
left join staging.seed_fema_regions r on r.state_abbr = coalesce(d.state_abbr, t.state_abbr);

alter table serving.declaration_request_outcomes
  add primary key (state_abbr, incident_year, declaration_type, hazard_family);
