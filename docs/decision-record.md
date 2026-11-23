# Decision record

Status: **accepted** (2026-11-08) · Scope: problem selection and core architecture.
Evidence for every source fact is in [`sources.md`](sources.md); each candidate below was probed
live on 2026-11-08 before scoring.

## 1. Context

The course brief (`reference/project-instructions.pdf`) requires: a real-world problem with ≥ 3
concrete questions (A.1–4); ≥ 3 complementary, real, non-trivial datasets, ideally from different
providers, at least one semi-structured (B.5–9); structured data in PostgreSQL with raw data
preserved and separated from curated data, and a justified store for semi-structured data
(C.10–13); quality checks, cleaning, entity resolution, flattening, aggregation, SQL where
possible (D.14–19); serving tables and analysis that answers the questions (E.20–22). Four
students, ~4 weeks to the final presentation (7 or 9 Dec 2026). Proposal due 9 Nov 2026.

Additional team constraints: no paid credentials, public and legally reusable data, a topic with
social value and identifiable stakeholders, and technology students can explain.

## 2. Options considered

| # | Candidate problem | Datasets probed (all HTTP 200 on 2026-11-08) | Providers |
|---|---|---|---|
| **A** | **Resilience Gap** — how do presidential disaster declarations and recorded mitigation obligations align with storm losses and social vulnerability? | NOAA Storm Events CSV; NWS zone crosswalk; OpenFEMA Declarations / HMA / Denials JSON; CDC SVI 2022; Census Vintage 2025 | NOAA, FEMA, CDC, Census |
| B | Air quality and respiratory-health equity | EPA AirData `annual_aqi_by_county_2025.zip`; CDC PLACES County 2025 release (Socrata JSON `swc5-untb`); Census | EPA, CDC, Census |
| C | Pittsburgh housing-code enforcement and neighbourhood conditions | WPRDC CKAN API (`pittsburgh-pli-violations-report`, `property-assessments`); Census | WPRDC (City + County publishers), Census |
| D | Hospital access and quality | CMS Provider Data Catalog API (`xubh-q36u`, Hospital General Information); HRSA; Census | CMS, HRSA, Census |
| E | Flood-insurance protection gap | OpenFEMA `NfipClaims` v3 (2.73 M rows), `NfipPolicies` v3 (74.7 M rows); NOAA; SVI | FEMA, NOAA, CDC |
| F | Federal spending equity | USAspending API (`/api/v2/references/toptier_agencies/`); Census | Treasury, Census |

### Scoring (1 = weak, 3 = strong)

| Criterion | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| Fit to every brief requirement | 3 | 3 | 2 | 2 | 3 | 2 |
| ≥ 3 distinct providers | 3 (4) | 3 | 2 | 2 | 3 | 1 |
| Semi-structured data that genuinely needs flattening | 3 | 2 | 2 | 2 | 2 | 3 |
| Volume / heterogeneity | 3 | 2 | 2 | 2 | 3 | 3 |
| Entity-resolution richness | 3 | 2 | 2 | 1 | 2 | 2 |
| Social value and clear stakeholders | 3 | 3 | 3 | 3 | 3 | 2 |
| Feasible for 4 students in 4 weeks on laptops | 3 | 3 | 3 | 3 | 1 | 2 |
| No credentials needed | 3 | 3 | 3 | 3 | 3 | 3 |
| **Total** | **24** | 21 | 19 | 18 | 20 | 18 |

## 3. Decision

**Option A — Resilience Gap.** Problem statement:

> Severe storms, floods, hurricanes and winter storms damage U.S. communities every year. Two
> federal channels respond, and in both a county usually reaches FEMA through a state-level
> process: presidential disaster declarations (designation of counties for FEMA assistance
> after an incident) and Hazard Mitigation Assistance (subapplications forwarded by states,
> territories and tribes, obligated later). We ask how declarations and recorded HMA obligations
> align with recorded county storm losses and social vulnerability, keeping declaration
> coverage, observed subapplication activity, status and recorded obligation separate, and
> describing where recorded federal response and recorded losses diverge most. The analysis is
> descriptive, not causal.

