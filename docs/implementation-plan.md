# Implementation plan

Companion to [`decision-record.md`](decision-record.md). Dates are 2026.

## 1. Problem statement

Storms impose large losses on U.S. counties. Two federal channels respond, and in both a county
usually reaches FEMA through a state-level process: after an incident, **presidential disaster
declarations** designate counties for FEMA assistance (or the request is turned down); before
the next one, **Hazard Mitigation Assistance (HMA)** programs fund projects whose subapplications
states, territories and tribes screen and forward to FEMA, with funds obligated later. Using
recorded losses (NOAA), federal records (FEMA), social vulnerability (CDC/ATSDR) and population
(Census), we ask **how presidential disaster declarations and recorded HMA obligations align with
county storm losses and social vulnerability**, and show where recorded federal response and
recorded losses diverge most. Four measures stay separate because each is observed at a
different stage: declaration coverage (designation, not money), observed HMA subapplication
activity, status (verbatim snapshot) and recorded obligation. The analysis is descriptive, not
causal; it never infers that a county did not apply, was denied or was neglected.

## 2. Analytical questions (exact definitions)

Common definitions (all configurable in `config/profiles.yml`):
* **Window**: profile years (`full` = 2000–2025).
* **Dollars**: constant base-year dollars (`analysis.inflation.base_year`, default 2025) using BLS
  CPI-U `CUUR0000SA0`: event year for storm damage, federal fiscal year for HMA. Nominal values are
  kept alongside.
* **Universe**: national SVI quartiles and the high-need, low-obligation follow-up screen cover
  the 50 states + DC. Puerto Rico (SVI ranked within PR) and the island areas (no SVI) are
  reported separately.
* **County key**: 5-digit Census FIPS; population = Census Vintage 2025 estimate for 2022 (aligned
  with SVI 2022).
* **Vulnerability**: CDC/ATSDR SVI 2022 overall national percentile `RPL_THEMES`; quartiles
  Q1 < 0.25 ≤ Q2 < 0.50 ≤ Q3 < 0.75 ≤ Q4.
* **Weather hazard**: hazard family with `is_weather = true` in `seeds/hazard_crosswalk.csv`
  (excludes e.g. COVID-19 "Biological" declarations and earthquakes).
* **Q2 unit**: an **event-county pair** (a storm event that touches three counties is three pairs).
* **Significant pair**: county-allocated damage ≥ $100,000 **in constant base-year dollars**
  (`analysis.significant_damage_real_usd`) **or** the event caused ≥ 1 death/injury.
* **Declared areas**: `county`; `statewide` only when OpenFEMA `designatedArea` is literally
  "Statewide"; `tribal` for reservations, tribal statistical areas and Alaska Native villages
  (mostly `fipsCountyCode = '000'`), never expanded to counties; `unresolved`.
* **Hazard compatibility**: a declaration can cover an event only if `seeds/hazard_match_rules.csv`
  lists the pair (declaration family, event family); see §4 for the rule and its evidence.
* **FEMA region**: `seeds/fema_regions.csv`, checked against OpenFEMA's `region` field.
* **HMA unit**: a subapplication published in OpenFEMA, i.e. one that reached FEMA. Its
  benefiting areas are resolved to counties where possible; statewide, out-of-state, unresolved
  and unspecified shares stay at state level.
* **HMA stage** (county-mapped areas; per county over all profile fiscal years and per five-year
  period): `geography_not_comparable` (the 9 current Connecticut planning regions, checked
  first), `not_observed` (no county-mapped subapplication observed; never "did not apply"),
  `observed_no_positive_obligation`, `obligated` (≥ 1 with `totalObligatedAmount` > 0, whatever
  the status).
