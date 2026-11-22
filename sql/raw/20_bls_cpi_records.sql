-- Landing table for BLS Public Data API responses: one row per data point, stored unmodified.
create table if not exists raw.bls_cpi_records (
  series_id      text not null,
  partition_key  text not null,
  record         jsonb not null,
  record_sha256  text not null,
  _object_key    text not null,
  _run_id        uuid not null,
  _loaded_at     timestamptz not null default now()
);
create index if not exists bls_cpi_records_part_idx on raw.bls_cpi_records (partition_key);