*(Wording revised after the Milestone 1 proposal was finalized, which is the product contract:
the earlier "proportionate response" and "largest gaps" framing implied that the data show
need left unmet; they do not.)*

Why A wins:

1. **Every dataset is necessary.** NOAA supplies the losses, FEMA the response (declaration
   coverage, HMA subapplications with their status and recorded obligation, and turned-down
   declaration requests as government-level context for Q2 and Q4), CDC the vulnerability,
   Census the denominators and the canonical county key, the NWS crosswalk places the 39 % of
   land events recorded by forecast zone in counties, and BLS CPI-U puts 26 years of dollars on
   a common basis. Drop any one and a question becomes unanswerable (requirement B.9).
2. **Real integration work, not a single join.** Measured on the full 2000–2025 run:
   * date-effective zone→county mapping over 47 crosswalk versions (2004–2026), with
     name verification inside documented version gaps;
   * Storm Events' own territory codes (Puerto Rico = 99 vs Census 72) and marine pseudo-states;
   * free-text county lists in HMA (`"BUCKS; DAUPHIN COUNTY"`, `Statewide`, mixed case,
     out-of-state names in multi-state subapplications, USVI `St. Croix` vs Census
     `St. Croix Island`);
   * Connecticut's 2022 switch from 8 counties to 9 planning regions (SVI and Vintage 2025 use the
     new geography; older NOAA/FEMA records use the old one);
   * three different hazard vocabularies (NOAA event types, FEMA incident types, HMA hazards with
     mixed spellings such as `Flood`/`Flooding`) mapped onto one hazard family;
   * temporal matching of events to declaration incident windows.
3. **Semi-structured data used for what it is.** OpenFEMA JSON pages are stored as received, then
   flattened in SQL, including multi-valued fields exploded into bridge tables.
4. **Volume without pain.** 1.59 M events + 104 k FEMA records. Measured on a laptop on
   2026-11-08: the national NOAA + OpenFEMA ingest takes 97 s, the crosswalk, Puerto Rico and CPI
   sources about 3 minutes more, and the national transform about a minute (52–62 s across
   versions; about 62 s with the current code); the default sample runs
   end to end in 2.5–3 minutes on a clean lake (most of it fetching archived crosswalk files) and
   in under a minute when sources are unchanged.
5. **Clear stakeholders.** State hazard-mitigation officers and county emergency managers
   (a starting point for outreach and technical assistance within the state's HMA process),
   FEMA regional staff, legislators and auditors (a transparent, stage-by-stage measure of how
   declarations and recorded obligations track recorded need), community advocates in
   high-vulnerability counties, and insurers/researchers.
6. **Local relevance.** Pennsylvania counties are in every profile; the Allegheny/PA drill-down
   suits a Pittsburgh audience, and DR-4815-PA (Tropical Storm Debby, 2024) is a worked example
   verified in the test data.

### Why not the others

* **B (air quality):** strong, but CDC PLACES values are model-based small-area estimates and
  EPA AirData's 2025 annual county file has only 985 county rows (counties with monitors, of
  ~3,144), so the core relationship would rest on modelled outcomes and partial coverage. Less entity-resolution work.
* **C (Pittsburgh housing):** excellent local value, but two of three datasets come from one
  platform (WPRDC), the Census API now needs a key, and volume/heterogeneity is lower.
* **D (hospitals):** CMS dominates the data, facility IDs (CCN) make integration nearly trivial,
  and the questions lean on clinical domain knowledge the team lacks.
* **E (flood insurance):** compelling, but the 74.7 M-row policy file is heavy for laptops in
  4 weeks. NFIP claims are kept as a stretch dataset for Option A (Q5).
* **F (federal spending):** single dominant provider and a complex award hierarchy, with no
  natural third dataset.

## 4. Architecture decisions

