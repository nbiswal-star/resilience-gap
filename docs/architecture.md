# Architecture

## Layers and storage

```
 sources (HTTPS, redirect-validated)
   │
   ▼
 OBJECT STORE — system of record for raw bytes (team's shared Dropbox folder resgap-lake/;
                local data/lake/ or S3 by configuration)
   raw/noaa_storm_events/details/year=2024/StormEvents_details-ftp_v1.0_d2024_c20260728.csv.gz
   raw/nws_zone_county/bp16ap26.dbx
   raw/nws_zone_county/internet_archive/bp28de04.dbx        (SHA-1-verified archive copy)
   raw/openfema/HmaSubapplications/v2/scope=<id>/snapshot=<UTC ts>/page-00001.json.gz
   raw/bls_cpi/CUUR0000SA0/years=2020-2029/snapshot=<UTC ts>/response.json.gz
   raw/cdc_svi/release=2022/SVI_2022_US_county.csv   ·   raw/cdc_svi_pr/release=2022-puerto-rico/…
   raw/census_popest/vintage=2025/co-est2025-alldata.csv   ·   raw/census_popest_pr/vintage=2025/…
   │  load (COPY), lineage columns
   ▼
 POSTGRESQL — team AWS RDS 18.3 (local PostgreSQL 16 remains supported for development and tests)
   raw      all-TEXT tables, no constraints; openfema_records and bls_cpi_records (JSONB)
   staging  typed, parsed, de-duplicated, conformed to county FIPS; versioned zone crosswalk and
            date-effective zone resolution; CPI monthly / annual / fiscal year
   curated  dim_county (analysis universes, FEMA region, HMA geography comparability),
            dim_hazard, dim_hazard_match,
            dim_inflation, dim_hma_period, fact_storm_event_county, fact_declaration_area
            (county | statewide | tribal | unresolved), fact_hma_allocation (verbatim status,
            positive-obligation flag), fact_declaration_denial
            (PK + FK; nominal and constant dollars)
   serving  county_resilience_profile, event_declaration_coverage, declaration_timeliness,
            declaration_timeliness_summary, declaration_request_outcomes, state_year_summary,
            svi_quartile_summary, hma_county_period_activity,
            hma_county_program_hazard_activity, hma_status_summary, hma_stage_summary,
            hma_damage_burden_summary, zone_mapping_coverage, inflation_reference
   ops      pipeline_runs, source_objects (SHA-256), raw_loads, raw_rejects, dq_results,
            active_profile
   │
   ▼
 reports/resilience_gap_report.html   ·   Streamlit dashboard   ·   any BI tool
```

## Storage choices and justification (brief C.11–13)

| Data | Store | Why |
|---|---|---|
| Original downloaded files and API pages | Object store, kept in the team's **shared Dropbox folder `resgap-lake/`** (every member has edit access); `data/lake/` locally or S3 by configuration | Write-once, immutable, file-shaped artefacts. Keeping the exact bytes (gzip only, lossless) proves fidelity, allows reprocessing without re-downloading, and is the cheapest durable option. A shared cloud folder means the raw data does not live on one person's laptop: any member can browse the original files, and a run with `RESGAP_LAKE_DIR` pointed at a synced copy writes its downloads there. Keys are S3-style (`raw/<source>/<partition>/<file>`), so `RESGAP_OBJECT_STORE=s3` moves the same layout to AWS S3 without code changes |
| Semi-structured JSON (OpenFEMA) | Object store pages (system of record) **and** `raw.openfema_records` JSONB | The object store keeps each response as received; JSONB lets SQL query, validate and flatten nested and multi-valued fields where the other data already lives. We chose this over MongoDB because the analysis is relational: every JSON record ends up joined to tabular data |
| Structured CSV / pipe files | Team-hosted AWS RDS PostgreSQL `raw` (all TEXT) | Required by the brief; TEXT means nothing is rejected at load time, and problems are measured later by DQ checks |
| Cleaned, integrated, analysis-ready data | Team-hosted AWS RDS PostgreSQL `staging` / `curated` / `serving` | Typed columns, primary/foreign keys and constraints enforce integrity only after cleaning. Raw and curated data are separated by schema |
| Pipeline metadata | PostgreSQL `ops` | Provenance, idempotency state and the data-quality log can be queried next to the data they describe |

