# Runbook

## Everyday commands

| Goal | Command |
|---|---|
| First-time setup | `make setup && make up` |
| Offline end-to-end check (no network) | `make offline-demo` |
| Live run, default sample profile | `make run` |
| Live run, national 2000–2025 (~300 MB download, 3.3 GB DB, 4 BLS queries) | `make run PROFILE=full` (or `uv run resgap --profile full run`) |
| Re-run one source | `uv run resgap ingest openfema` (names: `census_county_reference census_popest cdc_svi nws_zone_county noaa_storm_events openfema`) |
| Force re-download and reload | `uv run resgap ingest noaa_storm_events --force` |
| Rebuild tables after changing SQL | `make transform && make dq && make report` |
| Rebuild the national tables after a model change, without downloading | `uv run resgap --profile full transform && uv run resgap dq && uv run resgap report` against the database that already holds the 2000–2025 raw data |
| What happened? | `make status`, or `select * from ops.pipeline_runs order by started_at desc;` |
| Are the sources healthy? | `make source-check` (no database, no large downloads; writes `reports/source_check.json`) |
| Skip the preflight (e.g. offline, or BLS daily limit reached) | `uv run resgap ingest --no-preflight` |
| Restate dollars in another base year | set `analysis.inflation.base_year` in `config/profiles.yml`, then `make transform` |
| Tests | `make test` (creates/uses `resgap_test`; never point `RESGAP_TEST_DATABASE_URL` at real data — the test session refuses to start if it names the same database as `RESGAP_DATABASE_URL`) |

## Exit codes
`0` success · `1` database unreachable, or ingestion failed (the log line
`SOURCE UNAVAILABLE: <source>` names the source) · `2` critical data-quality failure (see
`ops.dq_results`) or invalid command-line usage · `3` a SQL step failed (see
`ops.pipeline_runs.message`).

## When a source is unavailable

Before downloading, each source passes a preflight (`resgap source-check` logic):
* a redirect to an unexpected host, an HTML page instead of data, or a changed header stops that
  source with `SOURCE UNAVAILABLE`;
* JSON sources only warn on added fields.

The run fails loudly for that source and continues with the others, so the cause is isolated.
Nothing already loaded is replaced: raw partitions change only after a complete, verified
download.

| Source | Verified fallback |
|---|---|
| NOAA bulk directory | Files already in `data/lake/raw/noaa_storm_events/` are reused automatically while this database still holds their provenance records (`ops.source_objects`). If the directory is down, keep working on the lake copy and note the retrieval date; for a new machine, copy `data/lake` from a teammate (or shared drive) and run with `--offline-dir` pointing at a folder laid out like `tests/fixtures/` |
| OpenFEMA API | **Automatic**: if API paging fails, the pipeline downloads `<Dataset>.jsonl` from the same host, keeps it in the object store, filters it with the same scope rules and records `via bulk_export_fallback` in `ops.pipeline_runs`. Manual alternative: the catalog lists bulk files per dataset (`https://www.fema.gov/api/open/<version>/<Dataset>.<format>`, formats csv/json/jsona/jsonl/parquet). Save each of the three files as `<dir>/openfema/<Dataset>/page-00001.json` (the bulk JSON is `{"<Dataset>": [...]}`, which the offline loader accepts; tested 2026-11-08 with `DeclarationDenials.json`, 1,303 records) and run `resgap ingest openfema --offline-dir <dir>` |
| NWS zone files | Automatic: if an NWS-hosted version fails, a registered Internet Archive capture with the **same SHA-1** is used (`bp05mr24`, `bp18mr25`, `bp16ap26`). Pre-2020 versions come only from SHA-1-verified archive captures; an unavailable non-linked version is a warning and its coverage loss shows in `zone_events_unmapped` |
| BLS CPI | No keyless fallback. `REQUEST_NOT_PROCESSED` usually means the 25-query daily limit was reached: wait until the next day. Stored responses are reused, and unchanged decades are not re-requested |
| CDC SVI | The file is versioned (2022 release); the lake copy is sufficient. Landing page lists alternative years |
| Census files | Lake copy; Census FTP2 archives older vintages |
| Any (demo day) | `make offline-demo` runs the whole pipeline on committed real excerpts |

## Common problems