| ID | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| AD-1 | PostgreSQL 16 for all structured data; schemas `raw` → `staging` → `curated` → `serving`, plus `ops` | Separate databases; DuckDB | Required by the brief; one engine keeps SQL transformations and constraints in one place; schemas make the layers visible |
| AD-2 | Raw tables are all `TEXT`, no keys/constraints, plus lineage columns; malformed rows go to `ops.raw_rejects` | Typed raw tables | Brief C.10: never discard problematic source records at the raw stage |
| AD-3 | Object store for raw files and JSON pages; local filesystem by default, S3-compatible backend by config | MongoDB for JSON; Postgres only | Payloads are write-once, immutable, file-shaped; object storage is the cheapest faithful system of record and maps 1:1 to S3. Querying happens in Postgres JSONB, so MongoDB would add a second query language with no analytical benefit |
| AD-4 | OpenFEMA records also landed as `JSONB` and flattened with SQL | Flatten in Python with pandas | Brief D.19 prefers SQL in the database; JSONB keeps the original structure queryable |
| AD-5 | Transformations as ordered plain SQL files, full rebuild each run | dbt; incremental models | Easy to read and explain; deterministic; data volume makes full rebuilds cheap (about a minute national) |
| AD-6 | Idempotency by content hashes: file SHA-256 and a canonical record-level hash for API snapshots; a partition is reloaded only when its content changes | Timestamp-based reloads | Re-runs are safe and cheap; API pages differ by `rundate` even when the data does not |
| AD-7 | Declarative DQ checks in YAML, executed as SQL, logged in `ops.dq_results`; `critical` stops the run | Great Expectations | Small, transparent, and the log doubles as the quality report for Milestone 2 |
| AD-8 | Zone-based events and multi-county HMA awards allocated equally across counties | Population- or area-weighted allocation | Simple, documented, totals reconcile exactly; population weighting is a sensitivity analysis in the plan |
| AD-9 | SVI quartiles from fixed national cut points (0.25/0.50/0.75) | Re-ranking within the analysed subset | A county is classified identically in sample and full runs |
| AD-10 | Orchestration by CLI + Makefile | Airflow / Prefect | Four-week course project; nothing needs a scheduler. The CLI steps map 1:1 to DAG tasks if one is added later |
| AD-11 | Consumption via static HTML report (inline SVG) and Streamlit dashboard | Tableau / Power BI only | Works offline, versionable, testable in CI; Streamlit gives interactive filtering. BI tools can still connect to `serving` |
| AD-12 | SeaweedFS (optional) as local S3 | MinIO | `minio/minio` is no longer pullable from Docker Hub (verified) |
| AD-13 | Zone events mapped with the crosswalk **version in effect on the event date**; versions no longer on NWS servers taken from Internet Archive captures of the original NWS URLs, SHA-1 verified, labelled `provenance_tier = internet_archive`; matches inside documented version gaps, or using a different version, require the zone name to agree; otherwise unmapped with a reason | Union of current versions (the first implementation); third-party zone databases; matching zone names to county names | The union applied 2020s zone definitions to 2000s events (renumbered zones → wrong or missing counties). Archive captures are byte-identical copies of NWS files and are verifiable. Guessing a county from a name is not acceptable |
| AD-14 | Inflation: BLS CPI-U `CUUR0000SA0` via the keyless API v1; annual average (BLS-published when returned, else mean of 12 months); fiscal-year mean for HMA; base year configurable (default 2025); nominal columns kept | BLS flat files; BLS API v2; FRED; BEA deflators | Flat files require personal contact details in the User-Agent (HTTP 403 otherwise); API v2 and BEA need personal keys; FRED was unreachable from the build machine and redistributes the same BLS series. CPI-U is the standard deflator for household-facing losses |
| AD-15 | Separate **analysis universes**: national SVI quartiles for 50 states + DC only; Puerto Rico's within-PR SVI kept in its own columns; island areas and legacy CT counties carry no SVI or per-capita measures | Pooling PR into national quartiles; imputing territory SVI | CDC documents that “Puerto Rico is not included in the U.S.-wide ranking”; no SVI is published for the island areas, and Census has no Vintage 2025 estimates for them |
| AD-16 | Source health checks (`resgap source-check`) and a fail-fast preflight before each ingestion: redirect host allow-lists, content-type checks, header/record-key fingerprints, last-modified; verified fallbacks only where an independent path serves identical content (OpenFEMA bulk exports, archive copies of NWS files) | Blind retries; unverified mirrors | Detects retired links (an HTML page or another host), schema changes and outages before any partial download replaces good data |