* **Geography comparability** (`curated.dim_county`): for the current units of a state whose
  county geography changed between the 2020 reference and Vintage 2025 (only Connecticut: 9
  planning regions), both flags are false. NOAA Storm Events, OpenFEMA declarations and OpenFEMA
  HMA record 2000–2025 Connecticut history on the 8 legacy counties, and no county-to-region
  crosswalk is defensible, so nothing is crosswalked or allocated across the break.
  * `storm_declaration_geography_comparable`: storm events, damage, casualties, significant-event
    coverage, major disasters and emergencies are NULL (not comparable), never zero, and so are
    per-capita damage, coverage rate and damage percentile; the units are excluded from damage
    ranks, damage-burden quartiles, SVI-quartile damage and coverage numerators and denominators,
    and the follow-up screen.
  * `hma_geography_comparable`: HMA stage `geography_not_comparable`; excluded from HMA stage
    denominators, per-capita ranks, medians and the follow-up screen.

  SVI and population are kept. Legacy counties keep their storm, declaration and HMA records as
  `legacy_geography`; their storm and declaration records may remain in national historical
  event totals, and only the current-geography HMA headline shares leave them out.
* **Status**: OpenFEMA `status`, verbatim snapshot; never recoded, never used to set the stage.
* **Recorded obligation**: equal benefiting-area shares of positive `totalObligatedAmount`, in
  nominal and constant base-year dollars (fiscal-year CPI); weather-related = primary hazard in a
  weather hazard family, a lower bound because subapplications without a primary hazard cannot
  be classified. Not money received by a county government.
* **Damage-burden quartile**: quartile of constant-dollar damage per resident, percent rank within
  the analysis universe (fixed cut points; quartile 4 = `is_high_loss`); Q3 stage alignment by
  quartile in `serving.hma_damage_burden_summary`, over units with comparable storm history,
  HMA-comparable units only.
* **Periods**: fixed five-year federal fiscal-year periods FY2001–2005 … FY2021–2025 (FY2000 in
  FY1996–2000, a one-year period in the full profile), clipped to the profile.
* **Follow-up screen** (`is_high_need_low_obligation`): states + DC, units with comparable storm
  history and HMA geography; top-quartile constant-dollar damage per resident (percent rank ≥
  0.75 over all states + DC units with a population and comparable storm history), SVI quartile 4, constant-dollar recorded HMA obligation per resident ≤ the
  median of HMA-comparable states + DC units. Not evidence of neglect or non-application.
  `is_low_obligation` is computed in every analysis universe against that universe's own median
  (Puerto Rico against Puerto Rico); the screen itself is states + DC only.

| # | Question | Measure | Serving table |
|---|---|---|---|
| Q1 | **Exposure.** How is NOAA-recorded storm damage per resident distributed across SVI quartiles, and which counties bear the highest burden? | `damage_total_usd / population_base` by county and quartile; top-N counties | `county_resilience_profile`, `svi_quartile_summary` |
| Q2 | **Declaration coverage.** What share of significant weather event-county pairs fall inside a weather-related Major Disaster (DR) declaration that designates the same county (or is a literal statewide designation), has a compatible hazard family, and covers the date (±1 day; open incidents capped at 60 days)? Does it differ by SVI quartile, hazard family, FEMA region and state? Government-level context only: weather-related declaration requests turned down (DeclarationDenials; no county, not HMA decisions) by region and hazard | `covered_by_major_disaster` rate over event-county pairs (designation, not money); `turndown_share` | `event_declaration_coverage`, `declaration_request_outcomes` |
| Q3 | **Mitigation alignment, stage by stage.** By five-year period, program and hazard family, is a county's HMA activity (a) not observed, (b) observed without a positive obligation, or (c) obligated (CT planning regions: `geography_not_comparable`, excluded)? How do the stages, the verbatim status mix, total and weather-related (lower-bound) recorded obligation per resident vary with damage-burden quartile and SVI, over all periods and period by period, each analysis universe separately? Which counties fall in the **high-need, low-obligation follow-up screen**? | `hma_activity_stage`, observed / positive-obligation subapplications, `hma_per_capita_real`, `hma_weather_per_capita_real`, `hma_to_damage_ratio`, `hma_status_mix`, `is_high_need_low_obligation` | `county_resilience_profile`, `hma_county_period_activity`, `hma_county_program_hazard_activity`, `hma_status_summary`, `hma_stage_summary`, `hma_damage_burden_summary`, `svi_quartile_summary` |
| Q4 | **Timeliness.** How many days pass from incident start to the declaration request, and from incident start to a DR declaration (request to declaration as an extra lag)? How do the lags differ by SVI group (population-weighted SVI of designated counties), hazard family and FEMA region? **Descriptive one-way breakdowns** (median, P25, P75) with the hazard mix of each group and sample-size flags; no causal adjustment. Turned-down requests per hazard family and region as context | `days_incident_to_request`, `days_incident_to_declaration` (+ `days_request_to_declaration`) | `declaration_timeliness`, `declaration_timeliness_summary` |
| Q5 *(stretch)* | **Insurance gap.** For flood events, how do NFIP claims paid compare with NOAA-recorded flood damage, by SVI quartile? | claims paid / recorded flood damage | to add: `NfipClaims` v3 (county code present) |