## Idempotency and provenance

* **Files** (NOAA, NWS, SVI, Census): the object key includes the source's own version (NOAA
  `cYYYYMMDD`, NWS `bpDDmonYY`, SVI release, Census vintage). Each object's SHA-256 is recorded in
  `ops.source_objects`. `ops.raw_loads` stores which checksum populates each raw partition. A
  partition is reloaded (delete + COPY in one transaction) only when the checksum changes.
* **Reuse rule**: an object already in the store is reused instead of re-downloaded only if this
  database's `ops.source_objects` records it as fetched from the same URL. Offline copies
  (`--offline-dir`, fixtures) are written under a separate `offline/` key prefix with a `file://`
  source URL, so a PA-only excerpt can never stand in for a full national file.
* **API snapshots** (OpenFEMA): pages are fetched with a stable `$orderby=id`. The snapshot hash is
  computed over sorted canonical record hashes, ignoring the envelope's `rundate`. If the hash
  matches the loaded snapshot, nothing is written. A snapshot whose record count differs from
  the API's `$count` is rejected (`IncompleteSnapshotError`).
* **Transformations** are deterministic full rebuilds; the integration test checks that the
  serving table's fingerprint is identical after a re-run. Because every staging, curated and
  serving table is dropped and re-created, a database built by an earlier version picks up
  renamed columns on its next `make transform` (e.g. `is_funded` → `has_positive_obligation`,
  `is_priority_gap` → `is_high_need_low_obligation`). The old names are deliberately not kept
  as aliases because they overstated what the data show; BI queries must use the new names.
* **Lineage**: every raw row carries `_partition_key`, `_object_key`, `_source_row`, `_run_id`,
  `_loaded_at`; every run step is in `ops.pipeline_runs`.

## Source health, redirects and fallbacks

* **Redirect validation.** Every request checks each redirect hop and the final URL against the
  source's `allowed_hosts` in `config/sources.yml` and requires HTTPS. A data URL that serves an
  HTML page is rejected. A retired download that now lands on a landing page or another host is
  therefore an explicit `SOURCE UNAVAILABLE`, never silently ingested.
* **`resgap source-check`.** Probes every source without downloading large files:
  * delimited files are streamed only to their first line (gzip decompressed incrementally);
  * APIs return one record; fallbacks are probed with HEAD.
  It reports status, redirects, content type, size, last-modified, a schema fingerprint compared
  with the registry, and whether the primary or a fallback would be used. Exit code 1 if any
  source has no usable endpoint or its schema changed.
* **Preflight.** `ingest`/`run` perform the same check per source before downloading (skippable
  with `--no-preflight`):
  * a delimited source with a changed header fails fast, because the raw loader would reject it;
  * JSON sources only warn on drift, because added fields are harmless in JSONB;
  * the BLS check uses HEAD so it does not spend one of the 25 daily keyless queries.
* **Verified fallbacks** exist only where an independent path serves identical content:
  * OpenFEMA bulk `.jsonl` exports, used automatically if API paging fails;
  * Internet Archive copies of NWS crosswalk files whose SHA-1 equals the NWS file.

  NOAA, CDC, Census and BLS have no independent keyless mirror. They fail fast, and stored objects
  are reused while their provenance is recorded.

## Date-effective geography

NWS crosswalk versions are modelled in two tables:
* `staging.nws_zone_snapshot`: one row per version, valid from its date to the day before the
  next version;
* `staging.nws_zone_county_version`: the (version, zone, county) pairs.

`staging.storm_event_zone_resolution` resolves each zone event:
1. Use the version in effect on the event date.
2. Inside a documented gap (successor more than 400 days later), require the zone name to agree.
3. Otherwise use the nearest version with the same zone code and the same zone name.
4. Otherwise leave the event unmapped, with a reason.

Each mapping records the version and its provenance tier (`nws` or `internet_archive`).
`serving.zone_mapping_coverage` exposes mapped and unmapped counts by year.

## Analysis universes