| AD-17 | A declared area is `statewide` only when OpenFEMA `designatedArea` is literally "Statewide"; other `fipsCountyCode = '000'` areas are `tribal` areas kept with their name and `placeCode`, never expanded to counties, and never count as Q2 coverage of a county | "`000` = statewide" (the first implementation); mapping reservations to the counties they overlap | 1,102 of 1,122 code-`000` rows are reservations, tribal statistical areas and Alaska Native villages; treating them as statewide inflated coverage in SD, AZ, WA, NE, MN and other states. No authoritative reservation-to-county allocation is in scope, so tribal areas are reported as their own areas |
| AD-18 | Events match declarations only for compatible hazard families (`seeds/hazard_match_rules.csv`): same family, or a companion hazard that declarations of that type routinely name (evidence from declaration titles); Heat, Other Weather and unmapped families never match | Any weather declaration covers any weather event; matching on FEMA's `designatedIncidentTypes` | Same-county, same-window matching alone counted heat waves under storm declarations. `designatedIncidentTypes` is filled for only 391 of about 1,600 major disasters, so the incident type is the only field available for all of them |
| AD-19 | Q4 is answered with descriptive one-way breakdowns (SVI group, hazard family, FEMA region) with quartiles, hazard mix and sample-size flags; DeclarationDenials provides turned-down requests as Q2/Q4 context in a dedicated serving table | A regression "controlling" for hazard and region | Too few disasters per cell for a defensible model in the course time frame; showing the hazard mix next to each group makes the confounding visible without claiming adjustment |
| AD-20 | Q3 is an application/status/obligation **funnel**: county stages `not_observed` / `observed_no_positive_obligation` / `obligated` over all profile fiscal years and per fixed five-year fiscal-year period (dense grid); program × hazard detail for observed combinations only; OpenFEMA `status` verbatim and independent of the stage (stage uses amount > 0); positive-obligation names replace `is_funded` / "funded"; the screen is renamed `is_high_need_low_obligation` (rule unchanged) and called a follow-up screen | One "HMA per capita" number with a "priority gap" flag (the first implementation); mapping statuses to "approved/denied"; filling program × hazard absences with zeros; keeping the old names as aliases | OpenFEMA HMA holds only subapplications that reached FEMA, so absence and zeros are ambiguous (state screening, local capacity, statewide entries, pending or withdrawn subapplications, true zeros). Stages state exactly what is observed; absence is stated only at grains where it is meaningful; status values are snapshots whose vocabulary FEMA controls; aliases would keep misleading names in BI tools. Fixed fiscal-year periods classify a year identically in every profile |
| AD-21 | **Nothing is attributed across the Connecticut geography break (storm, declaration and HMA).** The 9 current planning regions keep SVI and population (current social geography). `storm_declaration_geography_comparable = false`: their 2000–2025 storm, damage, casualty, coverage, major-disaster and emergency measures are NULL (not comparable), never zero; they are excluded from damage ranks and quartiles, SVI-quartile damage and coverage numerators and denominators, and the follow-up screen. `hma_geography_comparable = false`: explicit HMA stage `geography_not_comparable` (profile and every period), excluded from HMA denominators, ranks, medians and the screen. Both flags come from one data-driven rule. The 8 legacy counties keep their storm, declaration and HMA records as `legacy_geography`; their storm and declaration records may stay in national historical event totals, and only the current-geography HMA headline shares leave them out. Fact-triggered critical guards fail if current units of such a state get ordinary historical metrics, ranks or HMA stages | Mapping legacy-county facts or amounts to planning regions (population- or area-weighted); treating the regions as having zero storm history or as HMA `not_observed`; dropping Connecticut | NOAA Storm Events, OpenFEMA declarations and OpenFEMA HMA name the legacy counties; the planning regions do not nest in them, so any split would invent attributions. Zeros were false: as HMA `not_observed` the regions lowered the states + DC median, and as zero-damage units they shifted every damage percentile, sat in the bottom damage quartile, diluted SVI-quartile rates and changed the follow-up screen (84 counties with the false zeros, 83 after the fix, measured 2026-11-08; Union County SC and Dickey County ND are below the 0.75 damage-percentile boundary after the correction and are not high-loss). Dropping Connecticut would lose valid SVI and population geography and the legacy history |
| AD-22 | `is_low_obligation` and HMA ranks are computed within each analysis universe (Puerto Rico against Puerto Rico's own median); the follow-up screen stays states + DC only. `serving.state_year_summary` is explicitly scoped to state, DC and territory applicants, with excluded subapplications reconciled by DQ | One national median including Puerto Rico; silently dropping records without an applicant state | Universes must not be mixed (AD-15), and Puerto Rico has no national SVI rank for the screen. Exclusions must be counted, not implied away |

### Sources considered and not used (verified 2026-11-08)

* **FEMA National Risk Index (NRI).** The bulk county download
  (`https://hazards.fema.gov/nri/Content/StaticDocuments/DataDownload//NRI_Table_Counties/NRI_Table_Counties.zip`)
  and `https://hazards.fema.gov/nri/data-resources` both redirect to FEMA's Resilience Analysis and
  Planning Tool page (`https://www.fema.gov/emergency-managers/practitioners/resilience-analysis-and-planning-tool`)
  and return HTML rather than data. The project measures *observed* losses from NOAA instead of
  NRI's modelled expected annual loss, so no question depends on NRI.
* **Census Data API (`api.census.gov`).** Unauthenticated requests are redirected to
  `missing_key.html`, so the API needs a (free, personal) key. Every Census input the project
  needs is published as a keyless flat file on `www2.census.gov`, which is the supported route.

## 5. Consequences and known limitations

* Constant-dollar measures use a national CPI-U; regional price differences are not modelled.
  Nominal values are kept next to every real-dollar column.
* NOAA damage estimates are reported by NWS offices and are known to be incomplete (28 % of events
  have no estimate); results describe *recorded* losses.
* Declarations are triggered by state/county damage assessments and per-capita thresholds, not by
  NOAA records, so a low "coverage" rate is a measured divergence to explain, not proof of
  neglect. Coverage is designation (eligibility), not money.
* HMA records are subapplications that reached FEMA. A county with no observed county-mapped
  subapplication may still have tried to apply, and a zero recorded obligation can be pending,
  withdrawn, unsuccessful or a true zero; results never claim non-application, denial,
  rejection, causation or neglect. Recorded obligation is not money received by a county
  government. DeclarationDenials are outcomes of presidential declaration requests, not HMA
  decisions.
* Puerto Rico's SVI is ranked within Puerto Rico only, so PR is analysed as its own universe;
  island areas have no SVI and no Census population estimates and get no per-capita measures.
* Zone-based events from 2000–2004 (before the oldest surviving crosswalk, 2004-12-28) and inside
  the two documented version gaps can only be mapped when the zone name agrees; the remainder
  (1.26 % of all land events, measured) stays unmapped with a reason.
* Statewide and out-of-state HMA amounts cannot be attributed to a county; they stay in state
  totals (`serving.state_year_summary`), except the 906 subapplications without an applicant
  state or from FM (about $82.2 M, 2025 $), which have no state-year row and are reconciled by
  DQ checks.