Preliminary national signal from the national run of 2026-11-08 (2000–2025, constant 2025
dollars, date-effective zone mapping, literal-statewide and hazard-compatibility rules; for
planning only). The damage per resident, follow-up screen and HMA figures below were re-measured
on 2026-11-08 with the storm/declaration geography break (the 9 CT planning regions excluded
from storm and declaration comparisons).
* Recorded damage per resident (states + DC, units with comparable storm and declaration
  history, 2025 $) rises from Q1 $1,803.72 and Q2 $1,742.93 to Q3 $2,391.35 and Q4 $3,079.95.
  Comparable / not-comparable units per quartile: Q1 784/2, Q2 783/3, Q3 783/3, Q4 785/1.
  *Historical:* the earlier run, with the regions' false zeros, gave Q1 $1,793 and Q4 $3,071.
* 82,573 significant event-county pairs; 21.6 % are covered by a major disaster. The rate is
  similar across quartiles and slightly lower for higher SVI (Q1 22.5 %, Q2 23.2 %, Q3 21.4 %,
  Q4 20.6 %), confirmed in the current national database. Before the statewide/tribal,
  real-dollar and hazard fixes the figures were 77,004 pairs and 23.8 % (Q1 24.9 % … Q4 23.0 %);
  the correction lowered every quartile.
* Follow-up screen: **83 counties** (measured 2026-11-08). Union County SC and Dickey County ND
  are below the 0.75 damage-percentile boundary after the correction and are not high-loss.
  *Historical, superseded:* 84 counties in the rebuild with the HMA-only Connecticut fix, in the
  intermediate funnel run and before the funnel (when the rule was labelled "priority gap"). The
  current states + DC HMA median is $33.41 per resident (2025 $; Puerto Rico's own median is
  $78.21).
* HMA stages over FY2000–2025 (county-mapped areas; re-measured with the storm/declaration
  geography break on 2026-11-08, unchanged):
  * Of 3,243 counties and county-equivalents, 3,223 have a recorded positive obligation, 0 are
    observed without one, 11 have no observed county-mapped subapplication, and the 9 CT
    planning regions are `geography_not_comparable` (HMA records are published on the legacy
    county geography).
  * By universe (obligated / not observed / geography not comparable): states + DC 3,126 / 9 /
    9; Puerto Rico 78 / 0 / 0; island areas 11 / 2 / 0; legacy geography 8 / 0 / 0.
  * Report and dashboard HMA headline (HMA-comparable units of the states, DC and Puerto Rico):
    3,213 units, 3,204 obligated and 9 not observed (all states + DC). Island areas (13 units:
    11 obligated, 2 not observed), the 9 CT planning regions and the 8 legacy counties are
    reported separately; 3,226 (which adds the island areas) is not the headline.
  * By five-year period (confirmed in the current national database), the
    not-observed count falls from 2,976 in FY2000 (a one-year period) and 1,397 in FY2001–2005
    to 37 in FY2021–2025; the 9 CT regions are `geography_not_comparable` in every period
    (re-confirmed 2026-11-08). Every period is in `verification.md`.
  * *Historical, intermediate funnel run before the fix:* 20 not observed (18 in states + DC,
    including the 9 CT regions), and 2,985 … 46 by period.
