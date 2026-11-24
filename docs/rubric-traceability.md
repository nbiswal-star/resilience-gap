# Requirement traceability

Numbers refer to the items in `reference/project-instructions.pdf` (95-815 Fall 2026, Mini 2).

| # | Requirement | Where it is met | Evidence |
|---|---|---|---|
| A.1 | Business/societal context | Storm losses, presidential disaster declarations and FEMA Hazard Mitigation Assistance, and community vulnerability | `README.md`; `docs/implementation-plan.md` §1 |
| A.2 | Overarching problem statement | How presidential disaster declarations and recorded HMA obligations align with county storm losses and social vulnerability (descriptive; four measures kept separate) | `docs/implementation-plan.md` §1 |
| A.3 | ≥ 3 concrete questions | Q1–Q4 with exact metrics (+ Q5 stretch) | `implementation-plan.md` §2 |
| A.4 | Why it matters, for whom, motivation | Stakeholders and motivation | `decision-record.md` §3 |
| B.5 | ≥ 3 complementary datasets, different providers | NOAA NCEI, NOAA NWS, FEMA, CDC/ATSDR, Census, BLS (5 agencies) | `docs/sources.md` |
| B.6 | ≥ 1 semi-structured dataset | OpenFEMA JSON API (3 datasets) | `src/resgap/ingest/openfema.py` |
| B.7 | Real data, nothing fabricated | Live downloads with SHA-256; test fixtures are byte-subsets of real files | `ops.source_objects`; `tests/fixtures/README.md` |
| B.8 | Non-trivial data | 1.59 M events, 104 k FEMA JSON records, 158-column SVI, 47 crosswalk versions (283,796 rows), CPI 1990–2026 | `architecture.md` (measured) |
| B.9 | Every dataset contributes | NOAA → Q1–Q4 losses; NWS → county placement; declarations → Q2, Q4; HMA → Q3 (observed subapplications, verbatim status, recorded obligation); DeclarationDenials → turned-down requests by region and hazard (Q2/Q4 context, `serving.declaration_request_outcomes`, report and dashboard); SVI → Q1–Q4 vulnerability; Census → denominators and keys; BLS → constant dollars and the significance threshold | `decision-record.md` §3.1; report sections Q1–Q4 |
| C.10 | Structured data in PostgreSQL; raw preserved; constraints later | Team-hosted AWS RDS PostgreSQL; `raw` all-TEXT + lineage; keys/FKs in `curated`/`serving` | `src/resgap/ingest/rawload.py`; `sql/curated/*` |
| C.11 | Suitable storage for semi-structured data | Object store in the team's shared Dropbox folder `resgap-lake/` (local dir ↔ S3 by config) + JSONB landing | `objectstore.py`; `sql/raw/10_openfema_records.sql` |
| C.12 | Justify storage choices | Storage table | `architecture.md` |
| C.13 | Raw separate from curated; staging layer | Schemas `raw`, `staging`, `curated`, `serving` | `sql/` |
| D.14 | Quality checks; critical fails, recoverable documented; log kept | 75 checks with severity (incl. statewide/tribal classification, coverage-match guards, and the HMA funnel: verbatim status checked against raw, stage exclusivity, period boundaries, reconciliation and independent recounts, exact follow-up screen, the Connecticut geography-break guards for storm/declaration history and for HMA, independent recounts of Q1/Q2 summaries, state-year scope reconciliation); `ops.dq_results`; issue register; source preflight. All 75 checks passed on the fixture and on the national data (2026-11-08); 175 tests passed. Historical: 71/71 with the HMA-only Connecticut fix | `config/dq_checks.yml`; `docs/data-quality.md` |
| D.15 | Types, missing values, duplicates | `try_*` casts, damage parser, sentinels, `DISTINCT ON` | `sql/functions`, `sql/staging` |
| D.16 | Integrate and resolve entities across sources | FIPS, date-effective zone crosswalk, territory codes, HMA name matching, county vs literal-statewide vs tribal declared areas, hazard crosswalk and compatibility rules, FEMA regions, SVI ranking universes, CPI deflators, temporal event↔declaration matching | `implementation-plan.md` §4 |
| D.17 | Flatten semi-structured data | JSONB → relational; multi-valued fields → arrays and bridge tables | `sql/staging/06–08` |
| D.18 | Aggregate / reshape | County, quartile, state-year, disaster-level, Q4 breakdown, request-outcome, and HMA stage aggregates (county × period grid, program × hazard, status mix, stage distribution, damage-burden quartile) | `sql/serving/*` |
| D.19 | SQL where appropriate | All cleaning, integration and aggregation run as SQL in PostgreSQL; Python only moves bytes | `sql/` |
| E.20 | Serving tables | 14 documented serving tables with explicit grain, keys and table comments (+ star-like curated model) | `docs/data-contracts.md` |
| E.21 | Consumption tools | HTML report (inline SVG), Streamlit dashboard; any BI tool can read `serving` | `src/resgap/report/`, `dashboard/` |
| E.22 | Answer questions, discuss meaning | Report sections Q1–Q4 (Q3 stage by stage with the central limitation; Q4 by SVI group, hazard family and FEMA region, with stated limits: descriptive, confounded, sample sizes); final narrative in weeks 3–4 | `reports/` (generated); `implementation-plan.md` §2 |
| 3.A | Project manager | Team administration | Managed outside this repository |
| 3.B | Weekly meetings, shared Google Doc | Team administration | Managed outside this repository |
| 23–26 | Milestone 1 contents | Milestone 1 submission | Artifacts kept outside this repository |
| 27–28 | Milestone 2 PDF + 5-min video | Content plan and video script | `implementation-plan.md` §6, §9 |
| 29–32 | Final presentation and report | 15-min structure, backup offline demo | `implementation-plan.md` §9 |