`curated.dim_county.analysis_universe` separates:
* `states_dc`: national SVI percentile and quartiles;
* `puerto_rico`: SVI ranked only within Puerto Rico, kept in separate columns;
* `island_areas`: no SVI and no Census population;
* `legacy_geography`: the 8 pre-2022 Connecticut counties.

National quartiles and the high-need, low-obligation follow-up screen are computed within
`states_dc` only; percentile ranks and the obligation median behind `is_low_obligation` are
computed within each universe (Puerto Rico against Puerto Rico). A critical check enforces the
separation.

**Geography comparability (the 2022 Connecticut break).** NOAA Storm Events, OpenFEMA disaster
declarations and OpenFEMA HMA all record 2000–2025 Connecticut history on the 8 legacy counties
(09001–09015); Census Vintage 2025 and SVI 2022 use the 9 planning regions (09110–09190), which
do not nest in them. No defensible crosswalk exists, so no legacy fact is crosswalked or
allocated to a region. `curated.dim_county` carries two flags derived from the same data-driven
rule (a current unit of a state that also has `legacy_geography` units), false today for exactly
the 9 planning regions:
* `storm_declaration_geography_comparable`: the regions' storm events, damage, casualties,
  significant-event coverage, major disasters and emergencies are NULL (not comparable), never
  zero, in `serving.county_resilience_profile` (a table constraint enforces it), as are
  per-capita damage, coverage rate and damage percentile. They are excluded from damage ranks,
  damage-burden quartiles, the SVI-quartile damage and coverage numerators and denominators
  (`svi_quartile_summary.history_comparable_population` is the per-capita denominator) and the
  follow-up screen. SVI and population are kept, so the regions stay visible as current social
  geography.
* `hma_geography_comparable`: the regions get the HMA stage `geography_not_comparable` (in the
  profile and in every period) and are excluded from HMA denominators, ranks, medians and the
  screen.

The legacy counties keep their storm, declaration and HMA records in their own universe. Their
storm and declaration records are real pre-change history and may remain in national historical
event totals; only the current-geography HMA headline shares leave them out. Two critical guards
are triggered by the fact data, independently of the flags: wherever storm/declaration facts
(`storm_declaration_geography_break_guard`) or HMA records (`hma_geography_break_guard`) land on
legacy geography, no current unit of that state may carry ordinary historical metrics, ranks or
HMA stages. The recount checks (`history_summaries_reconcile`, `high_need_low_obligation_flag_exact`,
`hma_damage_burden_reconciles`, `serving_damage_reconciles`) re-derive storm-history
comparability from `analysis_universe` and state instead of reading the stored flag.

## HMA application, status and obligation funnel

OpenFEMA `HmaSubapplications` holds subapplications that **reached FEMA**; proposals that a state
screened out, or that a community never prepared, are not in the source. The model therefore
keeps four things apart and never derives one from another:

| Concept | Where | Rule |
|---|---|---|
| Benefiting area | `staging.hma_county_bridge` → `curated.fact_hma_allocation.area_level` | free-text county list resolved to county FIPS, or kept as statewide / out_of_state / unresolved / unspecified at state level |
| Status | `fact_hma_allocation.status` | OpenFEMA `status`, verbatim; a snapshot, never recoded (critical `hma_raw_status_preserved`) |
| Recorded obligation | `fact_hma_allocation.obligated_usd`, `obligation_amount_class`, `has_positive_obligation` | share of `totalObligatedAmount`; the positive flag is the amount > 0, independent of status (critical `hma_positive_obligation_matches_source`) |
| Period | `curated.dim_hma_period`, `fact_hma_allocation.period_start_fy` | fixed five-year federal fiscal-year periods FY2001–2005 … FY2021–2025 (FY2000 in FY1996–2000), clipped to the profile (critical `hma_period_boundaries`) |