* Recorded positive HMA obligation is $31.447 B in 2025 dollars. Of that, $25.800 B is
  attributed to counties and $5.648 B (17.96 %) stays at state level. (The net sum including
  negative de-obligation records, about $31.446 B, is not used as recorded obligation.)
* Weather-related recorded obligation is a lower bound: 10,056 of 54,638 subapplications have no
  primary hazard; county-attributed "Not specified" records cover 7,401 subapplications and
  $2.537 B.
* Median days from incident start to declaration (1,513 weather DRs; overall 40): SVI Q1 43,
  Q2 45, Q3 32, Q4 29; tropical cyclones 9, tornadoes 13, severe storms 39, floods 57; FEMA
  Region 4 21 to Region 10 73. Severe storms are 65 % of Q1 disasters but 48 % of Q4 disasters,
  so the shorter lags for high-SVI disasters are confounded by hazard mix and region. They are
  reported descriptively, not as an effect of vulnerability.
* 262 weather-related major disaster requests were turned down (14.8 % of recorded DR
  decisions; tornado 43 %, wildfire 31 %, tropical cyclone 5 %).
* Puerto Rico, analysed on its own, recorded $8,023 per resident ($25.8 B, 2025 dollars).

## 3. Target architecture (implemented)

See [`architecture.md`](architecture.md). Summary: sources → object store (immutable raw files and
JSON pages, SHA-256) → PostgreSQL `raw` (all-TEXT, lineage) → `staging` (typed, parsed,
de-duplicated, entity-resolved) → `curated` (dimensions and facts) → `serving` (analysis-ready)
→ HTML report and Streamlit dashboard; `ops` holds runs, objects, raw loads, rejects and DQ
results.

## 4. Entity-resolution strategy

