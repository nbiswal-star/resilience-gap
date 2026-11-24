# Resilience Gap

**How do presidential disaster declarations and recorded Hazard Mitigation Assistance (HMA)
obligations align with county storm losses and social vulnerability?**

Resilience Gap is an end-to-end data engineering project for 95-815 Data Engineering (Fall 2026,
Mini 2). It integrates storm losses (NOAA), presidential disaster declarations and HMA
subapplications, statuses and obligations (FEMA), social vulnerability (CDC/ATSDR), population
(U.S. Census Bureau) and prices (BLS CPI) at the U.S. county level. The pipeline stores the raw
data unchanged, cleans and joins it with SQL in PostgreSQL, and produces analysis-ready tables, an
HTML report and a dashboard. The analysis is descriptive, not causal.

> **Central limitation.** HMA records are subapplications that *reached FEMA*. A county with no
> observed county-mapped subapplication may still have tried to apply: the state may have
> screened out or deprioritized a proposal, local capacity (staff, cost share, benefit-cost
> analysis) may have limited what was prepared, or the county may be covered only by a statewide
> or unparsed entry. A zero recorded obligation can also reflect a pending, withdrawn or
> unsuccessful subapplication, or a true zero. The data cannot separate these cases, so the
> pipeline reports the **stage** at which activity is or is not observed and never infers that a
> county did not apply, why its obligation is low, or that it was neglected.