| Symptom | Fix |
|---|---|
| `Cannot connect to PostgreSQL` | `make up`; check Docker Desktop is running; or set `RESGAP_DATABASE_URL` to a local PostgreSQL (16 tested) |
| Port 5433 in use | change the host port in `docker-compose.yml` (`127.0.0.1:<port>:5432`) and `RESGAP_DATABASE_URL` |
| Need the database from another machine | Compose publishes PostgreSQL and SeaweedFS on 127.0.0.1 only, on purpose; use an SSH tunnel rather than widening the binding |
| DQ `declarations_code000_unclassified` warns | OpenFEMA published a code-`000` area that is neither "Statewide" nor identifiable as tribal; inspect the sample, then extend `staging.fema_area_level` (it stays `unresolved`, never statewide) |
| DQ `fema_region_matches_source` fails | a declaration or denial carries a region that differs from `seeds/fema_regions.csv`; confirm on fema.gov and update the seed |
| `schema drift at <source>` (preflight) or `SchemaDriftError` | the agency changed a file header. Run `resgap source-check <source>` to see the fingerprint; Inspect the new file in `data/lake`, update the data contract and the affected staging SQL, then drop the raw table (`drop table raw.<name>`) and re-ingest |
| `IncompleteSnapshotError` (OpenFEMA) | the API returned fewer records than it counted (often mid-refresh). Re-run later; the previous snapshot stays loaded |
| DQ `fail` | read `sample` in `ops.dq_results`, decide (fix, exclude, or document), record the decision in `docs/data-quality.md` |
| DQ `hma_raw_status_preserved` fails | a model recoded an OpenFEMA HMA `status`. Statuses must stay verbatim; remove the recoding (derive anything new in a separate column, never by overwriting `status`) |
| DQ `hma_stage_exclusive_exhaustive`, `hma_period_grid_complete` or a `hma_*_reconciles` check fails | a serving model and the curated fact disagree (e.g. a filter added to one table only). Compare the sample with `curated.fact_hma_allocation`; all HMA serving tables must use county-mapped rows and `has_positive_obligation` the same way |
| DQ `high_need_low_obligation_flag_exact` fails | the follow-up screen in `sql/serving/31_county_resilience_profile.sql` no longer matches its documented rule (states + DC, units with comparable storm history and HMA geography, damage percent rank ≥ 0.75 over states + DC units with a population and comparable storm history, SVI Q4, recorded obligation per resident ≤ median of HMA-comparable states + DC units), or `is_low_obligation` no longer uses each universe's own median; restore the rule or update the rule, the check and the docs together |
| DQ `hma_geography_break_guard` fails | HMA records land on legacy geography (today the 8 pre-2022 CT counties) while current units of that state have an ordinary HMA stage. Check `curated.dim_county.hma_geography_comparable` (must be false for the current units of a state that also has `legacy_geography` units) and the stage expression in `31_` / `38_`. Never fix it by mapping legacy amounts to current units without a documented crosswalk decision |
| DQ `storm_declaration_geography_break_guard` fails | storm-event or declaration facts land on legacy geography (today the 8 pre-2022 CT counties) while a current unit of that state is flagged comparable or carries non-NULL storm/declaration history, a damage rank, `is_high_loss`, a screen result or same-year period damage. Check `curated.dim_county.storm_declaration_geography_comparable` (false for current units of a state that also has `legacy_geography` units) and the `case when ... storm_declaration_geography_comparable` expressions in `31_` / `38_`. Never replace NULL with 0 and never crosswalk or allocate legacy facts to current units without a documented decision |
| DQ `history_summaries_reconcile` fails | a Q1/Q2 column of `serving.svi_quartile_summary` or the all-period storm damage of `serving.hma_stage_summary` includes a unit without comparable storm history in a numerator or in `history_comparable_population` (or drops a comparable one). Compare the sample with `curated.fact_storm_event_county` and `serving.event_declaration_coverage`; every damage or coverage aggregate must filter on `storm_declaration_geography_comparable` |
| DQ `history_facts_on_noncomparable_geography` warns | a storm-event or declaration fact already resolved to a CT planning region (e.g. a recent zone crosswalk version or declaration naming the new geography). Those rows stay in the curated facts, state-level tables and `serving.event_declaration_coverage`, are withheld from the county profile (partial post-change history is not comparable with a 2000–2025 total), and `serving_damage_reconciles` accounts for the withheld damage. Review the sample and record a decision in `docs/data-quality.md` |
| DQ `hma_records_on_noncomparable_geography` warns | a `benefitingCounties` entry resolved to a CT planning region. Such amounts are counted under `geography_not_comparable` and in no HMA rate; review the sample and record a decision in `docs/data-quality.md` |
| DQ `state_year_hma_reconciles` fails | `serving.state_year_summary` dropped or duplicated HMA records beyond the documented exclusion (no applicant state, applicant not in `curated.dim_county`); compare with the sample of `state_year_hma_out_of_scope` |
| A BI query or notebook fails after upgrading with a missing column (`is_funded`, `hma_funded_subapplications`, `is_priority_gap`, `is_low_mitigation`, `priority_gap_counties`, `hma_subapplications`) | the columns were renamed on purpose: use `has_positive_obligation`, `hma_positive_obligation_subapplications`, `is_high_need_low_obligation`, `is_low_obligation`, `high_need_low_obligation_counties`, `hma_observed_subapplications`. `make transform` rebuilds every table with the new names |

## Switching the object store to S3

```bash
uv sync --extra dev --extra s3
# local S3-compatible store:
docker compose --profile s3 up -d s3
# .env
RESGAP_OBJECT_STORE=s3
RESGAP_S3_BUCKET=resgap-lake
RESGAP_S3_ENDPOINT_URL=http://localhost:8333      # empty for AWS S3
AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  # any value for local SeaweedFS
```
Create the bucket once (`aws --endpoint-url http://localhost:8333 s3 mb s3://resgap-lake` or the
AWS console). Keys and code paths are identical to the local store (covered by
`tests/unit/test_s3_objectstore.py`).

## Resetting

* Rebuild derived tables only: `make transform`.
* Start the database from scratch: `docker compose down -v` (deletes the volume), then `make up`
  and `make run`. Files are downloaded again: a stored object is only reused when the database
  has a provenance record proving it came from the same URL (this prevents offline excerpts from
  masquerading as full downloads). Existing objects at the same keys are overwritten.
* Delete downloaded files: `make clean-lake`. Remove caches and the generated report: `make clean`.