| Link | Method | Evidence of quality (full run) |
|---|---|---|
| NOAA county events → county | `STATE_FIPS` + `CZ_FIPS`, with Storm Events territory codes mapped to Census (seed) | 944 k bridge rows |
| NOAA zone events → county | **Date-effective**: the NWS crosswalk version in effect on the event date (47 versions 2004–2026: 12 NWS-hosted, 35 SHA-1-verified Internet Archive copies); inside documented version gaps and for nearest-version matches the zone name must agree; otherwise unmapped with a reason; damage split equally across the zone's counties | 578,570 zone events mapped (393,389 in-effect, 102,440 gap name-verified, 82,741 nearest name-verified); 19,489 land events unresolved (1.26 %, was 2.64 %) |
| Marine events | `CZ_TYPE = 'M'` or pseudo-states 81–95 | excluded by design (logged) |
| FEMA declarations → county | `fipsStateCode` + `fipsCountyCode` → county; `designatedArea` literally "Statewide" → statewide; other code-`000` areas (non-zero `placeCode` or tribal request) → tribal, kept by name, never expanded; tribal requests flagged | 47,781 county, 20 statewide, 1,102 tribal, 60 unresolved (e.g. Bedford city VA, merged 2013). The first implementation called all 1,122 code-`000` rows statewide (fixed; data-quality issue 28) |
| HMA `benefitingCounties` → county | split on `;`/`,` → normalised name key (case, punctuation, accents incl. the Census file's double-encoded letters, COUNTY/PARISH/…, SAINT→ST, spaces) → match within applicant state → explicit alias seed → classify `statewide` / `out_of_state` / `unspecified` / `unresolved`; equal money split | National benefiting-area rows after accent folding: 94,396 county, 19,985 out-of-state (multi-state subapplications, deliberately not guessed), 3,423 statewide, 621 unresolved, 2,593 unspecified. Historical, before accent folding: 855 unresolved and 19,993 out-of-state |
| Hazard vocabularies | seed crosswalk for NOAA `EVENT_TYPE`, FEMA `incidentType`, HMA `primaryHazard` | DQ check `seed_unmapped_hazard_values` = 0 |
| Events ↔ declarations | same county or literal statewide designation + compatible hazard family (`hazard_match_rules.csv`) + event date within incident window | TS Debby floods 2024-08-09 ↔ DR-4815-PA via the tropical-cyclone → flood rule (test); real tribal and statewide records with labelled event vectors (test) |
| FEMA regions | state → region seed | agrees with the `region` field of all 48,963 declaration and 352 denial records (critical check) |
| Denials → analysis | state + incident year + hazard family (no county in the source) | 351 records in 2000–2025 by incident year; 301 weather-related |
| Legacy geography | Census 2020 reference (legacy CT counties) ∪ Vintage 2025 (planning regions) | 8 legacy CT counties: `analysis_universe = legacy_geography`, per-capita = NULL, storm, declaration and HMA records kept here. 9 CT planning regions: `storm_declaration_geography_comparable = false` (storm and declaration history NULL, not zero; no damage rank) and `hma_geography_comparable = false` (HMA stage `geography_not_comparable`); SVI and population kept; no legacy fact or amount is mapped to a region |
| Territories | Puerto Rico: Census municipio estimates + CDC's stand-alone PR SVI (ranked within PR); island areas: no SVI or population published | 78 municipios with population and within-PR SVI; 13 island-area units reported without SVI or per-capita measures |
| Constant dollars | BLS CPI-U annual average (calendar year) and fiscal-year mean | 36 annual values 1990–2025; `real_dollars_reconcile` critical check passes |

**Hazard compatibility rule** (`seeds/hazard_match_rules.csv`). A declaration covers an event of
the same hazard family, or of a companion hazard that declarations of its type routinely name.
Evidence from declaration titles of weather DRs with incidents in 2000–2025: of 853 "Severe
Storm" disasters, 616 titles name flooding, 313 tornadoes, 156 winter storms, snow or ice, and
114 landslides or mudslides; 177 of 238 "Flood" titles name storms or winds and 25 tornadoes; 16
of 37 "Tornado" titles name flooding; 7 of 10 "Mud/Landslide" titles name flooding.

| Declaration family | Also covers |
|---|---|
| Severe Storm & Wind | Tornado, Flood, Landslide, Winter Weather |
| Flood | Landslide, Severe Storm & Wind, Tornado |
| Tornado | Severe Storm & Wind, Flood |
| Tropical Cyclone | Flood, Severe Storm & Wind, Tornado, Coastal & Marine, Landslide |
| Coastal & Marine (Coastal Storm) | Flood, Severe Storm & Wind, Winter Weather, Tropical Cyclone |
| Winter Weather | Flood |
| Landslide | Flood |
| Wildfire, Drought | nothing else |

NOAA Heat, Other Weather and unmapped families have no rule and never match. The rule is a
team judgement; a stricter same-family-only variant is a planned sensitivity analysis.

Sensitivity analyses planned (week 3): population-weighted instead of equal allocation;
significant-event threshold $50k/$250k; match window ±7 days; same-family-only hazard matching;
Q4 lags within hazard family × region strata where groups have at least 10 disasters.

## 5. Acceptance criteria

| Area | Criterion | Status 2026-11-08 |
|---|---|---|
| Ingestion | All 9 ingest sources (the 6 provider datasets; 11 source checks, one per OpenFEMA dataset) ingest live with `make run`; a source outage fails loudly and names the source | ✅ verified (sample + full) |
| Raw fidelity | Raw row count = source data lines; 0 silent drops; original files retrievable by key with matching SHA-256 | ✅ (test `test_raw_preserves_every_source_row`) |
| Idempotency | Re-running ingest on unchanged sources reloads nothing (`unchanged`) and serving output is byte-identical | ✅ (test `test_rerun_is_idempotent`) |
| Semi-structured | OpenFEMA pages stored as received; flattened in SQL incl. multi-valued fields | ✅ |
| DQ | ≥ 25 checks (now 75) across raw/staging/curated/serving, each with documented handling; critical failure exits non-zero | ✅ all 75 checks passed on the fixture (`make offline-demo`) and on the national data (2026-11-08). The sample data was last checked with the 51 pre-funnel checks and has not been re-run since. The checks caught 52 negative HMA obligations and a flawed name check. Historical: 71/71 with the HMA-only Connecticut fix; 64/64 in the intermediate funnel run before the Connecticut fix |
| HMA funnel | Stages exclusive and exhaustive; status verbatim; fixed periods; counts and amounts reconcile across period, program × hazard, profile, status and stage tables; follow-up screen exact; no claim of non-application, denial or neglect in any output | ✅ funnel and final-review checks (stages, raw-amount and recount checks, damage-burden and state-year reconciliation) passed on the fixture and national data with the current code; 175 tests passed locally and in GitHub Actions run `42512681303` (2026-11-08; see `verification.md`; historical: 166 with the HMA-only Connecticut fix, 155 in the intermediate run before it) |
| HMA geography break | The 9 CT planning regions never appear as ordinary `not_observed`; excluded from HMA denominators, ranks, medians and the screen; legacy CT counties separate and outside the current-geography HMA headline shares; guard check | ✅ `hma_geography_break_guard` and the simulated-CT integration test passed with the current code; nationally the 9 regions are `geography_not_comparable` in the profile and every period; headline 3,213 HMA-comparable units, 3,204 obligated, 9 not observed (2026-11-08) |
| Storm / declaration geography break | The 9 CT planning regions keep SVI and population but have NULL (never zero) storm and declaration history, no damage rank or quartile, no place in SVI-quartile damage and coverage numerators or denominators, and no screen result; no legacy fact crosswalked; fact-triggered guard and independent recounts | ✅ executed 2026-11-08: guard, recount and simulation tests passed (175 tests), national DQ 75/75; exactly 9 units non-comparable with NULL history and kept SVI and population; follow-up screen 83 (84 in the superseded run) |
| Declared areas | Only literal "Statewide" rows are statewide; tribal areas never become county or statewide coverage | ✅ critical checks + tests on real OpenFEMA code-`000` records (2026-11-08) |
| Source resilience | `resgap source-check` reports availability, redirects, content type, fingerprint and freshness for every source without large downloads; ingestion fails fast on drift; verified fallbacks where they exist | ✅ 11/11 checks usable live; OpenFEMA bulk and NWS archive fallbacks tested |
| Date-effective geography | Zone events use the crosswalk version valid on the event date; no county is guessed; mapped/unmapped rates exposed by year | ✅ critical checks + rule tests; 1.26 % unresolved nationally (was 2.64 %) |
| Constant dollars | Real-dollar damage and HMA with a configurable base year; nominal kept; reconciliation | ✅ base 2025; `real_dollars_reconcile` passes |
| Territory universes | No territory row classified with national SVI; PR uses CDC's PR ranking; island areas flagged | ✅ `svi_universe_separation` passes; tests on real PR/VI fixtures |
| Reconciliation | Allocated damage and HMA dollars sum to source totals | ✅ critical checks + test |
| Serving | One row per county in scope; documented grain and keys | ✅ |
| Answers | Each of Q1–Q4 is answered by a chart and a table in the report; Q4 by SVI group, hazard family and FEMA region; every dataset contributes to a question | ✅ fixture and national reports written with the current code (75 checks; 2026-11-08); the sample report was last built before the funnel. Full-profile narrative due week 4 |
| Reproducibility | New teammate reaches a report from README in < 15 min; CI green on fixtures | ✅ fresh-clone walkthrough of the README (see `verification.md`); current GitHub Actions run `42512681303` is green at commit `1ba38a2` with lint, 175 tests and the 75-check offline pipeline |
| Documentation | Sources, contracts, DQ log, runbook, decision record, traceability | ✅ drafts |

## 6. Timeline (aligned to course milestones)

| Week | Dates | Deliverables | Gate |
|---|---|---|---|
| 0 | Sun 8 – Mon 9 Nov | First team meeting logged in Google Doc; elect PM; finalize **Milestone 1 PDF** (1–2 pages) | **M1 due Mon 9 Nov 11:59 pm** |
| 1 | 10 – 16 Nov | Everyone runs `make offline-demo` and `make run`; full profile run on one machine; profile each source; write source descriptions + storage justification; extend DQ checks found while profiling | Mentor check-in |
| 2 | 17 – 23 Nov | DQ narrative from `ops.dq_results`; before/after cleaning examples; record **5-min video** (Fri 20 Nov); 3–4-page PDF (Sat 21 Nov) | **M2 due Mon 23 Nov 11:59 pm** |
| 3 | 24 – 30 Nov | Sensitivity analyses (allocation, thresholds, hazard rule, CPI base year); Q4 stratified breakdowns where n allows; optional Q5 (NFIP claims); freeze serving schema (Sun 29 Nov). Thanksgiving (Thu 26) is a light week | Serving freeze |
| 4 | 1 – 6 Dec | Findings narrative, dashboard polish, slides; two full rehearsals (timed 15 min) | Dry run Fri 4 Dec |
| 5 | 7 / 9 Dec | **Live presentation** (A2/B2: Mon 7 Dec; C2: Wed 9 Dec); final report; peer evaluation | **M3** |

## 7. Team work split (4 people; names to be filled by the team)

| Role | Owns | Code / docs | Milestone 2 video segment |
|---|---|---|---|
| **R1 Ingestion & storage** | NOAA, NWS, Census, SVI ingestion; object store and S3 path; provenance | `src/resgap/ingest/`, `objectstore.py`, `docs/sources.md` | Sources and ingestion (1:15) |
| **R2 Semi-structured & integration** | OpenFEMA client, JSON flattening, entity resolution (zones, HMA names, territories, CT) | `ingest/openfema.py`, `sql/staging/04–08`, seeds | Where data lives; JSON → tables (1:15) |
| **R3 Quality & transformations** | DQ framework and checks, staging/curated models, reconciliation, tests/CI | `config/dq_checks.yml`, `sql/curated/`, `tests/`, `docs/data-quality.md` | Data quality and cleaning (1:30) |
| **R4 Analysis & storytelling** | Serving tables, report, dashboard, sensitivity analyses, slides | `sql/serving/`, `report/`, `dashboard/` | Cleaned data and first results (1:00) |
| **PM** (one of the four) | Agenda/minutes rotation, deadlines, mentor contact, submissions | Google Doc, `docs/` consistency | Intro and wrap (0:20) |

Pairing rule: every SQL model and ingestion module has a second reviewer from another role, so
each member can explain the whole pipeline in Q&A.

## 8. Risk register

| ID | Risk | L | I | Mitigation / fallback |
|---|---|---|---|---|
| R1 | Federal source outage, URL change or retired download | M | H | `resgap source-check` and a fail-fast preflight (redirect host allow-lists, content type, schema fingerprints); verified fallbacks for OpenFEMA (bulk `.jsonl`) and NWS (SHA-1-verified archive copies); immutable copies in the object store; committed real fixtures keep demos working |
| R2 | OpenFEMA dataset version deprecated mid-project (catalog shows `HazardMitigationGrantProgramDisasterSummaries` v2 deprecation 2026-10-15) | M | M | Check `OpenFemaDataSets` catalog (`depDate`) weekly; version is one config value |
| R3 | NOAA revises a year (`c` date changes) and numbers move | H | L | New file = new object key; SHA-256 change triggers reload; record retrieval date in deliverables |
| R4 | Crosswalk versions missing at the source (before Dec 2004; Dec 2012–Oct 2014; Apr 2017–Mar 2020) | H | M | Implemented: date-effective mapping with 47 versions and name verification inside gaps; residual 1.26 % unresolved, per year in `serving.zone_mapping_coverage` |
| R5 | "Coverage gap" misread as neglect | M | H | Explain declaration thresholds; coverage is designation, not money; present as measured association; sensitivity tests |
| R6 | BLS keyless API limit (25 queries/day) or outage | M | L | A national run uses 4 queries; the preflight uses HEAD; responses are kept in the object store and unchanged data is not re-requested |
| R7 | Allocation assumptions bias county totals | M | M | Equal split documented; population-weighted sensitivity |
| R8 | Docker not available on a laptop | M | L | Local PostgreSQL (16 tested; a non-superuser owner is enough) via `RESGAP_DATABASE_URL`; CI runs the stack on Linux |
| R9 | Full profile too heavy (3.3 GB DB) | L | L | `sample` is the default; full run on one machine and share the serving tables (CSV export) |
| R10 | Mixing SVI ranking universes | M | H | Implemented: separate analysis universes; critical `svi_universe_separation` check; PR uses CDC's PR file and Census municipio estimates |
| R11 | Team availability (Thanksgiving week) | M | M | Serving freeze before 30 Nov; M2 done early (20–21 Nov) |
| R12 | Scope creep (NFIP, IPAWS alerts) | M | M | Stretch items only after serving freeze and only if acceptance criteria are green |
| R13 | Attribution/terms omissions | L | M | FEMA statement embedded in report and dashboard; sources cited with retrieval dates |
| R14 | Source codes misread (e.g. FEMA county code `000` taken as statewide) | M | H | Classify from the authoritative field (`designatedArea`); critical checks; real-record regression tests that reintroduce the old rule |
| R15 | Q4 differences read as effects of vulnerability | M | H | One-way descriptive breakdowns only, hazard mix shown per group, sample-size flags, stated in report and dashboard |
| R16 | Integration tests run against the working database | L | H | Separate `resgap_test`; the test session stops if its URL names the same database as `RESGAP_DATABASE_URL` |
| R17 | Absence of HMA records or a zero obligation read as non-application, denial or neglect; status read as a final outcome | H | H | Explicit stages at supported grains only; "no observed county-mapped subapplication" wording; status verbatim; positive-obligation names; central limitation in report, dashboard and README; follow-up screen language |

## 9. Demo plans

**Milestone 2 video (5 min):** (1) source websites: NCEI listing, OpenFEMA data page, SVI page,
Census page (0:45); (2) one raw file and one JSON page opened from `data/lake` with their SHA-256
in `ops.source_objects` (0:45); (3) `make run` with the log scrolling: ingestion, `unchanged` on
re-run (0:45); (4) raw vs staging in psql: damage `"1.50M"` → 1500000; zone event → three
counties; HMA free text → FIPS (1:15); (5) `ops.dq_results` and the DQ table in the report, with
two decisions explained (negative HMA obligations, marine events) (1:00); (6) serving table
preview (0:30).

**Final presentation (15 min):** problem and stakeholders (2) → architecture and storage choices
(3) → integration challenges with concrete examples (3) → data quality story (2) → findings Q1–Q4
with the dashboard, drilling into Pennsylvania and DR-4815-PA (4) → limitations and next steps (1).
Backup: offline demo on fixtures and a pre-rendered report if the network fails.

## 10. Stretch backlog (only after week-3 serving freeze)

1. Q5 with OpenFEMA `NfipClaims` v3 (2.73 M rows, `countyCode` present).
2. IPAWS archived alerts (deeply nested CAP JSON, SAME county codes) for warning coverage.
3. OpenFEMA `PublicAssistanceFundedProjectsDetails` (851 k rows) as recorded Public Assistance
   obligations per county (recovery), kept separate from declaration coverage.
4. Within-Puerto-Rico follow-up screen (PR's own SVI quartiles) as a separate section.
