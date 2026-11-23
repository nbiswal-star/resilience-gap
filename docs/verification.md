# Verification log — 2026-11-08

Sections are in chronological order. **The last section,
["Storm and declaration geography break"](#current-storm-and-declaration-geography-break-executed-2026-11-08),
describes the current code (75 DQ checks, 175 tests) and its executed results of 2026-11-08.**
Earlier sections are **historical**: they record the state at that time (test counts, DQ check
counts, Docker availability), and their numbers are superseded where a later section gives newer
ones. In particular, the 166-test and 71-check results of the "Connecticut geography break and
final review" section were measured on the HMA-only fix, before the storm/declaration break, and
the 155-test and 64-check results of the HMA funnel section are intermediate evidence from before
any Connecticut fix. The follow-up screen count of 84 in those sections is historical; the
current count is 83.

## Historical: first build — commands and results

Environment at that time: macOS (Apple Silicon), Python 3.12 via uv 0.11, PostgreSQL 16.2 on
`localhost:5433`. Docker Desktop could not be started on the build machine during this first
session, so PostgreSQL ran natively on the same port the Compose file publishes, and
`docker compose config` validated the Compose file. (Docker was exercised later; see below.)

| # | Command | Result |
|---|---|---|
| 1 | `make lint` | `All checks passed!` |
| 2 | `make test` (`RESGAP_TEST_DATABASE_URL=…/resgap_test`) | **56 passed** (unit: config, DQ evaluation, NOAA listing parser, NWS link parser, header sanitising, OpenFEMA scope/paging/hashing, local and S3 object stores via `moto`; integration: SQL helper functions, offline end-to-end pipeline, raw fidelity, checksums/lineage, reconciliation, DR-4815-PA match, idempotency) |
| 3 | `make offline-demo` on an empty database | Full pipeline on real excerpts; report written; 0 critical failures |
| 4 | `make run` (sample, live) on an empty database + empty object store | 14 files + 3 API snapshots downloaded; 215,281 storm rows; all 30 DQ checks pass; report written |
| 5 | `make run` again | every raw partition and API snapshot reported `unchanged`; serving fingerprint identical (also asserted by `test_rerun_is_idempotent`) |
| 6 | `make offline-demo` then `make run` on the same database | Live run reloads full files because checksums differ; no warnings (regression check for the bug below) |
| 7 | `resgap --profile full ingest` / `transform` / `dq` | 97 s ingest (26 NOAA files, 257 MB), ~33 s transform; 1,592,849 storm events; 48,963 declaration areas; 54,638 HMA subapplications; 352 denials; DB 3.0 GB; all 30 checks pass after the decisions recorded in `data-quality.md` |
| 8 | `docker compose config -q` and with `--profile s3 --profile app` | valid |
| 9 | `uv run --extra dev python scripts/render_proposal.py` | `milestone1-proposal.pdf (2 pages)` |
| 10 | Streamlit `AppTest` on `dashboard/app.py` | no exceptions; 4 metrics; 453-row county table (sample) |
| 11 | `resgap ingest openfema --offline-dir` with FEMA's bulk `DeclarationDenials.json` | 1,303 records loaded (bulk-file fallback works) |

## Historical: defects found by verification and fixed (first build)

| Found by | Defect | Fix |
|---|---|---|
| First live NWS ingest | The page text names a retired file (`bp01my18.dbx - now invalid`); text matching tried to download it (404) | Discover only `href` links; archived versions listed explicitly |
| DQ `storm_county_fips_not_in_reference` | Storm Events territory codes (PR = 99 …) differ from Census | `seeds/storm_events_state_codes.csv` |
| Transform log (`ops.pipeline_runs`) | Duplicate key in the zone crosswalk union (`LA152` zone attribute changed between versions) | De-duplicate on (`state_zone`, `county_fips`), keep latest attributes; CLI now prints database failures clearly (exit 3) |
| Full-profile DQ (critical) | 52 negative HMA obligations stopped the run | Investigated; reclassified as documented warning, excluded from funded totals |
| Full-profile DQ (warn) | 18 % unresolved HMA names | Split into `out_of_state` (multi-state subapplications) and true unknowns (0.7 %); USVI alias seed |
| Integration test | Assumed Lycoming County had Debby events in NOAA; it does not | Test asserts the verified matches (Wayne, Wyoming, Susquehanna) |
| Clean-room smoke test | Offline excerpts and live downloads shared object keys, so a live run after the offline demo reused PA-only excerpts | Offline copies under `offline/`; reuse requires the same source URL; test added |

## Historical: README audit — fresh-clone walkthrough (2026-11-08)

The committable files (`git ls-files --others --exclude-standard`: 95 files, 1.4 MB, no `.env`,
`data/` or `.venv`) were copied to an empty directory and the README was followed literally,
using the no-Docker path (§6.1) with a new **non-superuser** role and two new databases.

| Step (README) | Result |
|---|---|
| `make setup` | `.venv` created with Python 3.12.12 (picked from `.python-version`) |
| `make env`, edit `RESGAP_DATABASE_URL` | `.env` created |
| `make offline-demo` | report written, exit 0 |
| `make test TEST_DB=…` | `56 passed` |
| `make run` (live sample, right after the offline demo) | exit 0, 30/30 DQ checks pass, no warnings; 14 s |
| `make run` again | all 17 ingest steps `unchanged` |
| `make status`, `make dq`, `make report` | serving tables populated (453 counties); `{'pass': 30}`; report written |
| `uv run resgap ingest openfema` | 3 datasets `content unchanged` |
| `make transform PROFILE=fixture` then `make report`; back to `make transform` | report header follows the last transform (fixture → sample) |
| `make lint` / `make test-unit` | `All checks passed!` / `25 passed` |
| `pytest` without `RESGAP_TEST_DATABASE_URL` | `25 passed, 31 skipped` |
| `make proposal` | `milestone1-proposal.pdf (2 pages)` |
| `make fixtures` (network) | rebuilt; only OpenFEMA `metadata.rundate` differs from the committed fixtures |
| Dashboard (`streamlit.testing` AppTest) | no exceptions; 4 metrics |
| `make clean` | caches and generated report removed; `reports/.gitkeep` kept |
| `docker compose config -q` (all profiles), `make -n up psql down` | valid / expected commands (Docker daemon not available on this machine) |
| Unreachable database | clear message, exit 1 |
| Link and command check | every `make` target named in README/docs exists; CLI source names match; no broken relative links or README anchors |

Changes made during this audit:
* `.python-version` pins Python 3.12.
* `--profile` is accepted before or after the command.
* `make dq` and `make report` no longer pass a profile they ignore.
* New Make targets: `proposal`, `clean`, `clean-lake`.
* `dashboard/` is linted locally and in CI.
* CI uses `--extra dev` consistently.
* Corrected statements:
  * a database reset re-downloads files;
  * only PostgreSQL 16 is tested;
  * exit codes;
  * the NWS landing-page failure mode;
  * the pytest marker text.
* Added `tests/unit/test_cli.py` (profile flag position; CLI source list = registry). The suite is
  now **61 tests**: `make test` → `61 passed` (as superuser and as the non-superuser owner);
  `make test-unit` → `30 passed`; without a test database → `30 passed, 31 skipped`. The rows
  above record the counts at the time they ran (56 / 25).

## Historical: source fixes — date-effective geography, CPI, territories, source resilience (2026-11-08)

Counts in this section (40 DQ checks, 90 tests) were current until the next section.

### Live endpoints verified (`make source-check`, 11/11 usable, every fingerprint matching, 0 redirects)

| Source | Primary (status) | Fallback (status) | Last-Modified |
|---|---|---|---|
| NOAA Storm Events | listing + `StormEvents_details-ftp_v1.0_d2026_c20261026.csv.gz` (200, 51 cols) | none independent | 2026-10-26 |
| NWS crosswalk | landing + `bp16ap26.dbx` (200, 11 fields) | archive capture `20260519150834` of `bp16ap26.dbx` (200) | 2026-03-03 |
| OpenFEMA ×3 | API `$top=1` (200; 70,599 / 63,635 / 1,303 records) | `.jsonl` bulk exports (HEAD 200, `application/jsonl+json`) | n/a |
| CDC SVI U.S. / PR | `SVI_2022_US_county.csv` / `PuertoRico_county.csv` (200, 158 cols) | none | 2024-11-25 |
| Census county / PR / reference | `co-est2025-alldata.csv` / `cc-est2025-agesex-72.csv` / `national_county2020.txt` (200) | none | 2026-03-26 / 2026-06-25 / 2023-02-13 |
| BLS CPI-U | API v1 `CUUR0000SA0` (200; latest 2026-M09 = 336.940) | none keyless (flat files 403; v2 needs a key) | n/a |

Research checks behind the registry:
* **NWS versions.** All 41 candidate names were HEAD-checked on NWS: 12 return 200, and every
  pre-2020 name returns 404.
* **Archive digests.** On 5 files, the archive's CDX digest equals the SHA-1 of the downloaded
  bytes. One capture (`bp03oc08`) resolves only without `:80` in the original URL.
* **OpenFEMA bulk exports.** Record keys and values equal the API's (checked record by record).
* **Puerto Rico SVI.** CDC's documentation PDF states the separate PR ranking, and the download
  tool lists Puerto Rico as its only territory.
* **Puerto Rico population.** The Census layout PDF defines the `YEAR` key.
* **BLS.** The terms, API limits and the series title were read on bls.gov. October 2025 is marked
  "Data unavailable due to the 2025 lapse in appropriations".
* **Sources not used.** Re-checked on this date; the evidence and decisions are in the decision record.

### Runs

| Run | Result |
|---|---|
| Clean room: new database + empty object store, `make run` (sample, live) | exit 0 in **149 s** (47 crosswalk versions incl. 35 archive files; 9 sources); 40/40 DQ checks pass, no warnings; DB 487 MB, lake 60 MB |
| Same, `make run` again | exit 0 in **51 s**; all 59 ingest steps `unchanged` |
| National backfill (`--profile full`) | new sources + crosswalks 3 min 13 s; transform ~47 s; DQ 40/40 pass (after fixing one flawed check, below); DB 3.3 GB |
| `make offline-demo` (fixture: PA + PR + VI) | report written, no critical failures |
| `make test` | **90 passed** (52 unit + 38 integration) |
| `make lint` | All checks passed |
| `make proposal` | 2 pages |
| Dashboard (Streamlit AppTest) | no exceptions |

### Docker, PostgreSQL and live S3 verification

After the source-remediation work, Docker Desktop was restarted cleanly and the optional services
were exercised:

| Check | Result |
|---|---|
| `make up` | Pulled `postgres:16-alpine`; `resgap-db-1` became healthy on host port 5433 |
| `docker compose exec -T db pg_isready -U resgap -d resgap` | accepting connections |
| `make test` against the container | **90 passed in 4.24 s** |
| `make offline-demo` against the container | report written; all **40/40** DQ checks passed |
| `make source-check` | all **11/11** source contracts usable; fingerprints matched |
| `make proposal` | PDF regenerated; **2 pages** |
| `docker compose --profile s3 up -d s3` | SeaweedFS 4.52 started on port 8333 |
| Fixture pipeline with `RESGAP_OBJECT_STORE=s3` | completed; 40/40 DQ checks passed; bucket contained 10 raw objects |
| `docker compose --profile app build pipeline` | `resgap-pipeline:latest` built successfully |
| `docker compose --profile app run --rm pipeline resgap status` | connected to the Compose database and listed all pipeline layers |

AWS S3 remains untested because it requires external account credentials. GitHub Actions run
`42492181411` completed successfully in 42 seconds after the first push. It ran lint, all 90 tests,
the offline end-to-end pipeline, and the report artifact upload against PostgreSQL 16.

### Before / after (national, 2000–2025)

| Measure | Before | After |
|---|---|---|
| Crosswalk versions | 8 (2022–2026), union, not date-aware | 47 (2004–2026), date-effective, 12 NWS + 35 archive |
| Land events with no county | 40,785 (2.64 %) | **19,489 (1.26 %)**, each with a reason |
| Events placed that were unmappable before | — | 34,563 events, $64.8 B nominal (e.g. 2005 storm surge $22.0 B) |
| Recorded damage (weather, in scope) | $513.4 B nominal | $574.9 B nominal = **$818.0 B in 2025 dollars** |
| HMA obligated (funded; all areas incl. statewide and out-of-state) | nominal only | $22.82 B nominal = $31.45 B in 2025 dollars (county-attributed: $18.75 B = $25.74 B) |
| Puerto Rico | no SVI, no population, quartile "No SVI" | 78 municipios with Census population and CDC within-PR SVI; separate universe |
| Island areas | "No SVI" | `island_areas` universe; no SVI/per-capita by design |
| Source checks | none | 11 checks; preflight; fallbacks for OpenFEMA and NWS |
| DQ checks / tests | 30 / 61 | 40 / 90 |

### Defects found and fixed during this work

| Found by | Defect | Fix |
|---|---|---|
| `source-check` | NWS files sorted alphabetically, so the "newest" probe used `bp18mr25` | Sort by the date encoded in the name |
| SQL review | `to_date(..., 'DDmonYY')` cannot parse NWS two-letter months (old crosswalk dates were silently NULL) | `staging.nws_file_date` |
| Archive download | `bp03oc08` 404 for the `:80` URL form | Registry uses the verified form (same SHA-1) |
| National DQ (critical) | Name-verification check flagged 191 valid matches: some 2004–2008 versions name one zone differently on different rows | Check requires *a* matching row, as the mapping does |
| Report review | DQ table showed "! warn" in the severity column for passing checks | Status labels rendered from data |
| Fixture test | Real Bucks County zones (`UPPER/LOWER BUCKS` vs `Bucks`) inside a fixture gap | Correct behaviour (unmapped, not guessed); asserted |

## Historical: declared areas, Q4 and denials (2026-11-08)

Two independent reviews found that FEMA declared areas with `fipsCountyCode = '000'` were all
treated as statewide, that Q4 lacked region and hazard breakdowns, that DeclarationDenials did not
feed any question, and that several documents contradicted each other. Changes:

* `staging.fema_area_level`: `statewide` only for a literal "Statewide" `designatedArea`; other
  code-`000` areas are `tribal` (kept by name, never expanded to counties); `county_fips` only on
  county rows (table constraint and critical check).
* Q2: unit renamed **event-county pair**; significance threshold applied in constant 2025 dollars
  (`significant_damage_real_usd`); events match declarations only for compatible hazard families
  (`seeds/hazard_match_rules.csv`); `fema_region` added.
* Q4: `fema_region`, `svi_group`, tribal/statewide flags and real-dollar NOAA damage on
  `serving.declaration_timeliness`; new `serving.declaration_timeliness_summary` (SVI group,
  hazard family, FEMA region; quartiles, hazard mix, sample-size notes).
* DeclarationDenials: `curated.fact_declaration_denial` and
  `serving.declaration_request_outcomes`, shown in the report (Q2 by region, Q4 by hazard and
  region) and the dashboard.
* 11 new DQ checks (51 in total), 30 new tests (120 in total), a real OpenFEMA code-`000` fixture,
  a guard that stops the tests if `RESGAP_TEST_DATABASE_URL` names the pipeline database, and
  Compose ports bound to 127.0.0.1.

### Commands and results

| Check | Result |
|---|---|
| `make lint` | `All checks passed!` |
| `make test` against Docker PostgreSQL (`postgres:16-alpine`, `127.0.0.1:5433/resgap_test`) | **120 passed** (62 unit + 58 integration) |
| `make test-unit` / `pytest` without a test database | `62 passed` / `62 passed, 58 skipped` |
| `RESGAP_TEST_DATABASE_URL` set to the pipeline database `…@127.0.0.1:5433/resgap` | session stopped before any test ("points at the same database as RESGAP_DATABASE_URL"); the database's 215,281 raw storm rows were untouched |
| `make offline-demo` on a new, empty Docker database | exit 0; **51/51** DQ checks pass, no warnings; report written |
| Fixture pipeline with `RESGAP_OBJECT_STORE=s3` against SeaweedFS 4.52 (new database, new bucket prefix) | exit 0; **51/51** DQ checks pass; 14 raw objects in the bucket (every fixture object) |
| Live `sample` run (`resgap --profile sample run`) on Docker PostgreSQL with an empty lake | exit 0 in **184 s** (2 min 22 s fetching the 47 crosswalk versions, 35 from the archive at ≥ 1 s spacing; transform 9 s); 60.3 MB in 58 objects; 215,281 storm rows; 205 + 1,013 + 9 OpenFEMA records; **51/51** DQ checks pass, no warnings; database 490 MB |
| National transform + DQ on the already-loaded 2000–2025 data (no download; native PostgreSQL 16) | transform 52 s, DQ 15 s; **51/51** DQ checks pass |
| `docker compose config` (all profiles) | valid; `db` published on `127.0.0.1:5433`, `s3` on `127.0.0.1:8333` (confirmed with `docker compose ps` after recreating the containers) |
| `docker compose --profile app build pipeline`, then `… run --rm pipeline resgap status` | image `resgap-pipeline:latest` built (317 MB); the container listed the new tables from the Compose database |
| Dashboard (Streamlit `AppTest`) on the fixture and the national database | no exceptions; 4 metrics; Q2 region and Q4 tables render; Q4 grouping switch works |
| `make proposal` | `milestone1-proposal.pdf (2 pages)` |
| `make source-check` | not re-run: no source contract (URLs, fields, fingerprints, scopes) changed |
| GitHub Actions | at this historical checkpoint, runs `42492181411` and `42492264285` were green; the current implementation later passed run `42512681303` at commit `1ba38a2` |

### Before / after

Same raw data before and after; "before" is the previous commit's SQL.

| Measure | Before | After |
|---|---|---|
| National declared areas (incident 2000–2025) | 47,781 county · **1,122 statewide** · 60 unresolved | 47,781 county · **20 statewide** · **1,102 tribal** · 60 unresolved |
| National weather DRs with a statewide designation | 208 | 6 (202 were tribal areas); 204 DRs designate tribal areas; 70 have no county designation |
| National significant event-county pairs | 77,004 (nominal threshold) | 82,573 (constant-dollar threshold) |
| National covered pairs | 18,316 (**23.79 %**) | 17,864 (**21.63 %**) |
| … of which matched through a "statewide" designation | 780 | 32 |
| Effect of each fix, applied in turn (national) | 23.79 % | statewide/tribal fix 22.80 % (−756 pairs; SD −129, AZ −87, WA −83, NE −82, MN −75) → real-dollar threshold 22.27 % → hazard compatibility 21.63 % |
| National coverage by SVI quartile Q1 / Q2 / Q3 / Q4 | 24.85 / 24.92 / 23.56 / 23.03 % | 22.54 / 23.17 / 21.37 / 20.63 % |
| National coverage, Puerto Rico / island areas / legacy CT | 10.61 / 13.85 / 30.90 % | 8.61 / 13.85 / 28.17 % |
| National coverage of Heat / Drought / Other Weather pairs | 2.78 / 0.69 / 3.21 % | 0 / 0 / 0 % (no compatible declaration type) |
| National emergency (EM) coverage, pairs | 4,398 | 3,996 |
| Sample (2022–2024, 8 states) | 715 pairs, 95 covered (13.29 %); 1 "statewide" area (a NY tribal area) | 717 pairs, 93 covered (12.97 %); 0 statewide, 1 tribal |
| Fixture (PA, PR, VI 2024) | 36 pairs, 4 covered (11.11 %) | unchanged (no code-`000` rows) |
| Q4 national medians, incident → declaration | by SVI quartile only (Q1 43, Q2 45, Q3 32, Q4 29 days) | unchanged by SVI group, plus hazard family (tropical cyclone 9 … flood 57 days), FEMA region (Region 4 21 … Region 10 73 days), quartiles and hazard mix; overall 40 days over 1,513 DRs |
| DeclarationDenials in analysis | one count column, by request year, all hazards | 351 records by incident year (301 weather); 262 DR + 39 EM turndowns in `declaration_request_outcomes`; 14.8 % of recorded weather DR decisions were turndowns |
| Priority-gap counties (now the high-need, low-obligation follow-up screen), damage, HMA | 84; $818.0 B (2025 $) | unchanged |
| DQ checks / tests | 40 / 90 | 51 / 120 |

## Historical: HMA application, status and obligation funnel (executed 2026-11-08, before the Connecticut fix)

Counts in this section (64 DQ checks, 155 tests, the stage tables) are intermediate evidence; they
were current until the next section, which re-measured them after the Connecticut fix.

The Milestone 1 proposal (`docs/proposal/milestone1-proposal.md`) is the product contract.
Declaration coverage is designation, not money; DeclarationDenials are outcomes of presidential
declaration requests, not HMA decisions; HMA records are subapplications that reached FEMA; the
absence of a county-mapped record does not show that a county never tried to apply. The code was
changed to match:

* `curated.fact_hma_allocation`: `is_funded` replaced by `has_positive_obligation`; new
  `obligation_amount_class` (positive / zero / negative / not_reported), `is_weather` and
  `period_start_fy`; `status` documented as verbatim. New `curated.dim_hma_period` (fixed
  five-year fiscal-year periods, clipped to the profile) and `staging.hma_period_start`.
* `serving.county_resilience_profile`: all-period `hma_activity_stage`, observed /
  positive-obligation / no-positive subapplication counts, weather-related recorded obligation in
  nominal and constant dollars, `hma_status_mix`, `hma_per_capita_real_median`;
  `is_low_mitigation` → `is_low_obligation`, `is_priority_gap` → `is_high_need_low_obligation`
  (rule unchanged). `hma_subapplications` → `hma_observed_subapplications`,
  `hma_funded_subapplications` → `hma_positive_obligation_subapplications`.
* New serving tables `hma_county_period_activity`, `hma_county_program_hazard_activity`,
  `hma_status_summary`, `hma_stage_summary`; stage columns added to `svi_quartile_summary`
  (`priority_gap_counties` → `high_need_low_obligation_counties`) and subapplication counts to
  `state_year_summary`.
* Report and dashboard: Q3 rewritten stage by stage with the central limitation, "recorded
  obligation" and "no observed county-mapped subapplication" wording, program, hazard and status
  breakdowns; declaration turndowns kept in Q2/Q4 only. Documentation updated throughout.
* 13 new DQ checks (11 critical, 2 info; 64 in total) and new tests: an SQL-function boundary
  test (11 cases), 6 new county-name-key vectors, a unit test for the check registry, and
  integration tests for stages on real
  fixture records, verbatim statuses, periods, reconciliation, the exact follow-up rule, a pass
  check of every funnel DQ check, and 11 mutation cases that each funnel check must detect.
* `staging.county_name_key` now folds accents and repairs the Census reference file's
  double-encoded Spanish letters. An independent desk review found that `Mayaguez`,
  `Rio Grande`, `Anasco` and `Canovanas` in the real HMA fixture did not match their municipios,
  which would have shown them as "not observed" (and `Rio Grande` as out-of-state via Colorado).
  New SQL test vectors and fixture assertions cover this. The national name-resolution counts
  measured before this fix (855 unresolved, 19,993 out-of-state) are historical; the counts
  after the fix are below.
* `resgap transform` now runs the idempotent `init-db` step first, so SQL helper functions added
  since a database was created (here `staging.hma_period_start`) exist before the models run.
* No fixture was changed.

### What was run (2026-11-08)

| Check | Result |
|---|---|
| `make lint` | `All checks passed!` |
| `make test` against Docker PostgreSQL | **155 passed** in 9.64 s (collected: 63 unit + 92 integration) |
| `make offline-demo` | exit 0; **64/64** DQ checks pass; report written |
| National transform without download (`--profile full transform`) on the existing 2000–2025 raw data, PostgreSQL 16 | exit 0, about 62 s |
| National DQ (`resgap dq`) | exit 0; **64/64** checks pass; about 16 s |
| National report (`resgap report`) | written |
| Dashboard, Streamlit `AppTest` on `dashboard/app.py`, against the fixture database and the national database | each: 0 exceptions, 5 metrics, 8 dataframes |

**Not re-run after this change** (the latest results are in the historical sections above, from
before the HMA funnel):
* the live `sample` run (`make run`);
* the fixture run against S3 (SeaweedFS);
* `make source-check`: no source contract or source-check input changed.

AWS S3 itself has still never been exercised.

**CI at this historical checkpoint:** the funnel was not committed. The complete implementation
later passed GitHub Actions run `42512681303` at commit `1ba38a2`.

### National HMA results (2000–2025, after the accent fix)

Benefiting-area rows in the HMA county bridge, by `area_level`:

| `area_level` | Before the accent fix (historical) | After |
|---|---|---|
| `county` | ~94 k | 94,396 |
| `out_of_state` | 19,993 | 19,985 |
| `statewide` | not recorded | 3,423 |
| `unresolved` | 855 | 621 |
| `unspecified` | 2,593 subapplications | 2,593 |

All-period HMA activity stage (county-mapped areas, FY2000–2025), counties and
county-equivalents:

| Analysis universe | Counties | `obligated` | `observed_no_positive_obligation` | `not_observed` |
|---|---|---|---|---|
| `states_dc` | 3,144 | 3,126 | 0 | 18 |
| `puerto_rico` | 78 | 78 | 0 | 0 |
| `island_areas` | 13 | 11 | 0 | 2 |
| `legacy_geography` | 8 | 8 | 0 | 0 |
| **Total** | **3,243** | **3,223** | **0** | **20** |

Stage by five-year fiscal-year period (all 3,243 counties in every period):

| Period | `obligated` | `observed_no_positive_obligation` | `not_observed` |
|---|---|---|---|
| FY2000 (one-year period in the full profile) | 244 | 14 | 2,985 |
| FY2001–2005 | 1,688 | 149 | 1,406 |
| FY2006–2010 | 2,261 | 204 | 778 |
| FY2011–2015 | 2,328 | 135 | 780 |
| FY2016–2020 | 3,110 | 20 | 113 |
| FY2021–2025 | 2,995 | 202 | 46 |

Stage counts by SVI quartile are in `serving.hma_stage_summary`; they are not transcribed here.

* **Follow-up screen** (`is_high_need_low_obligation`): **84 counties**, unchanged (the rule is
  unchanged; only the name changed).
* **Recorded positive HMA obligation, constant 2025 dollars:** $31.447444 B in total. Of that,
  $25.799911 B is attributed to counties and $5.647533 B stays at state level (statewide,
  out-of-state, unresolved or unspecified areas), so **17.9586 %** is outside county attribution.
  An earlier version of this line quoted $31.446 B, which is the *net* sum including negative
  de-obligation records; it is not the positive recorded obligation and is not used. The
  historical figures from before the funnel were $31.45 B in total and $25.74 B
  county-attributed.
* The stage tables above were measured **before the Connecticut fix**: the 18 `not_observed`
  states + DC counties include the 9 Connecticut planning regions, and each period's
  `not_observed` count includes them too. The follow-up count of 84 was likewise computed with
  the regions in the states + DC HMA median. These figures are evidence for the funnel
  implementation only; they were re-measured after the fix (see the next section).

## Historical: Connecticut geography break (HMA only) and final review (executed 2026-11-08)

*Historical.* This section described the code at that time: **166 tests and 71 DQ checks**, with
the Connecticut break applied to HMA only. It is superseded by the
[next section](#current-storm-and-declaration-geography-break-executed-2026-11-08):
storm and declaration history of the CT planning regions was still shown as zero here, which
affected the damage ranks, the SVI-quartile damage and coverage rates, the damage-burden
quartiles and the follow-up screen count below. Two statements of this section are corrected in
place (marked *Correction*). The changes below were implemented and then executed on 2026-11-08;
the results are under
[What was run](#what-was-run-after-the-connecticut-fix-2026-11-08) and
[National results](#national-results-after-the-connecticut-fix-20002025-resgap_full). The 155
passing tests and 64/64 DQ results in the previous section are historical intermediate evidence
for the funnel before these changes.

**Connecticut geography (the release blocker, now fixed and measured).** OpenFEMA HMA names the
8 legacy Connecticut counties (09001–09015); the current geography (Census Vintage 2025, SVI 2022)
has 9 planning regions (09110–09190), which do not nest in the old counties. The funnel run
classified the regions `not_observed`, which put 9 zero-obligation units into the states + DC
HMA median and ranks, the stage counts, the headline shares and the follow-up denominator.
`geography_not_comparable` exists to state this explicitly: it means "the HMA records for this
area are published on a different county geography", not "no HMA activity". Storm, declaration
and SVI measures were left unchanged by this fix (*Correction*: that was itself a defect for
storm and declaration history, fixed in the next section).
* `curated.dim_county`: new `hma_geography_comparable` (false for current units of a state that
  also has `legacy_geography` units, i.e. the 9 CT planning regions) and `hma_geography_note`.
* `serving.county_resilience_profile`, `serving.hma_county_period_activity`: new stage
  `geography_not_comparable` (checked first, in the profile and in every period; the dense grid
  is kept); HMA per-capita, rank, median, `is_low_obligation` NULL and the follow-up flag false
  for those units; storm and SVI columns unchanged. Table constraints tie the stage to the flag.
* `serving.svi_quartile_summary`, `serving.hma_stage_summary`: new
  `counties_*geography_not_comparable`, `counties_hma_comparable` and comparable population /
  damage columns; every Q3 share and per-capita rate uses comparable units only.
* New `serving.hma_damage_burden_summary` (analysis universe × damage-burden quartile).
* No legacy amount is mapped to a planning region; legacy counties stay `legacy_geography` and
  are excluded from the current-geography HMA headline shares in the report and dashboard
  (*Correction*: an earlier wording said "headline percentages" in general; the storm, damage and
  coverage tiles include the legacy counties' pre-change history).

**Other final-review fixes.**
* Report and dashboard: Q3 stage by period × SVI quartile (states + DC) and by period for each
  other universe; damage-burden quartile × stage; Q2 coverage by state; Q4 incident start →
  request and incident start → declaration for every group (request → declaration kept as an
  extra column); weather-related obligation labelled a lower bound with queried counts; the
  follow-up list labelled "top 20 of N"; a note explaining why the all-period observed and
  positive-obligation shares are close (single combined tile, same denominator); positive,
  county-attributed, state-level and labelled net HMA totals, queried; state-year scope note;
  Puerto Rico's own-median `is_low_obligation` explained.
* `serving.state_year_summary`: scope documented in the SQL, the table comment and the data
  contract.
* DQ: 7 new checks (71 in total): `hma_geography_break_guard`, `state_year_hma_reconciles`,
  `hma_damage_burden_reconciles` (critical); `hma_records_on_noncomparable_geography` (warn,
  threshold 0); `hma_geography_not_comparable_units`, `state_year_hma_out_of_scope`,
  `hma_weather_lower_bound_not_specified` (info). Changed: `hma_positive_obligation_matches_source`
  (also against the raw JSON and county shares vs the raw amount), `hma_stage_exclusive_exhaustive`
  (fourth stage, comparability from the dimension), `hma_period_reconciles_profile`,
  `high_need_low_obligation_flag_exact` (from totals; every universe's `is_low_obligation`
  against its own median), `hma_stage_summary_reconciles` (independent recount from the fact),
  `hma_counties_not_observed` (comparable units only).
* Tests: 2 new unit tests (check count 71 and unique names; comparability taken from the
  dimension in every stage check) and an extended registry test; report assertions for every new
  section; 8 new mutation cases (positive-obligation flag in curated and in raw, two stage-summary
  shifts that keep the row sums, the geography guard, state-year amount, damage-burden shift,
  Puerto Rico `is_low_obligation`); a simulated-Connecticut integration test (one PA county
  marked non-comparable and the dependent serving tables rebuilt in a rolled-back transaction).
  The suite now collects 166 tests (155 before this section); all 166 passed (below).
* Formatting: `ruff format --check src tests scripts dashboard` reported 3 files that needed
  reformatting, and `ruff format` was applied to those 3 files (layout only). The repeat check
  reported all 41 Python files under `src`, `tests`, `scripts` and `dashboard` formatted (ruff's
  count for those four paths; other Python files in the repository, if any, were not part of the
  check). `make lint` separately runs `ruff check`.

### What was run after the Connecticut fix (2026-11-08)

| Check | Result |
|---|---|
| `make lint` (`ruff check src tests scripts dashboard`) | passed |
| `ruff format --check src tests scripts dashboard`, then `ruff format`, then repeat check | 3 files reformatted; repeat passed with all 41 Python files under `src`, `tests`, `scripts` and `dashboard` formatted |
| `make test` (`resgap_test`) | **166 passed** in 9.54 s |
| `make offline-demo` (fixture profile) | exit 0; **71/71** DQ checks pass; report written |
| National transform without download (`uv run resgap --profile full transform`) on the existing 2000–2025 raw data in `resgap_full` | exit 0; 13:14:33 → 13:15:31, about 58 s |
| National DQ (`uv run resgap dq`) | exit 0; **71/71** checks pass; 13:15:34 → 13:15:51, about 17 s |
| National report (`uv run resgap report`) | written |
| Dashboard, Streamlit `AppTest` on `dashboard/app.py`, against the fixture database and the national database | each: exit 0, 0 exceptions, 5 metrics, 12 dataframes |

**Not re-run after these changes** (the latest results are in the historical sections above):
* the live `sample` run (`make run`);
* `make source-check` (no source contract or source-check input changed);
* the fixture run against S3 (SeaweedFS).

AWS S3 itself has still never been exercised.

**CI at this historical checkpoint:** the HMA funnel and Connecticut changes were not committed.
The complete implementation later passed GitHub Actions run `42512681303` at commit `1ba38a2`.

### National results after the Connecticut fix (2000–2025, `resgap_full`)

All-period HMA activity stage (county-mapped areas, FY2000–2025), all 3,243 profile counties and
county-equivalents:

| Analysis universe | Counties | `obligated` | `observed_no_positive_obligation` | `not_observed` | `geography_not_comparable` |
|---|---|---|---|---|---|
| `states_dc` | 3,144 | 3,126 | 0 | 9 | 9 |
| `puerto_rico` | 78 | 78 | 0 | 0 | 0 |
| `island_areas` | 13 | 11 | 0 | 2 | 0 |
| `legacy_geography` | 8 | 8 | 0 | 0 | 0 |
| **Total** | **3,243** | **3,223** | **0** | **11** | **9** |

**Current-geography HMA headline** (*Correction*: an earlier version of this paragraph called
3,226 the headline; 3,226 adds the 13 island-area units, which the report and dashboard headline
does not include). The report and dashboard HMA headline covers HMA-comparable units of the
states, DC and Puerto Rico: **3,213** units (3,135 states + DC, 78 Puerto Rico), **3,204**
obligated, 0 observed without a positive obligation, **9** not observed (all in states + DC),
from the table above. Island areas (13 units: 11 obligated, 2 not observed) and the 8 legacy
counties (all obligated; Connecticut's HMA records) are reported separately, as are the 9
non-comparable CT planning regions.

Stage by five-year fiscal-year period (all 3,243 units in every period; the 9 CT planning regions
are `geography_not_comparable` in every period):

| Period | `obligated` | `observed_no_positive_obligation` | `not_observed` | `geography_not_comparable` |
|---|---|---|---|---|
| FY2000 (one-year period in the full profile) | 244 | 14 | 2,976 | 9 |
| FY2001–2005 | 1,688 | 149 | 1,397 | 9 |
| FY2006–2010 | 2,261 | 204 | 769 | 9 |
| FY2011–2015 | 2,328 | 135 | 771 | 9 |
| FY2016–2020 | 3,110 | 20 | 104 | 9 |
| FY2021–2025 | 2,995 | 202 | 37 | 9 |

* **Follow-up screen** (`is_high_need_low_obligation`): 84 counties in that run (*historical*,
  superseded: the CT planning regions were out of the HMA median but still ranked with
  false-zero damage; the next section measured 83). The states + DC median of
  HMA-comparable units is **$33.414512** recorded obligation per resident (constant 2025
  dollars). Puerto Rico's `is_low_obligation` uses its own median, **$78.207018**. Island areas
  and legacy geography have no population, hence no per-capita median.
* **Recorded positive HMA obligation, constant 2025 dollars:** $31.447444 B in total;
  $25.799911 B attributed to counties; $5.647533 B not attributed to a county (statewide,
  out-of-state, unresolved or unspecified areas), i.e. **17.9586 %**. The **net** sum including
  negative de-obligation records is about $31.446 B; it is quoted only when labelled as net.
* **Weather-related obligation is a lower bound:** 10,056 of 54,638 source subapplications have
  no `primaryHazard`. County-attributed records with hazard family "Not specified" cover 7,401
  distinct subapplications and $2.537332 B in positive recorded obligation (2025 $).
* **HMA benefiting areas** (`area_level`): 94,396 `county`, 19,985 `out_of_state`, 3,423
  `statewide`, 621 `unresolved`, 2,593 `unspecified` (unchanged by the Connecticut fix).
* **State-year scope:** `serving.state_year_summary` explicitly accounts for 906 excluded
  subapplications, 883 without an applicant state and 23 from FM, about $82.2 M (2025 $).

### Before / after the Connecticut fix (HMA only)

| Measure | Funnel run (historical) | HMA-only fix (historical) |
|---|---|---|
| States + DC `not_observed`, all periods | 18 (incl. the 9 CT regions) | 9 |
| CT planning regions | `not_observed`, zero obligation, in the median and ranks | `geography_not_comparable`, outside every HMA rate, rank, median and the screen |
| `not_observed` per period | 2,985 … 46 | 2,976 … 37 (each period −9) |
| Follow-up screen | 84 | 84 |
| DQ checks / tests | 64 / 155 | 71 / 166 |
| Dashboard `AppTest` | 5 metrics, 8 dataframes | 5 metrics, 12 dataframes |
| National transform / DQ | ~62 s / ~16 s | ~58 s / ~17 s |

## Current: storm and declaration geography break (executed 2026-11-08)

**Status: implemented and executed locally on 2026-11-08.** Lint, the format check, the full
test suite (175 passed), the offline demo (75/75 DQ checks), the national no-download rebuild
(75/75 DQ checks), the national report and the dashboard `AppTest` all succeeded; results are
under [What was run](#what-was-run-storm-and-declaration-geography-break-2026-11-08) and
[National results](#national-results-storm-and-declaration-geography-break-20002025-resgap_full).
GitHub Actions run `42512681303` passed at commit `1ba38a2` after these changes were committed.

**Problem (release blocker, now fixed and measured).** NOAA Storm Events and OpenFEMA disaster
declarations, like HMA, record 2000–2025 Connecticut history on the 8 legacy counties
(09001–09015). The 9 current planning regions (09110–09190) have current SVI and population but
no comparable storm or declaration facts. The HMA-only fix still gave them 0 storm events, $0
damage, 0 declarations and `damage_pctile` 0. Those false zeros entered the states + DC damage
ranks, the high-need / low-obligation screen, the SVI-quartile damage and coverage rates and the
damage-burden quartiles. After the fix the follow-up screen has **83** counties (84 in the
superseded HMA-only run).

**Changes.**
* `curated.dim_county`: new `storm_declaration_geography_comparable` and
  `storm_declaration_geography_note`, from the same data-driven rule as `hma_geography_comparable`
  (a current unit of a state that also has `legacy_geography` units; measured nationally: exactly
  the 9 CT planning regions 09110–09190). No legacy fact is crosswalked or allocated to a planning
  region.
* `serving.county_resilience_profile`: for those units storm events, damage (total, property,
  crops, nominal and real), deaths, injuries, significant and covered events, significant and
  covered damage, major disasters and emergencies are NULL, not zero; so are per-capita damage,
  coverage rate, `damage_pctile`, `is_high_loss` and `hma_to_damage_ratio`. They are excluded from
  damage ranks and the follow-up screen; SVI and population are kept; the HMA stage stays
  `geography_not_comparable`. New table constraint `profile_history_null_when_not_comparable`.
* `serving.svi_quartile_summary`: new `counties_history_not_comparable`,
  `counties_history_comparable`, `history_comparable_population`; damage, per-capita damage
  (denominator `history_comparable_population`) and coverage use comparable units only;
  `hma_comparable_damage_real_usd` / `hma_per_damage_dollar` need both HMA and storm
  comparability.
* `serving.hma_damage_burden_summary`: ranks only units with comparable storm history, so the
  regions are in no quartile (they were all in D1).
* `serving.hma_county_period_activity`: same-year storm damage NULL for those units.
  `serving.hma_stage_summary`: new `counties_storm_history_comparable`; storm damage over
  comparable units only.
* Report: Connecticut note rewritten (regions keep SVI/population and are excluded from 2000–2025
  storm, declaration, coverage, damage-rank and HMA comparisons; legacy-county storm and
  declaration facts are real pre-change history and may stay in national historical event
  totals; only the current-geography HMA headline shares leave the legacy counties out); the
  storm tile relabelled **storm event-county records** (the national 1,674,296 of the previous
  run counts event-county records, not distinct storm events); Q1 table shows the comparable
  counts and the population denominator; the top-loss table sorts NULL last and lists comparable
  units only; HMA headline note states its scope (states + DC + Puerto Rico; island areas,
  planning regions and legacy counties separate).
* Dashboard: same Connecticut note; damage and coverage metrics skip NULL history and explain the
  scope; county table shows `storm_declaration_geography_comparable` and `damage_pctile` and
  sorts NULL damage last.
* DQ (71 → **75** checks): new `storm_declaration_geography_break_guard` (critical; triggered by
  storm or declaration facts on legacy geography, it identifies current units from
  `analysis_universe` and state, and fails if the dimension or profile flag is not false, if any
  history column, rate or rank is non-NULL, if the unit is screened, or if same-year period damage
  is non-NULL), `history_summaries_reconcile` (critical; independent recount of the SVI-summary
  and all-period stage-summary storm/coverage columns from the curated facts, comparability
  re-derived from `analysis_universe` and state), `storm_declaration_geography_not_comparable_units`
  (info; 9 units nationally), `history_facts_on_noncomparable_geography` (warn, threshold 0;
  storm or declaration facts already on a planning region, withheld from the profile).
  Changed, not weakened: `serving_damage_reconciles` (profile + withheld damage = fact total
  exactly; withheld amount also present in the profile would double count and fail),
  `high_need_low_obligation_flag_exact` and `hma_damage_burden_reconciles` (storm comparability
  re-derived from `analysis_universe` and state; damage per resident recomputed from the fact).
* Tests: 2 new unit tests (registration and severities of the 4 checks; the recounts must not
  read the stored storm flag); the check-count test now expects 75; 6 new mutation cases (a PA
  county turned into legacy geography must be caught by the guard, the SVI-summary recount, the
  damage-burden recount and the damage reconciliation; a shifted comparable population; a shifted
  stage-summary storm damage); a fixture assertion that no unit lacks comparable history; report
  assertions for the new wording; and a new end-to-end simulation
  (`test_storm_declaration_geography_break_end_to_end`): a legacy PA county is added to
  `staging.county_ref`, one real storm fact moved onto it, `curated.dim_county` and the
  dependent serving tables rebuilt in a rolled-back transaction; all 67 current PA units must have
  NULL history, no damage rank, no screen, kept SVI and population, all critical checks must
  pass, and four disguised false-zero mutations must be caught. The previous single-unit
  simulation now flips only the HMA flag and no longer asserts that the damage rank stays
  non-null (that described the old, wrong rule). The suite collects 175 tests (166 + 2 unit +
  6 mutation cases + 1 simulation); all 175 passed (below).

**Preserved, unchanged:** positive-only obligations, the weather lower bound, state-year
exclusions, SVI-by-period, the damage-burden output, both Q4 lags, Q2 coverage by state, verbatim
status, the application-ambiguity wording and the proposal framing (`docs/proposal/` not edited).

### What was run (storm and declaration geography break, 2026-11-08)

| Check | Result |
|---|---|
| `make lint` (`ruff check src tests scripts dashboard`) | passed |
| `ruff format --check src tests scripts dashboard` | passed; 41 files already formatted, so no formatting was needed |
| `make format` | not completed: in the sandboxed session it stopped on a uv cache permission error (an environment restriction, not a product failure). It was not needed, because the format check above passed with every file already formatted |
| `make test` (`resgap_test`) | **175 passed** in 12.35 s |
| `make offline-demo` (fixture profile) | exit 0; **75/75** DQ checks pass; report written |
| National transform without download (`uv run resgap --profile full transform`) on the existing 2000–2025 raw data in `resgap_full` | exit 0; 13:46:40 → 13:47:42, about 62 s |
| National DQ (`uv run resgap dq`) | exit 0; **75/75** checks pass (including `storm_declaration_geography_break_guard`, `history_summaries_reconcile` and `history_facts_on_noncomparable_geography`, warn with threshold 0); 13:47:46 → 13:48:03, about 17 s |
| National report (`uv run resgap report`) | written successfully at 13:48:07 |
| Dashboard, Streamlit `AppTest` on `dashboard/app.py`, against the fixture database and the national database | each: exit 0, 0 exceptions, 5 metrics, 12 dataframes |

**Not re-run after these changes** (the latest results are in the historical sections above):
* the live `sample` run (`make run`);
* `make source-check` (no source contract or source-check input changed);
* the fixture run against S3 (SeaweedFS).

AWS S3 itself has still never been exercised.

**CI:** GitHub Actions run `42512681303` passed in 1 min 3 s at commit `1ba38a2`. It ran lint,
all 175 tests, the 75-check offline pipeline and uploaded the report artifact.

### National results (storm and declaration geography break, 2000–2025, `resgap_full`)

**Connecticut planning regions.** Exactly **9** units (09110–09190) have
`storm_declaration_geography_comparable = false`. For each, storm events, damage, damage
percentile, significant events, major disasters and emergencies are NULL (not zero); SVI and
population remain. They stay `geography_not_comparable` in the all-period HMA stage and in every
HMA period.

**Follow-up screen** (`is_high_need_low_obligation`): **83 counties** (84 in the superseded
HMA-only run). Union County SC and Dickey County ND are below the 0.75 damage-percentile boundary
after the correction and are not high-loss.

**Q1 damage per resident by national SVI quartile** (states + DC, constant 2025 dollars, units
with comparable storm and declaration history only; per-capita denominator
`history_comparable_population`):

| SVI quartile | Damage per resident (2025 $) | Units with comparable history | Units not comparable (CT planning regions) |
|---|---|---|---|
| Q1 | 1,803.72 | 784 | 2 |
| Q2 | 1,742.93 | 783 | 3 |
| Q3 | 2,391.35 | 783 | 3 |
| Q4 | 3,079.95 | 785 | 1 |
| **Total** | | **3,135** | **9** |

**HMA (unchanged by this fix, re-measured):**
* Current-geography HMA headline (HMA-comparable units of the states, DC and Puerto Rico):
  **3,213** units, **3,204** obligated, **9** not observed. Island areas and legacy geography are
  reported separately.
* All-period HMA stage across all 3,243 profile units: **3,223** obligated, **11**
  `not_observed`, **9** `geography_not_comparable` (the CT planning regions, in every period).
* Positive-only recorded obligation, the state-year exclusions and the missing-hazard
  (weather lower-bound) values are unchanged from the prior measured values: $31.447444 B
  positive recorded obligation (2025 $), $25.799911 B county-attributed, $5.647533 B
  (17.9586 %) not attributed to a county; 906 state-year exclusions (883 without an applicant
  state, 23 from FM; about $82.2 M); 10,056 of 54,638 subapplications without `primaryHazard`,
  7,401 county-attributed "Not specified" subapplications, $2.537332 B in positive recorded
  obligation.

Confirmed in the current national database: 1,674,296 weather event-county records; $818.045 B
damage in 2025 dollars ($574.891 B nominal); 82,573 significant event-county pairs with 17,864
covered (21.63 %); Q1/Q2/Q3/Q4 coverage 22.54/23.17/21.37/20.63 %; HMA per-capita medians
$33.414512 for comparable states + DC and $78.207018 for Puerto Rico. The per-period HMA stage
counts are unchanged and listed in the historical comparison section above.

### Before / after the storm and declaration geography break

| Measure | Before (HMA-only fix and earlier runs, historical) | Storm/declaration break (current) |
|---|---|---|
| CT planning regions' storm and declaration history | 0 events, $0 damage, 0 declarations, `damage_pctile` 0 | NULL (not comparable); SVI and population kept |
| Follow-up screen | 84 | 83 |
| States + DC SVI Q1 / Q4 damage per resident (2025 $) | $1,793 / $3,071 (earlier national run, rounded; the regions' population was in the denominator with zero damage) | $1,803.72 / $3,079.95 |
| HMA headline (states + DC + PR) | 3,213 / 3,204 obligated / 9 not observed | unchanged |
| HMA stages, all units | 3,223 / 11 not observed / 9 not comparable | unchanged |
| DQ checks / tests | 71 / 166 | 75 / 175 |
| Dashboard `AppTest` | 5 metrics, 12 dataframes | 5 metrics, 12 dataframes |
| National transform / DQ | ~58 s / ~17 s | ~62 s / ~17 s |