This README is the primary handoff document. Deeper material lives in [`docs/`](#14-further-documentation).

**Contents:**
[1 Problem](#1-problem-and-stakeholders) ·
[2 Questions](#2-analytical-questions) ·
[3 Data](#3-datasets-licences-and-terms) ·
[4 Architecture](#4-architecture-and-directory-map) ·
[5 Quick start](#5-quick-start-from-a-fresh-clone) ·
[6 Running](#6-running-the-pipeline) ·
[7 Commands](#7-command-reference) ·
[8 Outputs](#8-report-and-dashboard) ·
[9 Tests and CI](#9-tests-lint-and-ci) ·
[10 Configuration](#10-configuration-and-object-storage) ·
[11 Data model](#11-data-model-idempotency-provenance-and-data-quality) ·
[12 Troubleshooting](#12-troubleshooting-and-cleanup) ·
[13 Limitations](#13-known-limitations) ·
[14 Docs](#14-further-documentation) ·
[15 Milestones](#15-milestones-and-requirement-mapping) ·
[16 Team workflow](#16-team-workflow)

---

## 1. Problem and stakeholders

Storms, floods, hurricanes and winter storms damage U.S. communities every year. NOAA's Storm Events
Database records at least **$818 billion** in property and crop damage, in 2025 dollars ($575 billion
nominal), for events from 2000 to 2025 that can be placed in a county. This figure is our own tally
from the bulk files, adjusted with the BLS Consumer Price Index.

Two federal channels respond, and in both a county usually reaches FEMA through a state-level
process:

* **After** an incident, a governor or tribal chief executive requests a presidential declaration.
  A *major disaster declaration* designates specific counties (rarely a whole state) for FEMA
  assistance, or the request is turned down. Designation is eligibility, not a dollar amount.
* **Before** the next incident, FEMA *Hazard Mitigation Assistance (HMA)* programs fund projects
  such as buyouts, drainage and safe rooms. States, territories and tribes act as applicants:
  they solicit, screen and prioritize local *subapplications* and forward selected ones to FEMA;
  funds are *obligated* only later.

Communities differ in their ability to prepare and recover, which the CDC/ATSDR *Social
Vulnerability Index (SVI)* measures.

**Problem statement:** how do presidential disaster declarations and recorded HMA obligations
align with county storm losses and social vulnerability, and where do recorded federal response
and recorded losses diverge most? Four measures are kept separate because each is observed at a
different stage:

1. **Declaration coverage**: a significant event-county pair falls inside a weather-related major
   disaster declaration that designates its county (or literally the whole state), with a
   compatible hazard and the event date inside the incident period (±1 day).
2. **Observed HMA subapplication activity**: the county is named as a benefiting area in a
   subapplication published in OpenFEMA, i.e. one that reached FEMA.
3. **Status**: the subapplication's current recorded OpenFEMA status, verbatim (e.g. "Submitted
   to FEMA", "Approved", "Obligated"), a snapshot rather than a final outcome.
4. **Recorded obligation**: FEMA's obligated amount in constant 2025 dollars, split equally across
   the benefiting areas a subapplication lists; statewide, out-of-state, unresolved and missing
   areas stay at state level.

DeclarationDenials records are government-level outcomes of presidential declaration requests.
They carry no county and are never treated as HMA decisions.

**Stakeholders:**
* State hazard mitigation officers and county emergency managers: a starting point for outreach
  and technical assistance within the state's own HMA process.
* FEMA regional staff, legislators and auditors: a transparent, stage-by-stage county measure of
  how declarations and recorded mitigation obligations track recorded need.
* Community organizations in highly vulnerable counties: reproducible evidence, with its limits
  stated.
* Researchers and insurers: a cleaned, integrated county panel.

## 2. Analytical questions

| # | Question | Main measure | Serving table |
|---|---|---|---|
| Q1 | **Exposure:** how is recorded storm damage per resident distributed across SVI quartiles, and which counties carry the most? | damage ÷ 2022 population | `serving.county_resilience_profile`, `serving.svi_quartile_summary` |
| Q2 | **Declaration coverage:** what share of *significant* storm event-county pairs fall inside a weather-related major disaster declaration that designates the same county (or is a literal statewide designation), has a compatible hazard type and covers the date? Does it differ by SVI, hazard type, FEMA region or state? Government-level context only: weather-related declaration requests turned down (DeclarationDenials; no county, not HMA decisions) | `covered_by_major_disaster` rate (unit: event-county pair); coverage is designation, not money | `serving.event_declaration_coverage`, `serving.declaration_request_outcomes` |
| Q3 | **Mitigation alignment, stage by stage:** by five-year fiscal-year period, program and hazard family, is a county's HMA activity (a) not observed, (b) observed without a positive obligation, or (c) obligated (the CT planning regions are a separate `geography_not_comparable` stage, excluded from all rates)? How do the stages, the verbatim status mix, total and weather-related (lower-bound) recorded obligation per resident vary with damage-burden quartile and SVI, all periods and period by period? Follow-up screen: top-quartile damage per resident, SVI Q4, recorded obligation per resident ≤ the median of HMA-comparable states + DC counties (not evidence that a county was overlooked or never applied) | `hma_activity_stage`, observed and positive-obligation subapplications, recorded obligation (nominal, 2025 $), `is_high_need_low_obligation` | `serving.county_resilience_profile`, `serving.hma_county_period_activity`, `serving.hma_county_program_hazard_activity`, `serving.hma_status_summary`, `serving.hma_stage_summary`, `serving.hma_damage_burden_summary`, `serving.svi_quartile_summary` |
| Q4 | **Timeliness:** how many days pass from incident start to the declaration request, and from incident start to a major disaster declaration, and how do these lags differ by SVI group, hazard family and FEMA region? Request to declaration is reported as an extra lag. Descriptive breakdowns with sample-size flags; no causal adjustment | median days incident start → request and incident start → declaration (with quartiles) | `serving.declaration_timeliness`, `serving.declaration_timeliness_summary` |
| Q5 | *Stretch:* flood-insurance gap (NFIP claims vs recorded flood damage) | — | not built yet |

The definitions are configurable in `config/profiles.yml`:

* Q2 counts **event-county pairs**: a storm event that touches three counties is three pairs.
* A pair is **significant** if its county-allocated damage is at least $100,000 in constant 2025
  dollars (`analysis.significant_damage_real_usd`), or the event caused at least one death or injury.
* A pair is **covered** if a weather-related major disaster (DR) declaration
  * designates that county, or is a **literal "Statewide"** designation for its state. FEMA's
    county code `000` alone does not mean statewide: 1,102 of the 1,122 code-`000` rows of
    2000–2025 are reservations, tribal statistical areas or Alaska Native villages. These are kept
    as **tribal** areas and never expanded to counties;
  * has a hazard family **compatible** with the event's (`seeds/hazard_match_rules.csv`: same
    family, or a companion hazard that declarations of that type routinely name, such as flooding
    under a "Severe Storm" declaration. Heat events, and cross-hazard pairs such as a wildfire
    under a flood declaration, never match);
  * covers the event date, allowing ±1 day. Open-ended incidents are capped at 60 days.
* **SVI quartiles** are fixed national cut points (0.25, 0.50, 0.75) on the SVI 2022 national
  percentile and cover the **50 states + DC**. CDC ranks Puerto Rico only within Puerto Rico and
  publishes no SVI for the island areas, so those are reported as separate *analysis universes*.
* **Dollars** are restated in constant **2025 dollars** with BLS CPI-U: event year for damage,
  federal fiscal year for HMA. The base year is set by `analysis.inflation.base_year`, and nominal
  values are kept too.
* **Weather hazards** are identified with `seeds/hazard_crosswalk.csv`. Non-weather declarations,
  such as the COVID-19 "Biological" declarations, are excluded.
* **HMA stages** (Q3) use county-mapped benefiting areas only. Each county, over all profile fiscal
  years and in each five-year period, is in exactly one stage:
  * `geography_not_comparable`: only the 9 current Connecticut planning regions. OpenFEMA HMA
    names the 8 legacy counties, so HMA is not attributed across the 2022 geography break (see
    below);
  * `not_observed`: no subapplication naming the county reached FEMA and was published
    ("no observed county-mapped subapplication"; never "did not apply");
  * `observed_no_positive_obligation`: at least one, none with a recorded `totalObligatedAmount`
    above zero;
  * `obligated`: at least one with a recorded positive obligation, **whatever its status**.
  Status is kept verbatim and never recoded: "Approved" appears only where OpenFEMA says
  "Approved".
* **Recorded obligation** = the county's equal share of positive `totalObligatedAmount` values
  (negative de-obligation records are kept in the facts but excluded from these totals). It is
  FEMA's obligation, not money received by a county government and not all disaster aid.
* **Five-year periods** are fixed federal fiscal-year periods FY2001–2005, …, FY2021–2025; FY2000
  forms a one-year partial period in the full profile. Program and hazard-family breakdowns list
  observed combinations only: absence is stated only at county × period and county × all-period
  grain, because programs existed in different years and a subapplication records one primary
  hazard.
* **Connecticut geography break.** Census Vintage 2025 and SVI 2022 use the 9 planning regions
  (09110–09190). The 2000–2025 sources use the 8 legacy counties (09001–09015): NOAA Storm
  Events, OpenFEMA disaster declarations and OpenFEMA HMA `benefitingCounties` all record
  Connecticut there. No defensible county-to-region crosswalk exists, so no legacy fact is ever
  crosswalked or allocated to a region. The regions keep their SVI and population
  (`analysis_universe = states_dc`), so they stay visible as current social geography, but:
  * `storm_declaration_geography_comparable = false`: their storm events, damage, casualties,
    significant-event coverage, major disasters and emergencies are **NULL (not comparable),
    never zero**, as are per-capita damage, coverage rate and damage percentile; they are
    excluded from damage ranks, damage-burden quartiles, the SVI-quartile damage and coverage
    numerators and denominators, and the follow-up screen;
  * `hma_geography_comparable = false`: HMA stage `geography_not_comparable` in the profile and
    in every period; left out of every HMA stage denominator, per-capita rank and median.

  Both flags come from one data-driven rule (a current unit of a state that also has legacy
  units). The legacy counties keep their storm, declaration and HMA records as
  `analysis_universe = legacy_geography`; their storm and declaration records are real
  pre-change history and may remain in national historical event totals. Only the
  current-geography HMA headline shares leave the legacy counties out. Critical DQ checks
  (`storm_declaration_geography_break_guard`, `hma_geography_break_guard`), triggered by facts on
  legacy geography rather than by the flags, fail if current units of such a state ever get
  ordinary historical metrics, ranks or HMA stages.
* **Follow-up screen** (`is_high_need_low_obligation`, states + DC only; units with comparable
  storm history and HMA geography only): top-quartile constant-dollar damage per resident,
  national SVI quartile 4, and constant-dollar recorded HMA obligation per resident at or below
  the median of HMA-comparable states + DC counties. A critical DQ check recomputes it
  independently. It is a screen for follow-up, not proof of neglect. `is_low_obligation` is
  computed within each analysis universe against that universe's own median (Puerto Rico
  against Puerto Rico); only states + DC enter the screen.
* **Damage-burden alignment** (`serving.hma_damage_burden_summary`): HMA stages and recorded
  obligation by quartile of constant-dollar damage per resident, ranked within each analysis
  universe over units with comparable storm history, HMA-comparable units only.
* **Weather-related obligation is a lower bound**: subapplications without a `primaryHazard`
  (`hazard_family = 'Not specified'`) cannot be classified; the report and dashboard query their
  number and county-attributed obligation.

Full definitions are in [`docs/implementation-plan.md`](docs/implementation-plan.md) §2.

## 3. Datasets, licences and terms

All sources are real, public and need no API key. Every fact below was verified live on
2026-11-08; volumes come from this pipeline's national run. Details, URLs and update cadence are in
[`docs/sources.md`](docs/sources.md).

| Provider · dataset | Format | Volume (2000–2025) | Role | Terms |
|---|---|---|---|---|
| **NOAA NCEI** Storm Events Database, `StormEvents_details` yearly files ([listing](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/)) | CSV.gz, structured | 26 files, 257 MB compressed, 1,592,849 events | Losses, deaths and injuries | U.S. government data; the README states no use restriction; cite NCEI, no endorsement implied |
| **NOAA NWS** County–Public Forecast Zones correlation files ([page](https://www.weather.gov/gis/ZoneCounty)) | Pipe-delimited, no header | **47 dated versions (2004–2026)**, 283,796 rows: 12 from NWS, 35 from SHA-1-verified Internet Archive copies of the NWS files | Places the 39 % of land events recorded by forecast zone, **as of the event date** | [NWS disclaimer](https://www.weather.gov/disclaimer): public domain, no implied endorsement |
| **FEMA OpenFEMA API**: DisasterDeclarationsSummaries v2, HmaSubapplications v2, DeclarationDenials v1 ([API docs](https://www.fema.gov/about/openfema/api)) | **JSON, semi-structured** | 48,963 + 54,638 + 352 records | Declaration coverage (Q2, Q4); HMA subapplications that reached FEMA, their status and recorded obligation (Q3); turned-down declaration requests (government-level Q2/Q4 context, not HMA decisions) | [OpenFEMA terms](https://www.fema.gov/about/openfema/terms-conditions): the attribution statement in [§17](#17-attribution) is mandatory |
| **CDC/ATSDR** Social Vulnerability Index 2022: U.S. county file and the stand-alone Puerto Rico file ([page](https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html)) | CSV | 3,144 counties + 78 municipios × 158 columns | Vulnerability (Puerto Rico ranked within PR) | U.S. government data; use CDC's suggested citation |
| **U.S. Census Bureau** Vintage 2025 county population ([page](https://www.census.gov/data/tables/time-series/demo/popest/2020s-counties-total.html)), Puerto Rico municipio estimates, and the 2020 county reference file | CSV / pipe-delimited | 3,195 + 546 + 3,235 rows | Population denominators and the canonical county key (5-digit FIPS) | Public domain; cite the Census Bureau |
| **U.S. Bureau of Labor Statistics** CPI-U `CUUR0000SA0` via the keyless Public Data API v1 ([series](https://data.bls.gov/timeseries/CUUR0000SA0)) | JSON | monthly 1990–2026 | Constant-dollar damage and recorded HMA obligation | Public domain; cite BLS and the retrieval date; the BLS statement in [§17](#17-attribution) is required |

The integration problems are real ones:
* Storm Events uses its own codes for territories (Puerto Rico = 99, but 72 in Census).
* Forecast zones are renumbered and redrawn over time. Versions before 2020 survive only as
  archive copies, and some periods have no surviving version.
* Connecticut replaced its 8 counties with 9 planning regions in 2022; NOAA storm events, FEMA
  declarations and HMA still name the old counties, so the regions keep SVI and population but
  have no comparable storm, declaration or HMA history (NULL, not zero; HMA stage
  `geography_not_comparable`).
* Two SVI ranking universes must never be mixed.
* The HMA `benefitingCounties` field is free text. See
[§11](#11-data-model-idempotency-provenance-and-data-quality) and
[`docs/data-quality.md`](docs/data-quality.md).

## 4. Architecture and directory map

```
 NOAA NCEI CSV.gz ─┐                          ┌──────────────── PostgreSQL 16 ───────────────┐
 NWS zone files ───┤   object store           │ raw      all-TEXT copies + lineage columns   │
 CDC SVI CSV ──────┼─► data/lake/raw/... ─────┼► staging typed, parsed, de-duplicated,       │
 Census CSV/pipe ──┤   (local dir or S3;      │          resolved to county FIPS (SQL)       │
 OpenFEMA JSON ────┘    bytes kept unchanged) │ curated  5 dimensions, 4 facts               │
                                              │ serving  14 analysis-ready tables            │──► HTML report
                                              │ ops      runs, objects + SHA-256, DQ log     │──► Streamlit dashboard
                                              └──────────────────────────────────────────────┘
```

**Storage choices (course requirement C.12):**
* Downloaded files and API pages are write-once artefacts, so they go to an **object store** as the
  system of record. Keys are S3-style, which makes moving to AWS S3 a configuration change.
* Structured data goes into **PostgreSQL** (required by the brief), with raw kept apart from cleaned
  data by schema.
* OpenFEMA JSON is also landed as **JSONB** so it can be flattened with SQL next to the tabular
  data. MongoDB was not chosen: every JSON record ends up joined to relational tables.

The full justification is in [`docs/architecture.md`](docs/architecture.md).

```
.
├── README.md                this file
├── Makefile                 task runner (make help)
├── pyproject.toml, uv.lock  package `resgap` and pinned dependencies
├── .python-version          3.12 (uv uses it)
├── .env.example             configuration template (copy to .env; .env is git-ignored)
├── docker-compose.yml       db (PostgreSQL 16) · s3 (SeaweedFS, optional) · pipeline (optional)
├── Dockerfile               image for the optional pipeline container
├── .github/workflows/ci.yml lint + tests + offline end-to-end run on every push
├── config/
│   ├── sources.yml          source registry: URLs, hosts, fingerprints, fallbacks, NWS versions
│   ├── profiles.yml         run profiles (sample/full/fixture), thresholds, CPI base year
│   └── dq_checks.yml        75 declarative data-quality checks
├── sql/                     executed in this order:
│   ├── init/                schemas raw, staging, curated, serving, ops + ops tables
│   ├── functions/           safe casts, damage parser, county/zone name keys, NWS file dates
│   ├── raw/                 JSONB landing tables (other raw tables are created from file headers)
│   ├── staging/01–09        typed sources, versioned crosswalk, date-effective zone mapping, CPI
│   ├── curated/10–23        dimensions (analysis universes, FEMA regions, hazards, inflation, HMA periods) and facts
│   └── serving/30–42        tables that answer Q1–Q4 (Q3 HMA stage tables 38–42) + request outcomes, mapping coverage, CPI
├── seeds/                   team-authored mappings: hazard crosswalk and match rules, FEMA
│                            regions, territory codes, aliases
├── src/resgap/
│   ├── cli.py               `resgap` command
│   ├── ingest/              one module per source + raw loader (rawload.py) + shared context
│   ├── sourcecheck.py       health checks / preflight (redirects, schema fingerprints, fallbacks)
│   ├── dq/runner.py         runs config/dq_checks.yml, logs to ops.dq_results
│   ├── transform.py         loads seeds, runs sql/ layers
│   ├── report/build.py      HTML report (inline SVG charts)
│   ├── objectstore.py       local and S3 backends
│   ├── db.py, http.py, config.py
├── dashboard/app.py         Streamlit dashboard over serving tables
├── tests/                   unit/ (no DB), integration/ (disposable DB), fixtures/ (real excerpts)
├── scripts/                 build_fixtures.py, render_proposal.py
├── docs/                    decision record, plan, sources, contracts, DQ log, runbook, proposal
├── reference/               course brief (project-instructions.pdf)
├── data/                    object store (git-ignored, created on first run)
└── reports/                 generated report (git-ignored)
```

## 5. Quick start from a fresh clone

**Prerequisites**

| Tool | Why | Check |
|---|---|---|
| [uv](https://docs.astral.sh/uv/getting-started/installation/) (tested with 0.11) | Python environment; installs Python 3.12 automatically if missing | `uv --version` |
| GNU Make | task runner (preinstalled on macOS/Linux; on Windows use WSL, or the `uv run` commands in §7) | `make --version` |
| Docker Desktop, running | PostgreSQL 16 container (or see §6.1 for a local PostgreSQL) | `docker info` |
| Internet access | live runs only; the offline demo and tests need none | — |
| Google Chrome or Chromium | optional, only to render the proposal PDF | — |

**From zero to a report** (copy and paste; each step lists the expected result):

```bash
git clone https://github.com/nbiswal-star/resilience-gap.git && cd resilience-gap
make setup          # creates .venv with Python 3.12 and installs resgap + dev tools
make up             # creates .env from .env.example, starts PostgreSQL on localhost:5433, waits until ready
make offline-demo   # whole pipeline on committed real excerpts, no network, a few seconds
                    #   expect: "report written to .../reports/resilience_gap_report.html", exit code 0
make test           # creates resgap_test once, then runs every test; expect no failures
make source-check   # optional: every source reachable? expect "all 11 source checks usable"
make run            # live "sample" run: ~60 MB download, 2.5–3 min on first run, no critical DQ failure
open reports/resilience_gap_report.html     # macOS; Linux: xdg-open …; Windows: start …
make dashboard      # optional interactive view on http://localhost:8501 (Ctrl+C to stop)
```

The offline demo loads Pennsylvania excerpts into the same database. The next `make run` notices
that the checksums differ and reloads the full files automatically, so no cleanup is needed (we
verified this; see [`docs/verification.md`](docs/verification.md)). The reverse order mixes data:
an offline demo run *after* a live run replaces the county reference and other single files with
excerpts while older live storm years stay loaded, so three warn-level checks
(`storm_county_fips_not_in_reference`, `declarations_unresolved_area`,
`hma_unresolved_county_names`) fire until the next live run. To demo without touching live data,
use a second database: `docker compose exec db createdb -U resgap resgap_demo`, then
`RESGAP_DATABASE_URL=postgresql://resgap:resgap@localhost:5433/resgap_demo make offline-demo`.

## 6. Running the pipeline

### 6.1 Database: team AWS RDS, Docker, or a local PostgreSQL

**Team AWS RDS:** the shared `resgap` database is hosted on PostgreSQL 18.3 at
`de-postgres.cn2o2ayyyx65.us-east-2.rds.amazonaws.com:5432` in `us-east-2`. TLS is required. Keep
the password out of `.env`, shell history and Git.

Each teammate who uses the shared RDS database must complete these access steps:

1. Ask the project AWS owner to allow the teammate's current public IP on TCP port 5432. Find the
   IP with `curl -4 https://checkip.amazonaws.com`; the AWS rule must use that address with `/32`.
   Repeat this step when the public IP changes.
2. Get the RDS password from the project owner through a private channel. Do not put it in Git,
   chat logs, screenshots or a committed `.env` file.

Clone and configure the shared path. This password prompt works in both Bash and zsh:

```bash
git clone https://github.com/nbiswal-star/resilience-gap.git
cd resilience-gap
make setup

RESGAP_DB_PASSWORD="$(uv run python -c 'import getpass; print(getpass.getpass("RDS password: "))')"
export RESGAP_DATABASE_URL="postgresql://postgres:${RESGAP_DB_PASSWORD}@de-postgres.cn2o2ayyyx65.us-east-2.rds.amazonaws.com:5432/resgap?sslmode=require"
unset RESGAP_DB_PASSWORD

make status                 # confirms the remote database is reachable
make report                 # reads RDS and writes a report on this laptop
make dashboard              # reads RDS and starts the local dashboard
```

Do not run `make up` for this path; that target starts the local Docker database. Keep integration
tests on the disposable local `resgap_test` database because the test suite drops and rebuilds its
pipeline schemas. The shared RDS login lets all teammates read the same results. Dropbox is not
needed for `status`, `report` or `dashboard`.

All teammates must have at least read access to the Dropbox folder so they can inspect the shared
raw files. Only the teammate who runs shared ingestion needs edit access and a locally synced copy.
That teammate must accept the `resgap-lake` folder invitation, make the folder available offline,
and then run:

```bash
export RESGAP_OBJECT_STORE=local
export RESGAP_LAKE_DIR="$HOME/Library/CloudStorage/Dropbox/resgap-lake"
make run PROFILE=sample     # shared Dropbox raw layer -> hosted PostgreSQL
```

The Dropbox website URL for one account is not a team invitation or share link. Coordinate before
running this command: it changes shared tables and Dropbox files, so only one teammate should run
the shared pipeline at a time. For independent development and tests, use the Docker path below;
it gives each teammate a separate database and does not change RDS.

**Docker:** run `make up`. This starts `postgres:16-alpine`, publishes it on **127.0.0.1:5433**
(loopback only, so it is not reachable from the network; 5433 avoids clashing with a local 5432),
and stores data in the `resgap_pgdata` volume. Credentials come
from `.env` (`resgap` / `resgap` / database `resgap`).

**Without Docker:** use a local PostgreSQL. Version 16 is what we test (locally and in CI); other
recent versions (14+) should work but have not been tested. The database owner needs no superuser rights; we
verified the pipeline and the full test suite as a plain owner without CREATEDB. Install PostgreSQL in one
of these ways:

* macOS: `brew install postgresql@16 && brew services start postgresql@16`
* Ubuntu: `sudo apt install postgresql` (prefix the `psql` commands below with `sudo -u postgres`)
* Postgres.app

Then:

```bash
psql -d postgres -c "create role resgap login password 'resgap';"
psql -d postgres -c "create database resgap owner resgap;"
psql -d postgres -c "create database resgap_test owner resgap;"
make env                                   # creates .env
# edit .env:  RESGAP_DATABASE_URL=postgresql://resgap:resgap@localhost:5432/resgap
make offline-demo
make test TEST_DB=postgresql://resgap:resgap@localhost:5432/resgap_test
```

Skip `make up` and `make psql` in this setup (they use Docker). Connect with
`psql postgresql://resgap:resgap@localhost:5432/resgap` instead.

### 6.2 Profiles

The `--profile` option is accepted before or after the command. Make uses `PROFILE=...`.

| Profile | Scope | Download | Measured on a laptop (2026-11-08) | Use for |
|---|---|---|---|---|
| `fixture` | Pennsylvania, Puerto Rico, U.S. Virgin Islands; 2024; from `tests/fixtures/` (offline) | none | a few seconds end to end; 789 storm rows | tests, CI, demos without network |
| `sample` *(default)* | PA, OH, WV, NY, NJ, MD, DE, VA; 2022–2024 | 60 MB (58 objects) | first run 149 s and 184 s end to end in two clean-lake runs (most of it is the 47 crosswalk versions, 35 from the archive at ≥ 1 s spacing); re-run with unchanged sources 51 s; transform ~9 s; 215,281 storm rows; 0.49 GB database | everyday development, Milestone 2 video |
| `full` | all states and territories; 2000–2025 | 299 MB (NOAA 258 MB, crosswalks 20 MB, OpenFEMA 17 MB) | ingest: NOAA + OpenFEMA 97 s, crosswalks/PR/CPI sources 3 min 13 s; transform ~62 s; DQ ~17 s (75 checks; no-download rebuild of the current code, 2026-11-08); 1.59 M storm rows; **3.3 GB database**; ~0.6 GB peak RAM | final analysis |

Live times depend on the network. For the full run, keep at least 4 GB free for the Docker volume
plus about 0.3 GB for `data/lake`. BLS's keyless API allows 25 queries a day, and each query
covers one decade: a full run uses 4 (1990s–2020s, because fiscal year 2000 starts in October
1999), the sample and fixture profiles 1 each.

### 6.3 Typical runs

```bash
make offline-demo                    # fixture profile, no network
make run                             # sample profile, live
make run PROFILE=full                # national 2000–2025, live (same as: uv run resgap --profile full run)
uv run resgap ingest openfema        # refresh one source (sample profile unless --profile is given)
uv run resgap ingest noaa_storm_events --force    # re-download and reload even if unchanged
make transform && make dq && make report          # rebuild everything downstream after editing SQL
make status                          # recent runs and table sizes
```

`run` executes `init-db → ingest (all 9 ingest sources, covering the 6 provider datasets in §3) →
DQ(raw) → transform → DQ(staging, curated, serving) → report`. A source that cannot be reached
is reported as `SOURCE UNAVAILABLE: <source>`. The other sources still run, and anything already
loaded is kept. (`source-check` reports 11 checks because each OpenFEMA dataset is probed
separately.)

## 7. Command reference

### Make targets (`make help`)

| Target | Runs | Notes |
|---|---|---|
| `setup` | `uv sync --extra dev` | first time and after dependency changes |
| `env` | copy `.env.example` → `.env` | never overwrites |
| `up` / `down` | `docker compose up -d db` (+ wait) / `docker compose down` | `down` keeps the data volume |
| `test-db` | create `resgap_test` in the container | called by `make test`; prints a hint if Docker is not running |
| `init-db` | `resgap init-db` | also run automatically by `ingest` and `run` |
| `ingest` | `resgap --profile $(PROFILE) ingest` | idempotent |
| `transform` | `resgap --profile $(PROFILE) transform` | re-applies `init-db` (idempotent), sets the active profile, rebuilds staging → serving |
| `dq` | `resgap dq` | checks the tables of the **last transform** |
| `report` | `resgap report` | report of the **last transform** |
| `run` | `resgap --profile $(PROFILE) run` | whole pipeline |
| `offline-demo` | `resgap --profile fixture run --offline-dir tests/fixtures` | no network |
| `status` | `resgap status` | last 25 steps and approximate row counts |
| `source-check` | `resgap source-check --json reports/source_check.json` | probes all sources (no database, no large downloads); exit 1 if any source is unusable or its schema changed |
| `test` / `test-unit` | `pytest -q` with `RESGAP_TEST_DATABASE_URL=$(TEST_DB)` / `pytest tests/unit -q` | see §9 |
| `lint` / `format` | `ruff check` / `ruff format` on `src tests scripts dashboard` | |
| `dashboard` | `uv run --extra dashboard streamlit run dashboard/app.py` | installs Streamlit and pandas on first use |
| `proposal` | `python scripts/render_proposal.py` | Markdown → HTML → PDF (needs Chrome/Chromium) |
| `fixtures` | `python scripts/build_fixtures.py` | **network**; overwrites `tests/fixtures` with fresh real excerpts (if the agencies have not revised the data, only OpenFEMA's `metadata.rundate` changes) |
| `psql` | `docker compose exec db psql -U resgap -d resgap` | Docker only |
| `clean` | remove caches and `reports/resilience_gap_report.html` | keeps data and database |
| `clean-lake` | **delete** `data/lake` | files are downloaded again on the next run |

Make variables: `PROFILE` (default `sample`), `TEST_DB` (default
`postgresql://resgap:resgap@localhost:5433/resgap_test`). Make passes `--profile` explicitly, so
`RESGAP_PROFILE` in `.env` only affects direct `uv run resgap` calls.

### CLI (`uv run resgap --help`)

```
resgap [--profile sample|full|fixture] [-v] <command> [options]

  init-db                              create schemas, ops tables, SQL helper functions
  ingest [SOURCE ...] [--force] [--offline-dir DIR] [--no-preflight]
                                       SOURCE ∈ census_county_reference census_popest
                                                census_popest_pr cdc_svi cdc_svi_pr bls_cpi
                                                nws_zone_county noaa_storm_events openfema
  transform                            staging → curated → serving (SQL)
  dq [--layers raw staging curated serving]
  report [--out DIR]                   default ./reports
  run [--force] [--offline-dir DIR] [--out DIR] [--no-preflight]
  status
  source-check [SOURCE ...] [--json FILE]   health check; needs no database
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | database unreachable, or ingestion failed for a named source |
| `2` | critical data-quality failure, or invalid command-line usage |
| `3` | a SQL step failed; the message is in `ops.pipeline_runs` |

`--offline-dir DIR` reads real files from a directory laid out like `tests/fixtures/`:
`DIR/<source>/<file>`, OpenFEMA as `DIR/openfema/<Dataset>/page-*.json`, BLS as
`DIR/bls_cpi/<series>_<decade>.json`, and archive-tier crosswalks under
`DIR/nws_zone_county/internet_archive/`.

**Preflight:** before downloading, `ingest`/`run` check each source the way `source-check` does.
* A redirect to another host, an HTML page instead of data, or a changed header stops that source
  (`SOURCE UNAVAILABLE`).
* JSON sources only warn on added fields.
* The BLS check uses HEAD, so it does not spend one of BLS's 25 daily keyless queries.
* `--no-preflight` skips the check.

## 8. Report and dashboard

* **HTML report:** `reports/resilience_gap_report.html`, written by `make run`, `make offline-demo`
  or `make report`. It contains:
  * headline tiles (constant 2025 dollars, nominal alongside): storm **event-county records**
    (an event counts once per county it touched, not distinct storm events), damage and
    declaration coverage over units with comparable history (including the legacy CT counties'
    pre-change records, excluding the CT planning regions), and the current-geography HMA shares
    over HMA-comparable units of the states, DC and Puerto Rico (island areas, CT planning
    regions and legacy CT counties stated separately); a note explaining the HMA headline scope,
    why the all-period observed and positive-obligation shares are close, and the positive,
    county-attributed, state-level and net HMA totals (queried); the four measures, the central
    limitation and the Connecticut geography break;
  * Q1–Q4 charts as inline SVG, each with hover tooltips and a data table (national quartiles,
    states + DC);
  * Q2 coverage by hazard family, by FEMA region and by state, next to the weather-related major
    disaster requests declared and turned down (OpenFEMA DeclarationDenials, kept strictly
    separate from HMA);
  * Q3 by stage: share of HMA-comparable counties with an observed county-mapped subapplication
    and share with a recorded positive obligation by SVI quartile; stage by five-year period and
    SVI quartile (states + DC), the quartiles combined, and the other universes by period; stage
    and obligation by damage-burden quartile; recorded obligation per resident (total and
    weather-related, with the lower-bound note and the state-year scope note); program and
    hazard-family breakdowns of observed activity; and the verbatim status mix;
  * Q4 lags by SVI group, hazard family and FEMA region: incident start → request and incident
    start → declaration for every group (request → declaration as an extra column), with
    quartiles, the most common hazard in each group and a sample-size note;
  * the high-need, low-obligation follow-up screen, labelled "top 20 of N" when N > 20, with each
    county's HMA stage;
  * Puerto Rico, island areas and legacy geography as separate universes;
  * how storm events were placed in counties, by year (mapping rule, archive versions, unmapped);
  * the CPI-U factors used for each year;
  * the latest data-quality results;
  * provenance per source (objects, bytes, retrieval time);
  * source attribution.

  It is a single self-contained file that can be opened offline or attached to a submission.
* **Dashboard:** `make dashboard`, then open `http://localhost:8501`. It has a state filter,
  headline metrics, the central limitation, and damage and recorded HMA obligation per resident
  (2025 $) by national SVI quartile. It also has the Q3 stage shares and counts by quartile, a
  period × SVI-group view for each analysis universe (selectable, never pooled), stage by
  damage-burden quartile, program and hazard-family tables (observed activity only), the
  verbatim status mix, the full follow-up list for the selected states, Q2 coverage by state
  and by FEMA region with turned-down requests, Q4 incident start → request and incident start
  → declaration by SVI group, hazard family or FEMA region, and a sortable county table with
  each row's `analysis_universe`, storm/declaration and HMA comparability, damage rank and HMA
  stage (non-comparable history shown blank, not zero, and sorted last). It reads
  `RESGAP_DATABASE_URL` from `.env` and shows the last transformed profile.
* **Other tools:** Tableau, Power BI, Excel or Python can connect directly to the `serving` schema
  (see §11).

## 9. Tests, lint and CI

| Command | Needs | Expected result |
|---|---|---|
| `make lint` | — | `All checks passed!` |
| `make test-unit` | — | all unit tests pass |
| `make test` | PostgreSQL reachable at `TEST_DB` | all tests pass |
| `uv run --extra dev pytest -q` without `RESGAP_TEST_DATABASE_URL` | — | unit tests pass, integration tests skip |

**Current code (storm/declaration geography break, 75 DQ checks, 175 tests), executed locally on
2026-11-08:**
* `make lint` passed;
* `ruff format --check src tests scripts dashboard` passed with 41 files already formatted (no
  formatting needed; a separate `make format` attempt stopped on a sandbox uv-cache permission
  error, an environment restriction rather than a product failure);
* `make test` gave **175 passed** in 12.35 s;
* `make offline-demo` exited 0 with **75/75** DQ checks passing and the report written;
* the national rebuild without download (`--profile full transform`) exited 0 in about 62 s,
  national DQ passed **75/75** checks in about 17 s, and the national report was written;
* the dashboard `AppTest` ran against the fixture and the national database: each exit 0,
  0 exceptions, 5 metrics, 12 dataframes.

The live sample run, `make source-check` and the S3 (SeaweedFS) run were not repeated on this
code. Details are in [`docs/verification.md`](docs/verification.md).

Earlier counts are *historical*: 166 tests and 71/71 DQ checks with the HMA-only Connecticut fix;
155 tests (63 unit + 92 integration) and 64/64 DQ checks for the HMA funnel before the
Connecticut fix; 120 tests (62 unit + 58 integration) and 51 checks before the funnel.

**Unit tests** also check:
* that every schema fingerprint in `config/sources.yml` matches the real committed headers;
* NWS version planning (exclusions, archive tier, digest-verified fallbacks);
* the OpenFEMA bulk-fallback scope filter;
* BLS response parsing (real response, missing October 2025, throttling errors);
* redirect validation;
* source-check drift and foreign-redirect detection;
* the real OpenFEMA code-`000` example (literal statewide vs tribal rows), the hazard match rules,
  the FEMA region seed, and the guard that refuses a test database equal to the pipeline's.

They cover configuration, CLI argument parsing, the DQ rule engine, the NOAA listing parser, the NWS link
parser, header sanitising, OpenFEMA filtering, paging and hashing, and the local and S3 object
stores (S3 runs against an in-process mock, so no credentials are needed).

**Integration tests** run the real pipeline on the fixtures and check:
* the SQL helper functions;
* raw-row fidelity, checksums and lineage, and that offline copies are namespaced;
* that all 67 Pennsylvania counties reach the serving layer;
* that Tropical Storm Debby events match declaration DR-4815-PA through an explicit hazard rule;
* on real OpenFEMA records: only literal "Statewide" rows are statewide; reservations, OTSAs and
  Alaska Native villages coded `000` stay tribal and never cover a county; the critical DQ checks
  fail if the old "`000` = statewide" rule is reintroduced;
* the constant-dollar significance rule and hazard compatibility (labelled test vectors);
* the Q4 breakdowns, FEMA regions and the real fixture denial record;
* that dollar totals reconcile across layers;
* the analysis universes (PA national SVI; 78 PR municipios ranked within PR; VI without SVI);
* the HMA funnel on real fixture records: stages (Aibonito observed without a positive
  obligation, Humacao obligated, Adjuntas and VI not observed), statuses preserved verbatim,
  fixed fiscal-year periods, period / program × hazard / profile / status reconciliation, the
  exact follow-up rule recomputed in Python, and mutations (a recoded status, a wrong stage, a
  missing county-period, a shifted amount, a flipped flag, a changed raw obligation amount, a
  county moved between stages in the stage or damage-burden summary, a legacy unit with HMA
  records next to ordinary current units, a shifted state-year amount, a flipped Puerto Rico
  `is_low_obligation`) that each funnel DQ check must catch;
* the Connecticut geography rule, simulated on fixture data because the fixture has no
  Connecticut: one county marked HMA-non-comparable leaves every HMA denominator, rank, median
  and the follow-up screen and stays non-comparable in every period; and, end to end, a legacy
  Pennsylvania county holding a real storm fact makes every current PA unit
  storm/declaration-non-comparable through the data-driven rule, with NULL (not zero) history,
  no damage rank, no screen and kept SVI and population, all critical checks passing, and
  disguised false-zero histories caught by the guard and the recounts;
* constant dollars against the real BLS values;
* that real PA events use the crosswalk version valid on their date;
* the date-effective mapping rules on labelled test vectors (every rule and unmapped reason);
* that there are no critical DQ failures;
* idempotency: a re-run reports `unchanged` and produces an identical serving table.

> Warning: Integration tests **drop and recreate** the pipeline schemas in the database named by
> `RESGAP_TEST_DATABASE_URL`. Never point it at a database you care about. As a guard, the tests
> stop before touching anything if that URL names the same database as `RESGAP_DATABASE_URL`
> (environment or `.env`; host aliases such as `localhost`/`127.0.0.1` and the live server
> identity are compared).

**CI** (`.github/workflows/ci.yml`) runs on every push and pull request on Ubuntu with a
`postgres:16-alpine` service container. It lints, runs all tests, runs the offline end-to-end
pipeline, and uploads the report as a build artifact. GitHub Actions runs
[`42492181411`](https://github.com/anitksahu/resilience-gap/actions/runs/42492181411) (commit
`9525762`) and [`42492264285`](https://github.com/anitksahu/resilience-gap/actions/runs/42492264285)
(commit `1619c88`) both succeeded on 2026-11-08 with the 90 tests of that time. The current
implementation passed [run `42512681303`](https://github.com/anitksahu/resilience-gap/actions/runs/42512681303)
at commit `1ba38a2` with lint, all 175 tests and the 75-check offline pipeline. Full evidence is in
[`docs/verification.md`](docs/verification.md).

## 10. Configuration and object storage

**Environment variables:** copy `.env.example` to `.env`. `.env` is git-ignored; never commit
credentials. Real environment variables override `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `RESGAP_DATABASE_URL` | `postgresql://resgap:resgap@localhost:5433/resgap` | pipeline database |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `resgap` | used by `docker compose` to create the container database |
| `RESGAP_PROFILE` | `sample` | default profile for direct CLI calls |
| `RESGAP_OBJECT_STORE` | `local` | `local` or `s3` |
| `RESGAP_LAKE_DIR` | `./data/lake` | local object-store root |
| `RESGAP_S3_BUCKET` / `RESGAP_S3_PREFIX` / `RESGAP_S3_ENDPOINT_URL` | `resgap-lake` / empty / empty | S3 target (endpoint empty = AWS) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | empty / empty / `us-east-1` | standard boto3 credentials |
| `RESGAP_HTTP_TIMEOUT_SECONDS` | `120` | per-request timeout (with retries and backoff) |
| `RESGAP_HTTP_USER_AGENT` | `resgap-pipeline/0.1 (95-815 course project)` | sent to every source |
| `RESGAP_TEST_DATABASE_URL` | unset | tests only: a disposable database |

**YAML configuration:**
* `config/profiles.yml`: profiles, analysis thresholds and `analysis.inflation.base_year`
  (default 2025; change it and run `make transform` to restate every real-dollar column).
* `config/sources.yml`: URLs, allowed redirect hosts, schema fingerprints, fallbacks, OpenFEMA
  versions, the NWS version registry (hosted, excluded, SHA-1-verified archive captures) and terms.
* `config/dq_checks.yml`: data-quality checks.

**Object storage.** The keys mirror S3. Examples:
* `raw/noaa_storm_events/details/year=2024/StormEvents_details-ftp_v1.0_d2024_c20260728.csv.gz`
* `raw/openfema/HmaSubapplications/v2/scope=<id>/snapshot=<UTC time>/page-00001.json.gz`
* `offline/raw/...` for fixture copies

**Team raw-data store: shared Dropbox folder.** The raw files and JSON pages live in the team's
shared Dropbox folder **`resgap-lake/`**, so the raw layer is not tied to one laptop. The teammate
who runs shared ingestion must have edit access; all other teammates need at least read access.
It holds the `raw/` tree above. To run ingestion, add the folder to your Dropbox desktop app, make
it available offline, and point the pipeline at your synced copy:

```bash
# .env  (macOS path shown; use wherever Dropbox syncs on your machine)
RESGAP_OBJECT_STORE=local
RESGAP_LAKE_DIR=~/Library/CloudStorage/Dropbox/resgap-lake
```

Every run then writes its downloads into the shared folder, so the team always has the exact
source bytes in one place. Downloads are reused only when the same database recorded them, so a
first run against a new database fetches the sources again and refreshes the shared copies
(same keys). Without the desktop app, browse or download `raw/` on dropbox.com.

To use S3 instead of the local directory:

```bash
uv sync --extra dev --extra s3
# .env
RESGAP_OBJECT_STORE=s3
RESGAP_S3_BUCKET=resgap-lake
RESGAP_S3_ENDPOINT_URL=                  # empty for AWS S3; http://localhost:8333 for local SeaweedFS
AWS_ACCESS_KEY_ID=...                    # any non-empty value for local SeaweedFS
AWS_SECRET_ACCESS_KEY=...
# local S3-compatible server (optional):
docker compose --profile s3 up -d s3
uv run --extra s3 python -c "import boto3; boto3.client('s3', endpoint_url='http://localhost:8333').create_bucket(Bucket='resgap-lake')"
```

The S3 backend is covered by the mocked unit test. It was also verified against a live SeaweedFS
4.52 container: the fixture pipeline passed all 51 DQ checks of that time and wrote its raw
objects to the bucket (see [`docs/verification.md`](docs/verification.md)). That S3 run has not
been repeated since; the check set has since grown to 75 (13 added with the HMA funnel, 7 in the
final review, 4 with the storm/declaration geography break). AWS S3 has not been tried because that requires account credentials. We use SeaweedFS
(`chrislusf/seaweedfs:4.52`) because the `minio/minio` image could not be pulled from Docker Hub
when checked.

SeaweedFS is published on `127.0.0.1:8333` only.

**Containerised pipeline (optional):** `docker compose --profile app run --rm pipeline resgap run`
builds the `Dockerfile` image and runs against the `db` service. It mounts `./data` and
`./reports`. The image build and a containerised `resgap status` run against the healthy
PostgreSQL service were verified on 2026-11-08.

## 11. Data model, idempotency, provenance and data quality

**Layers** (one PostgreSQL schema each):

| Layer | Contents | Rules |
|---|---|---|
| `raw` | `noaa_storm_events_details`, `nws_zone_county` (one partition per crosswalk version), `cdc_svi_county` (U.S. + PR partitions), `census_popest_county`, `census_popest_pr_municipio`, `census_county_reference` (all TEXT, created from file headers); `openfema_records`, `bls_cpi_records` (JSONB) | no keys or constraints; lineage columns `_partition_key`, `_object_key`, `_source_row`, `_run_id`, `_loaded_at`; malformed rows go to `ops.raw_rejects` instead of being dropped |
| `staging` | `county_ref`, `svi_county`, `storm_events`, `nws_zone_snapshot`, `nws_zone_county_version`, `storm_event_zone_resolution`, `storm_event_county` (bridge), `fema_declarations`, `hma_subapplications`, `hma_county_bridge`, `fema_declaration_denials`, `cpi_monthly`, `cpi_annual`, `cpi_fiscal_year`, `seed_*` | typed with safe casts; damage strings such as `1.50M` parsed; `-999` → NULL; de-duplicated; JSON flattened; resolved to county FIPS, or kept as statewide/tribal/unresolved areas |
| `curated` | `dim_county` (Census + population + SVI + `analysis_universe` + `fema_region`), `dim_hazard`, `dim_hazard_match`, `dim_inflation`, `dim_hma_period` (fixed five-year fiscal-year periods clipped to the profile), `fact_storm_event_county`, `fact_declaration_area`, `fact_hma_allocation` (subapplication × benefiting area: verbatim `status`, `obligation_amount_class`, `has_positive_obligation`, `is_weather`, `period_start_fy`), `fact_declaration_denial` | primary and foreign keys; dollars allocated so totals reconcile; nominal and `*_real_usd` columns |
| `serving` | `county_resilience_profile` (county), `event_declaration_coverage` (event-county pair), `declaration_timeliness` (disaster), `declaration_timeliness_summary` (breakdown × group), `declaration_request_outcomes` (state × year × type × hazard), `state_year_summary` (state × year), `svi_quartile_summary` (universe × SVI group), `hma_county_period_activity` (county × five-year period, dense), `hma_county_program_hazard_activity` (county × period × program × hazard family, observed only), `hma_status_summary` (period × program × verbatim status × county mapping), `hma_stage_summary` (universe × SVI group × period), `hma_damage_burden_summary` (universe × damage-burden quartile), `zone_mapping_coverage` (year), `inflation_reference` (year) | documented grain and keys (also as PostgreSQL table comments); see [`docs/data-contracts.md`](docs/data-contracts.md) |
| `ops` | `pipeline_runs`, `source_objects`, `raw_loads`, `raw_rejects`, `dq_results`, `active_profile` | metadata for runs, provenance and quality |

**Entity resolution, in short:**

| Link | How it is resolved |
|---|---|
| NOAA county events | `STATE_FIPS` + `CZ_FIPS`, after mapping territory codes |
| NOAA zone events | **Date-effective**: the crosswalk version valid on the event date. Inside a documented version gap, or when the nearest version is used, the zone name must also agree; otherwise the event stays unmapped with a reason, and no county is guessed. Split equally across the zone's counties |
| Marine events | excluded |
| FEMA declarations | state + county FIPS → `county`; `designatedArea` literally "Statewide" → `statewide`; other code-`000` areas (reservations, tribal statistical areas, Alaska Native villages) → `tribal`, kept with their name and never expanded to counties; retired codes → `unresolved` |
| HMA county lists | split the free text → normalised name key (`St. Mary's County` = `SAINT MARYS`; accents folded, so `Mayaguez` = `Mayagüez`) → match within the applicant's state → alias seed; out-of-state names are classified, not guessed |
| Hazard types | three vocabularies (NOAA, FEMA, HMA) → one `hazard_family`; explicit compatibility rules between declaration and event families |
| Events ↔ declarations | same county or literal statewide declaration, compatible hazard family, within the incident window |
| FEMA regions | state → region seed, checked against the `region` field of every declaration and denial record (critical) |
| SVI universes | U.S. file → national percentile and quartiles (states + DC); CDC's Puerto Rico file → within-PR rank in separate columns; island areas → no SVI |
| Dollars | CPI-U annual (calendar) or October–September mean (fiscal) → factor to the base year |

**Idempotency and provenance:**
* Every downloaded object's SHA-256 is recorded in `ops.source_objects` along with its URL and
  retrieval time.
* A raw partition (a NOAA year, a NWS file version, an OpenFEMA dataset scope) is replaced in one
  transaction, and only when its content hash changes.
* OpenFEMA snapshots are hashed over the records themselves, not the response envelope, and are
  rejected if the record count differs from the API's count.
* Transformations are deterministic full rebuilds.
* Re-running with unchanged sources logs `unchanged` and leaves the serving tables byte-identical,
  which the tests assert.

**Data quality:** 75 checks in `config/dq_checks.yml`, each with a layer, a severity, a SQL
test, a threshold and a written handling decision. All 75 passed on 2026-11-08 on the fixture
(`make offline-demo`) and on the national 2000–2025 data. Among them:
* "statewide only when literally Statewide", no expansion of tribal/statewide areas to counties,
  and Q2 coverage only through county or literal statewide designations with compatible hazards
  (critical);
* date-effective mapping correctness and name verification (critical);
* SVI universe separation (critical);
* CPI coverage and real-dollar reconciliation (critical);
* the HMA funnel (critical): raw status preserved verbatim, positive-obligation flag equal to the
  source amount, fixed period boundaries, stages exclusive and exhaustive, a complete county ×
  period grid, period / program × hazard / profile / status-summary / stage-summary
  reconciliation of counts and amounts (the stage summary and the damage-burden summary are
  recounted from the curated fact, not from other serving tables), and the follow-up screen
  recomputed independently;
* the Connecticut geography break: critical guards triggered by facts on legacy geography (no
  current unit of such a state may carry ordinary storm/declaration history, a damage rank, a
  screen result or an ordinary HMA stage), and an independent recount of the SVI-summary damage
  and coverage columns with comparability re-derived from `analysis_universe` and state, so a
  false-zero history in a numerator or a population denominator fails;
* the state-year scope (critical: state-year rows + excluded subapplications = the curated
  fact).

| Severity | Effect |
|---|---|
| `critical` | stops the run (exit 2) |
| `warn` | logged; must stay under its threshold |
| `info` | logged; expected characteristic |

Results are written to `ops.dq_results`, with samples of offending rows, and appear in the report.
The issue register and the decisions taken are in [`docs/data-quality.md`](docs/data-quality.md).
Examples:
* 28 % of storm events carry no damage estimate.
* 52 negative HMA obligations.
* COVID-19 declarations are excluded as non-weather.
* 1,102 tribal-area designations coded `000` are kept as tribal areas, not statewide.
* October 2025 CPI was not published.
* 32 malformed lines in archived NWS files are kept in `ops.raw_rejects`.

To inspect: `select check_name, status, failing_rows, total_rows, sample from ops.dq_results order
by id desc limit 75;`

## 12. Troubleshooting and cleanup

| Symptom | Fix |
|---|---|
| `Cannot connect to PostgreSQL …` (exit 1) | `make up` and check `docker info`; or point `RESGAP_DATABASE_URL` at your local server (§6.1) |
| `make up` fails: port 5433 already in use | stop the other service, or change the host port in `docker-compose.yml` and in `RESGAP_DATABASE_URL`/`TEST_DB` |
| `make test` shows integration tests as skipped | `RESGAP_TEST_DATABASE_URL` is unset or the database is unreachable; use `make test` or set `TEST_DB=...` |
| `resgap: error: unrecognized arguments` | check the spelling of the command and options in §7 |
| `SOURCE UNAVAILABLE: <source>` | the agency site is down or changed; retry later, or use the documented fallbacks in [`docs/runbook.md`](docs/runbook.md) (e.g. OpenFEMA bulk JSON with `--offline-dir`) |
| `schema drift at <source>` (preflight) or `SchemaDriftError` | the agency changed a file header: run `resgap source-check <source>`, review the contract, update the staging SQL, `drop table raw.<table>`, re-ingest |
| `BLS API status REQUEST_NOT_PROCESSED` | BLS's 25-query daily limit for keyless use is reached; wait a day (stored responses are reused) |
| `make source-check` reports `UNAVAILABLE` for a source | the endpoint, its host or its content type changed; see the detail column and [`docs/runbook.md`](docs/runbook.md); verified fallbacks are used automatically where they exist |
| `IncompleteSnapshotError` | OpenFEMA returned fewer records than it counted (often mid-refresh); retry. The previous snapshot stays loaded |
| `Critical data-quality failure(s): …` (exit 2) | read `sample` in `ops.dq_results`; fix, exclude, or document the decision in `docs/data-quality.md` |
| SQL step failed (exit 3) | `make status`; the failing step and message are in `ops.pipeline_runs` |
| Report shows the wrong profile | `report`/`dq` use the last transform; run `make transform PROFILE=...` first |
| Proposal PDF not produced | install Chrome/Chromium or set `CHROME=/path/to/chrome`; otherwise open the HTML and print to PDF (Letter) |

**Cleanup:**

| Command | Effect |
|---|---|
| `make clean` | removes caches and the generated report |
| `make down` | stops containers and keeps the data |
| `docker compose down -v` | **deletes the database volume** |
| `make clean-lake` | **deletes downloaded files** |
| `rm -rf .venv` | removes the Python environment |

After a database reset, the next run downloads the files again, because reuse requires the
database's provenance record.

## 13. Known limitations

Measured on the national run of 2026-11-08 (details in [`docs/data-quality.md`](docs/data-quality.md)):

* **Zone mapping coverage.** 19,489 land events (1.26 %) remain unmapped, down from 40,785
  (2.64 %) before date-effective mapping. The causes:
  * 12,334 are from 2000–2004, before the oldest surviving crosswalk version (2004-12-28), with no
    same-name zone in any version;
  * 6,335 fall in the two periods with no surviving version (Dec 2012–Oct 2014, Apr 2017–Mar 2020)
    and their zone name disagrees;
  * 597 use zone codes found in no version;
  * 223 use a zone missing from the in-effect version.

  Mapped rates are ≥ 99.8 % for 2005–2011, 2015–2016 and 2021–2025; 94–99 % in the gap years; and
  84–90 % for 2000–2004 (`serving.zone_mapping_coverage`).
* **Archive copies.** Crosswalk versions before 2020 are Internet Archive copies of the NWS files
  (SHA-1-verified) because NWS no longer serves them; 415,527 mapped events used such a version.
* **Constant dollars.** National CPI-U, with no regional price differences. 2025 uses the
  BLS-published annual average because October 2025 was not published.
* **Recorded losses only.** NOAA damage values are estimates, and 28 % of events have none.
* **Coverage is designation, not money, and not neglect.** Declarations follow state requests and
  FEMA criteria, not NOAA records, so a low coverage rate is a divergence to explain, not proof
  of neglect. Q2 rates are rates of event-county pairs, so events that touch many counties weigh
  more.
* **HMA absence is not non-application (the central limitation).** HMA records are
  subapplications that reached FEMA. "No observed county-mapped subapplication" can reflect state
  screening or prioritization, local capacity (staff, cost share, benefit-cost analysis), a
  statewide or unparsed benefiting area, or no attempt; a zero recorded obligation can reflect a
  pending, withdrawn or unsuccessful subapplication, or a true zero. Stages show where activity
  is observed; they do not show who applied, why obligations are low, or any cause.
* **Status is a snapshot.** OpenFEMA statuses are current values, shown verbatim; they are not
  final outcomes, and status and recorded obligation are independent fields (positive
  obligations appear under statuses other than "Obligated").
* **Recorded obligation is not receipt.** Obligated amounts are split equally across benefiting
  areas; they are not money received by a county government and not all disaster aid.
* **Follow-up screen, not a finding.** `is_high_need_low_obligation` flags counties for follow-up
  within the state's HMA process; it is not evidence that a county was overlooked, did not apply,
  or was neglected. SVI comparisons are unadjusted associations. Measured nationally with the
  storm/declaration geography break (2026-11-08): **83 counties**. Union County SC and Dickey
  County ND are below the 0.75 damage-percentile boundary once the CT planning regions'
  false-zero damage is out of the ranks, and are not high-loss. The 84 of the earlier HMA-only
  run is *historical*. The current states + DC median of HMA-comparable units is $33.41 per
  resident (2025 $); Puerto Rico's separate median is $78.21. The
  report lists the top 20 by damage per resident and states the total.
* **Connecticut planning regions have no comparable 2000–2025 storm, declaration or HMA
  history.** NOAA storm events, FEMA declarations and HMA records name the 8 legacy counties;
  without a defensible crosswalk the 9 current planning regions keep SVI and population but have
  NULL (not zero) storm and declaration measures, no damage rank, no place in SVI-quartile damage
  or coverage rates and no screen result, and carry `geography_not_comparable` with no HMA rate,
  rank or median. Connecticut's 2000–2025 history is visible in the legacy-geography universe;
  its storm and declaration records may stay in national historical event totals, and only the
  current-geography HMA headline shares leave the legacy counties out. Measured nationally on
  2026-11-08 with the current code: exactly 9 units (09110–09190) have
  `storm_declaration_geography_comparable = false`, with NULL storm events, damage, damage
  percentile, significant events, major disasters and emergencies and kept SVI and population.
  Excluding them, states + DC damage per resident by SVI quartile is Q1 $1,803.72, Q2 $1,742.93,
  Q3 $2,391.35, Q4 $3,079.95 (2025 $). Over FY2000–2025: 3,223 of 3,243 units obligated,
  11 not observed, 9 `geography_not_comparable` (in every period). The report and dashboard HMA
  headline covers HMA-comparable units of the states, DC and Puerto Rico: 3,213 units, 3,204
  obligated and 9 with no observed county-mapped subapplication. Island areas and the 8 legacy
  counties are reported separately.
* **Weather-related HMA obligation is a lower bound.** Measured nationally: 10,056 of 54,638
  source subapplications have no `primaryHazard`; county-attributed records with hazard family "Not
  specified" cover 7,401 distinct subapplications and $2.537 B (2025 $). They count in total
  recorded obligation but cannot count as weather-related. The report and dashboard query these
  figures for the active profile.
* **State-year table scope.** `serving.state_year_summary` covers applicants that are states,
  DC or territories. Measured nationally, 906 subapplications ($82.2 M, 2025 $) are outside it:
  883 with no applicant state and 23 from FM. They remain in every county, status and program
  table and are reconciled by DQ checks.
* **Turndowns are recorded decisions, not need, and not HMA decisions.** A DeclarationDenials
  record is a presidential declaration request (by a state, territory or tribe) whose current
  OpenFEMA status is "Turndown". It says nothing about the damage, requests granted on
  appeal appear only as declarations, and events for which nothing was requested appear in
  neither. Denials have no county, so they are compared only by state, region and hazard. The
  ingest scope selects them by request date and the analysis by incident year, so requests filed
  after the profile's last year are not included.
* **Q4 is descriptive.** Lags are grouped one dimension at a time (SVI group, hazard family, FEMA
  region) without adjustment. Groups differ in hazard mix, region and year, so a difference
  between SVI groups is not an effect of vulnerability. Groups under 10 disasters are flagged.
* **Tribal designations.** Declarations for reservations, tribal statistical areas and Alaska
  Native villages are kept as their own areas. They do not count as coverage of the surrounding
  county because the designated area is not the county. Tribal requests that designate whole
  counties (8 rows nationally) count as county designations.
* **Hazard rules are a team judgement.** `seeds/hazard_match_rules.csv` allows a declaration to
  cover companion hazards its type routinely names (e.g. flooding under "Severe Storm"). The
  evidence is in the implementation plan; changing the seed changes coverage.
* **Equal splits.** Zone-based events and multi-county HMA subapplications are split equally across
  counties. Population-weighted allocation is a planned sensitivity check.
* **HMA geography.** Of the national HMA benefiting-area entries, 94,396 resolve to counties.
  The rest stay at state level: 621 could not be resolved, and 19,985 are out-of-state, 3,423
  statewide and 2,593 unspecified. In 2025 dollars, of $31.447 B in recorded positive obligation,
  $25.800 B is attributed to counties and $5.648 B (17.96 %) is therefore not attributed to a
  county (positive amounts only; the **net** sum including negative de-obligation records, about
  $31.446 B, is slightly lower and is not used as recorded obligation). These counts are after accented names such
  as `Mayaguez` / `Mayagüez` were matched; before that fix, 855 entries were unresolved and
  19,993 out-of-state (historical). Unresolved, statewide and out-of-state entries are one reason
  a county can show no observed county-mapped subapplication.
* **Universes outside the national SVI.**
  * Puerto Rico's SVI ranks municipios only within Puerto Rico, so PR is analysed as its own
    universe.
  * The 13 island-area units and the 8 legacy Connecticut counties have no SVI or population, so
    no per-capita measures (totals are kept). Legacy counties are left out of the
    current-geography HMA headline shares; their storm and declaration records are real
    pre-change history and stay in national historical event totals.
  * Puerto Rico's ranks and `is_low_obligation` use Puerto Rico's own distribution and median.
* **One SVI release.** SVI percentiles are only comparable within one release (2022 is used
  throughout).
* **Not exercised:** AWS S3 itself (needs account credentials; the S3 code path is verified
  against SeaweedFS and a mock). What was verified, and when, is in
  [`docs/verification.md`](docs/verification.md).

## 14. Further documentation

| Document | Read it for |
|---|---|
| [`docs/sources.md`](docs/sources.md) | verified source facts, URLs, terms, volumes, rejected sources |
| [`docs/decision-record.md`](docs/decision-record.md) | why this problem (6 options scored), 22 architecture decisions, and sources considered but not used |
| [`docs/implementation-plan.md`](docs/implementation-plan.md) | exact definitions, entity resolution, acceptance criteria, timeline, team roles, risk register, demo scripts |
| [`docs/architecture.md`](docs/architecture.md) | layers, storage justification, object keys, idempotency, measured run times |
| [`docs/data-contracts.md`](docs/data-contracts.md) | per-source expectations and serving-table guarantees |
| [`docs/data-quality.md`](docs/data-quality.md) | data-quality issue register with evidence and decisions |
| [`docs/runbook.md`](docs/runbook.md) | operations, source-outage fallbacks, S3 switch, resets |
| [`docs/rubric-traceability.md`](docs/rubric-traceability.md) | each course requirement → where it is met |
| [`docs/verification.md`](docs/verification.md) | exact checks run, results, defects found and fixed |
| [`docs/proposal/`](docs/proposal/) | Milestone 1 proposal (Markdown source, HTML, PDF) |
| [`seeds/README.md`](seeds/README.md), [`tests/fixtures/README.md`](tests/fixtures/README.md) | what the mappings and fixture excerpts are |

## 15. Milestones and requirement mapping

| Milestone | Due | Deliverable | Where in this repo |
|---|---|---|---|
| M1 Proposal | Mon 9 Nov 2026, 11:59 pm | 1–2 page PDF: team, PM, meeting-notes link, problem, questions, value, motivation | `docs/proposal/milestone1-proposal.md` → `make proposal`. **Fill the placeholders first** (team number and section, names, PM, Google Doc link and date, personal motivation). The Google Doc must already contain one meeting |
| M2 Data and ingestion | Mon 23 Nov 2026, 11:59 pm | 3–4 page PDF (sources, storage justification, data quality, initial cleaning) and a 5-minute video | content: `docs/sources.md`, `docs/architecture.md`, `docs/data-quality.md`, `ops.dq_results`; video script in `docs/implementation-plan.md` §9 |
| M3 Final | presentations Mon 7 Dec (A2, B2) or Wed 9 Dec (C2) | 15-minute live presentation, final report, peer evaluation | report, dashboard, serving tables; presentation outline in `docs/implementation-plan.md` §9 |

How each course requirement (A.1–E.22) is met is mapped in
[`docs/rubric-traceability.md`](docs/rubric-traceability.md). In brief:
* three or more providers, including semi-structured JSON → §3
* raw preserved in PostgreSQL and object storage, with storage justified → §4, §11
* data-quality checks, cleaning, entity resolution and flattening in SQL → §11
* serving tables and analysis → §2, §8

## 16. Team workflow

* **Roles** (detail in the implementation plan, §7):
  * R1 ingestion and storage
  * R2 semi-structured data and integration
  * R3 data quality and transformations
  * R4 analysis and storytelling
  * one member is also the elected PM
* **Weekly meeting**, recorded in the shared Google Doc (agenda, notes, decisions, action items).
  Rotate the note-taker. The course reviews this document's version history.
* **Branches and reviews:** branch from `main` (`feature/<topic>`). Run `make lint && make test`
  before pushing, and open a pull request for one review from a different role. CI must be green to
  merge. Never commit `.env`, `data/` or downloaded files; they are git-ignored.
* **Changing the pipeline:**
  * *New SQL model:* add a numbered file to the right `sql/<layer>/` folder (files run in name
    order), then `make transform`.
  * *New DQ check:* add an entry with a `handling` decision to `config/dq_checks.yml`, then
    `make dq`.
  * *New hazard value or county alias:* edit the seed CSV; it is picked up by `make transform`.
  * *New source:* add it to `config/sources.yml`, write a module in `src/resgap/ingest/`, register
    it in `cli.py`, add staging SQL, a data contract, DQ checks and a fixture excerpt (extend
    `scripts/build_fixtures.py`).
* **Record decisions** in `docs/data-quality.md` (data issues) or `docs/decision-record.md`
  (design).

## 17. Attribution

This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed by
FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these data
after the data have been retrieved from the Agency's website(s). BLS.gov cannot vouch for the data
or analyses derived from these data after the data have been retrieved from BLS.gov (CPI-U retrieved
via the BLS Public Data API; the retrieval date is shown in the report). Data from NOAA (NCEI and
NWS), CDC/ATSDR, the U.S. Census Bureau and BLS are U.S. government works; no endorsement by those
agencies is implied. Crosswalk versions before 2020 are NWS files retrieved from Internet Archive
captures of their original NWS URLs.

The code has no licence yet. Choose one as a team before publishing the repository.