Serving exposes the stages `not_observed` → `observed_no_positive_obligation` → `obligated` (plus
`geography_not_comparable` for the CT planning regions, outside every rate) at
grains where absence is meaningful: county × all profile fiscal years
(`county_resilience_profile`) and county × five-year period (`hma_county_period_activity`, a dense
grid). Program × hazard-family detail (`hma_county_program_hazard_activity`) is sparse: it lists
observed combinations only, because programs existed in different years and a subapplication
records one primary hazard. `hma_status_summary` counts every in-scope subapplication once by
verbatim status (county-mapped or state-level only), and `hma_stage_summary` gives the stage
distribution per analysis universe, SVI group and period. `hma_damage_burden_summary` aligns
stages and recorded obligation with quartiles of constant-dollar damage per resident (within each
universe, over units with comparable storm history; HMA-comparable units only). Because each subapplication has one fiscal year, one program
and one primary hazard, every layer adds up exactly; critical checks enforce stage exclusivity,
the complete grid and all count and amount reconciliations, and recount the stage and
damage-burden summaries from the curated fact. `state_year_summary` covers applicants that are
states, DC or territories only; subapplications without an applicant state (or from FM) are
counted by `state_year_hma_out_of_scope` and reconciled by `state_year_hma_reconciles`.
DeclarationDenials (turned-down presidential declaration requests) are a separate,
government-level dataset and never enter these tables.

## Declared areas, hazards and regions

`staging.fema_area_level` classifies each OpenFEMA declared area:
* `statewide` only when `designatedArea` is literally "Statewide" (20 rows in 2000–2025);
* `tribal` for other `fipsCountyCode = '000'` areas identified by a `placeCode` or a tribal
  request: reservations, tribal statistical areas and Alaska Native villages (1,102 rows). They
  keep their FEMA name and are never expanded to the counties of the state;
* `county` when state + county FIPS is a known county; `unresolved` otherwise.

Only `county` rows carry a `county_fips` (a table constraint and a critical check enforce it).
Q2 matches a storm event to a declaration only through a county or literal statewide designation
and only when `curated.dim_hazard_match` (from `seeds/hazard_match_rules.csv`) lists the pair of
hazard families. `curated.dim_county.fema_region` comes from `seeds/fema_regions.csv`, which a
critical check compares with the `region` field of every declaration and denial record.

## Constant dollars

`curated.dim_inflation` holds a factor per calendar year (storm damage, by event year) and per
federal fiscal year (HMA, October–September):

`factor = CPI-U(base year annual average) / CPI-U(period)`

The base year is set by `analysis.inflation.base_year` in `config/profiles.yml` (default 2025).
Facts carry both nominal and `*_real_usd` columns, and a critical check reconciles them.

## Execution

`resgap run` = `init-db` → `ingest` (all sources) → DQ(raw) → `transform` (staging → curated →
serving, one transaction per SQL file) → DQ(staging, curated, serving) → `report`. A critical DQ
failure stops the run with exit code 2; a database error exits with 3. The step and message are
recorded in `ops.pipeline_runs`.

Measured on a laptop (Apple Silicon, PostgreSQL 16, 2026-11-08). Ingest times depend on the
network; the archive crosswalk files are fetched at ≥ 1 s spacing.

| Profile | Download | Ingest (first run) | Transform | Raw storm rows | DB size |
|---|---|---|---|---|---|
| `fixture` (offline) | none | < 1 s | < 1 s | 789 | small |
| `sample` | 60 MB (58 objects) | 2.5–3 min end to end on a clean lake (149 s and 184 s measured; 2 min 22 s of the latter was the 47 crosswalk versions); 51 s re-run with unchanged sources | ~9 s | 215,281 | 0.49 GB |
| `full` | 299 MB (NOAA 258 MB, crosswalks 20 MB, OpenFEMA 17 MB) | NOAA + OpenFEMA 97 s; crosswalk versions, Puerto Rico and CPI sources 3 min 13 s | ~62 s (+ ~17 s for the 75 DQ checks), measured on the current code with the storm/declaration geography break (no-download rebuild, 2026-11-08: transform 13:46:40 → 13:47:42, DQ 13:47:46 → 13:48:03); historical: ~58 s + ~17 s for 71 checks with the HMA-only Connecticut fix | 1,592,849 | 3.3 GB |

BLS's keyless API allows 25 queries a day; a full run uses 4 (one per decade, 1990s–2020s), the
sample and fixture profiles 1.
