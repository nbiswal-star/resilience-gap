-- Layered schemas. raw = faithful copies; staging = typed/cleaned/conformed;
-- curated = integrated facts & dimensions; serving = analysis-ready tables; ops = pipeline metadata.
create schema if not exists raw;
create schema if not exists staging;
create schema if not exists curated;
create schema if not exists serving;
create schema if not exists ops;

create table if not exists ops.pipeline_runs (
  run_id        uuid primary key,
  step          text not null,
  profile       text,
  status        text not null check (status in ('running','succeeded','unchanged','failed')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  rows_affected bigint,
  params        jsonb,
  message       text
);
create index if not exists pipeline_runs_step_idx on ops.pipeline_runs (step, started_at desc);

-- Every object written to the object store (raw files, API pages) with its checksum.
create table if not exists ops.source_objects (
  object_key         text primary key,
  object_uri         text not null,
  source             text not null,
  partition_key      text not null,
  source_url         text not null,
  sha256             char(64) not null,
  size_bytes         bigint,
  content_type       text,
  http_etag          text,
  http_last_modified text,
  retrieved_at       timestamptz not null default now(),
  run_id             uuid references ops.pipeline_runs (run_id)
);

-- Which object version currently populates each raw-table partition (drives idempotency).
create table if not exists ops.raw_loads (
  raw_table      text not null,
  partition_key  text not null,
  source         text not null,
  object_keys    text[] not null,
  content_sha256 text not null,
  rows_loaded    bigint not null,
  rows_rejected  bigint not null,
  run_id         uuid references ops.pipeline_runs (run_id),
  loaded_at      timestamptz not null default now(),
  primary key (raw_table, partition_key)
);

-- Source rows that could not be stored in the raw table shape (never silently dropped).
create table if not exists ops.raw_rejects (
  id          bigserial primary key,
  run_id      uuid,
  object_key  text not null,
  source_row  integer not null,
  reason      text not null,
  raw_line    text,
  rejected_at timestamptz not null default now()
);

-- Data-quality results: one row per check per DQ run.
create table if not exists ops.dq_results (
  id             bigserial primary key,
  dq_run_id      uuid not null,
  check_name     text not null,
  layer          text not null,
  severity       text not null check (severity in ('critical','warn','info')),
  status         text not null check (status in ('pass','warn','fail','error')),
  failing_rows   numeric,
  total_rows     numeric,
  fail_ratio     numeric,
  max_fail_ratio numeric,
  description    text,
  handling       text,
  sample         jsonb,
  checked_at     timestamptz not null default now()
);
create index if not exists dq_results_run_idx on ops.dq_results (dq_run_id);

-- Parameters of the profile used by the most recent transform (read by SQL models).
create table if not exists ops.active_profile (
  singleton               boolean primary key default true check (singleton),
  profile                 text not null,
  start_year              int not null,
  end_year                int not null,
  states                  text[],          -- null = all states
  significant_damage_real_usd numeric not null,  -- constant base-year dollars
  significant_casualties  int not null,
  match_days_before       int not null,
  match_days_after        int not null,
  open_incident_cap_days  int not null,
  set_at                  timestamptz not null default now()
);

-- Columns added after the first release (idempotent upgrades of existing databases).
alter table ops.active_profile add column if not exists cpi_base_year int;
alter table ops.active_profile add column if not exists cpi_series_id text;
alter table ops.active_profile add column if not exists zone_gap_threshold_days int;
-- The significance threshold is applied to constant base-year dollars; older databases stored it
-- under a name that suggested nominal dollars.
do $$
begin
  if exists (select 1 from information_schema.columns where table_schema = 'ops'
             and table_name = 'active_profile' and column_name = 'significant_damage_usd') then
    alter table ops.active_profile rename column significant_damage_usd
      to significant_damage_real_usd;
  end if;
end $$;
