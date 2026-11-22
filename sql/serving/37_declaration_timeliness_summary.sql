-- Q4 breakdowns. Grain: one breakdown x group / (breakdown, group_label).
--   breakdown  'all' | 'svi_group' | 'hazard_family' | 'fema_region'
-- Descriptive medians and quartiles of FEMA lags for weather-related Major Disasters
-- (serving.declaration_timeliness). These are one-way breakdowns: no adjustment is made for the
-- other dimensions, so a difference between SVI groups can reflect their hazard mix, region or
-- year (top_hazard_family / top_hazard_share make the hazard mix visible). sample_note flags
-- groups too small to compare.
-- turned_down: weather-related Major Disaster requests recorded as "Turndown" for the same
-- hazard family or region (NULL for SVI groups: denial records have no county).
drop table if exists serving.declaration_timeliness_summary cascade;
create table serving.declaration_timeliness_summary as
with t as (
  select dt.*, g.breakdown, g.group_label, g.sort_key
  from serving.declaration_timeliness dt
  cross join lateral (values
    ('all', 'All weather-related major disasters', 0),
    ('svi_group', dt.svi_group,
       case dt.svi_group when 'Q1 (least vulnerable)' then 1 when 'Q2' then 2 when 'Q3' then 3
                         when 'Q4 (most vulnerable)' then 4
                         when 'Puerto Rico (SVI ranked within PR)' then 5
                         when 'Island areas (no SVI published)' then 6
                         when 'No county designation' then 7 else 8 end),
    ('hazard_family', dt.hazard_family, 0),
    ('fema_region', 'Region ' || coalesce(dt.fema_region::text, '?'), coalesce(dt.fema_region, 99))
  ) as g(breakdown, group_label, sort_key)
),
stats as (
  select breakdown, group_label, min(sort_key) as sort_key,
         count(*)                                                        as disasters,
         percentile_cont(0.25) within group (order by days_incident_to_declaration)
                                                                         as p25_days_incident_to_declaration,
         percentile_cont(0.5) within group (order by days_incident_to_declaration)
                                                                         as median_days_incident_to_declaration,
         percentile_cont(0.75) within group (order by days_incident_to_declaration)
                                                                         as p75_days_incident_to_declaration,
         count(days_request_to_declaration)                              as disasters_with_request_date,
         percentile_cont(0.5) within group (order by days_request_to_declaration)
                                                                         as median_days_request_to_declaration,
         percentile_cont(0.5) within group (order by days_incident_to_request)
                                                                         as median_days_incident_to_request,
         min(incident_year)                                              as first_incident_year,
         max(incident_year)                                              as last_incident_year
  from t group by breakdown, group_label
),
hazard_mix as (
  select distinct on (breakdown, group_label) breakdown, group_label,
         hazard_family as top_hazard_family,
         count(*)::numeric / sum(count(*)) over (partition by breakdown, group_label)
                                                                         as top_hazard_share
  from t group by breakdown, group_label, hazard_family
  order by breakdown, group_label, count(*) desc, hazard_family
),
turndowns as (
  select 'all' as breakdown, 'All weather-related major disasters' as group_label,
         count(*) as turned_down
  from curated.fact_declaration_denial where is_weather and declaration_type = 'DR'
  union all
  select 'hazard_family', hazard_family, count(*)
  from curated.fact_declaration_denial where is_weather and declaration_type = 'DR'
  group by hazard_family
  union all
  select 'fema_region', 'Region ' || coalesce(fema_region::text, '?'), count(*)
  from curated.fact_declaration_denial where is_weather and declaration_type = 'DR'
  group by fema_region
)
select s.breakdown, s.group_label, s.sort_key, s.disasters,
  s.p25_days_incident_to_declaration, s.median_days_incident_to_declaration,
  s.p75_days_incident_to_declaration, s.disasters_with_request_date,
  s.median_days_request_to_declaration, s.median_days_incident_to_request,
  s.first_incident_year, s.last_incident_year,
  h.top_hazard_family, h.top_hazard_share,
  case when s.breakdown = 'svi_group' then null else coalesce(td.turned_down, 0) end
                                                                         as turned_down,
  case when s.breakdown = 'svi_group' then null
       else coalesce(td.turned_down, 0)::numeric / nullif(s.disasters + coalesce(td.turned_down, 0), 0)
  end                                                                    as turndown_share,
  case when s.disasters < 10 then 'fewer than 10 disasters: do not compare'
       when s.disasters < 30 then '10-29 disasters: indicative only'
       else '30 or more disasters' end                                   as sample_note
from stats s
join hazard_mix h using (breakdown, group_label)
left join turndowns td using (breakdown, group_label);

alter table serving.declaration_timeliness_summary add primary key (breakdown, group_label);
