-- Landing table for OpenFEMA JSON records. One row per API record, stored unmodified as JSONB.
-- No primary key or constraints on content: raw keeps every record, even problematic ones.
-- (Delimited-file raw tables are created by the loader from each file's header; see rawload.py.)
create table if not exists raw.openfema_records (
  dataset        text not null,
  version        text not null,
  scope_key      text not null,
  record_id      text,
  record         jsonb not null,
  record_sha256  text not null,
  _object_key    text not null,
  _page          integer not null,
  _run_id        uuid not null,
  _loaded_at     timestamptz not null default now()
);
create index if not exists openfema_records_ds_idx on raw.openfema_records (dataset, scope_key);
