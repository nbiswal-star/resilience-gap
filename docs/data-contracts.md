# Data contracts

A contract states what the pipeline **expects** from each source and what it **guarantees** to
consumers. Breaking an expectation marked *critical* stops the run (see `config/dq_checks.yml`).
A changed header in any delimited source is treated as schema drift and fails the load
(`SchemaDriftError`) until the contract is reviewed.

## Source contracts (inbound)

### NOAA Storm Events details → `raw.noaa_storm_events_details`
| Field(s) | Expectation | Used as | On violation |
|---|---|---|---|
| header | 51 columns, unchanged | schema | critical: schema drift |
| `EVENT_ID` | integer, present, unique per event | primary key | critical if missing; duplicates de-duplicated (latest load wins) |
| `BEGIN_YEARMONTH`, `BEGIN_DAY`, `BEGIN_TIME` | form a valid local timestamp | event date | warn; begin = NULL, excluded from dated facts |
| `STATE_FIPS`, `CZ_TYPE`, `CZ_FIPS` | `C` = county FIPS; `Z` = NWS zone; `M` or state ≥ 80 = marine | location key | warn if unresolvable (≤ 5 %) |
| `DAMAGE_PROPERTY`, `DAMAGE_CROPS` | blank, or number with optional K/M/B | USD | warn if unparseable (→ NULL) |
| `INJURIES_*`, `DEATHS_*` | non-negative integers | casualties | NULL → 0 |
| Volume | ≥ 1 row for every profile year | completeness | critical |
| Freshness | current year file re-created monthly | — | informational |

### NWS zone–county correlation (versioned) → `raw.nws_zone_county`
* 11 pipe-delimited fields, no header (fingerprint in `config/sources.yml`).
* One raw partition per version (`file=bpDDmonYY`). The valid date is decoded from the name with
  `staging.nws_file_date`.
* A row is used only if `STATE_ZONE` matches `^[A-Z]{2}[0-9]{3}$` and `FIPS` matches `^[0-9]{5}$`;
  other rows stay in raw. Rows with the wrong field count go to `ops.raw_rejects`.
* At least one file must be linked from the landing page; otherwise the source fails with
  `SOURCE UNAVAILABLE`.
* Archive-tier files must match their registered SHA-1, otherwise they are not stored. An
  unavailable non-linked version is a warning, and its coverage loss is measured by
  `zone_events_unmapped`.
* Excluded versions (`bp03mr26`, `bp07se22`) are never loaded.

### OpenFEMA → object store pages + `raw.openfema_records`
| Expectation | On violation |
|---|---|
| Response JSON has the entity array and, on page 1, `metadata.count` | API path fails → automatic fallback to the bulk `.jsonl` export (same fields and values); if that also fails, `SOURCE UNAVAILABLE` (other sources continue) |
| Retrieved records = `metadata.count` (API path) | `IncompleteSnapshotError`; previous snapshot stays loaded |
| Bulk export: every line is valid JSON (fallback path) | `SOURCE UNAVAILABLE` (truncated download) |
| Every record has `id` | stored anyway; de-duplication uses `id` |
| Dates are ISO-8601 strings | parsed with `staging.iso_date` (no time-zone shift); invalid → NULL |
| Declarations: `fipsStateCode` 2 digits, `fipsCountyCode` 3 digits; `designatedArea` literally "Statewide" marks a statewide designation; other `000` rows are tribal areas with a non-zero `placeCode` | `statewide` only for literal "Statewide" (critical); tribal areas kept as `tribal`, never assigned to counties (critical); code-`000` rows that are neither → `unresolved` (warn, threshold 0); other unknown areas `unresolved` (warn ≤ 5 %) |
| Declarations and denials: `region` equals the FEMA region of the state in `seeds/fema_regions.csv` | critical `fema_region_matches_source` |
| Denials: `requestedIncidentTypes` is one FEMA incident type; `currentRequestStatus` = `Turndown` | unmapped types warn via `seed_unmapped_hazard_values`; a denial is a recorded decision, never a damage measure |
| HMA: every record is a subapplication that reached FEMA (proposals stopped locally or by the state are absent) | absence of a county-mapped record is reported as "not observed", never as non-application |
| HMA: `benefitingCounties` free text, `;`/`,` separated | resolved / classified; unknown names warn ≤ 5 % |
| HMA: `status` free text (e.g. "Submitted to FEMA", "Approved", "Obligated", "Subaward Accepted") | kept verbatim through staging, curated and serving; any recoding stops the run (critical `hma_raw_status_preserved`) |
| HMA: `totalObligatedAmount` numeric or NULL | NULL → `not_reported`; negative values kept (warn) as `negative`, excluded from recorded-obligation totals and the positive-obligation stage; the positive flag must equal amount > 0 (critical) |
| HMA: `fiscalYear` integer | assigned to a fixed five-year fiscal-year period (critical `hma_period_boundaries`) |
| Record keys unchanged (fingerprint) | preflight warns only (added JSON fields are harmless) |

### CDC/ATSDR SVI 2022 → `raw.cdc_svi_county` (two partitions)
* `release=2022` (U.S. counties, 3,100–3,300 rows) and `release=2022-puerto-rico` (78
  municipios); both have the same 158 columns (warn otherwise).
* `FIPS` 5 digits; `RPL_*` in [0, 1] with `-999` = missing.
* The two partitions are **different ranking universes**. Staging tags each row with
  `svi_universe`, and curated never mixes them (critical `svi_universe_separation`).

### Census → `raw.census_popest_county`, `raw.census_popest_pr_municipio`, `raw.census_county_reference`
* **Counties:** `SUMLEV = '050'` rows are counties; `STATE` + `COUNTY` form FIPS;
  `POPESTIMATE2020–2025` are integers.
* **Puerto Rico:** `MUNICIPIO` + `YEAR` (2..7 = July 1 of 2020..2025) + `POPESTIMATE`; FIPS is
  `72` + `MUNICIPIO`.
* **Reference file:** `STATEFP` + `COUNTYFP` = FIPS; `STATE` = USPS abbreviation.
* A header change in any of these files fails the preflight and the load (`SchemaDriftError`).

### BLS CPI-U → object store response + `raw.bls_cpi_records` (JSONB)
| Expectation | On violation |
|---|---|
| `status = REQUEST_SUCCEEDED` and series `CUUR0000SA0` present | `SOURCE UNAVAILABLE` (e.g. `REQUEST_NOT_PROCESSED` when the 25-query daily limit is reached) |
| Monthly `value` numeric | non-numeric (e.g. `-` for October 2025) → NULL, counted by `cpi_months_not_published` |
| Annual CPI for the base year and every profile year; fiscal-year CPI for every profile year | critical `cpi_years_covered` |
| Published annual average ≈ mean of 12 months | warn `cpi_published_vs_computed` (0.05 %) |

## Serving contracts (outbound)

| Table | Grain / primary key | Key columns | Guarantees |
|---|---|---|---|
| `serving.county_resilience_profile` | one county or PR municipio in profile scope / `county_fips` | `analysis_universe`, `fema_region`, `svi_coverage`, population, national SVI + quartile (states_dc only), `svi_pr_within_rank` (PR only), storm events, damage nominal and `*_real` (total, per capita, percentile within universe), significant events + covered, major disasters, emergencies; `storm_declaration_geography_comparable`, `storm_declaration_geography_note` (false / explained for the 9 CT planning regions: every storm/declaration history column, per-capita damage, coverage rate, `damage_pctile` and `is_high_loss` is NULL, not zero); `hma_geography_comparable`, `hma_geography_note` (false / explained for the 9 CT planning regions); HMA over all profile fiscal years (county-mapped areas only): `hma_activity_stage` (`geography_not_comparable` | `not_observed` | `observed_no_positive_obligation` | `obligated`), `hma_observed_subapplications`, `hma_positive_obligation_subapplications`, `hma_no_positive_obligation_subapplications`, recorded obligation `hma_obligated_usd` / `_real_usd`, weather-related `hma_weather_obligated_usd` / `_real_usd` (+ per capita), `hma_status_mix` (verbatim status → subapplications), `hma_pctile`, `hma_per_capita_real_median` (both within the unit's analysis universe); flags `is_high_loss`, `is_high_vulnerability`, `is_low_obligation` (own-universe median; Puerto Rico against Puerto Rico), `is_high_need_low_obligation` (follow-up screen, states_dc, comparable storm history and HMA-comparable only) | damage sums plus damage withheld on current units of changed-geography states reconcile exactly with `curated.fact_storm_event_county` (critical `serving_damage_reconciles`); storm/declaration history NULL, never zero, and no damage rank or screen for units without comparable history (table constraint `profile_history_null_when_not_comparable`; critical `storm_declaration_geography_break_guard`, triggered by facts on legacy geography); stage exclusive and consistent with counts, amounts and `curated.dim_county` comparability; HMA counts and amounts reconcile with the curated fact and with the period table (critical); the follow-up flag and `is_low_obligation` equal the documented rule recomputed independently (critical); no current unit of a state with HMA records on legacy geography has an ordinary stage (critical `hma_geography_break_guard`); per-capita NULL when population unknown (island areas, legacy geography); HMA per-capita, rank, median and `is_low_obligation` NULL for non-comparable units |
| `serving.event_declaration_coverage` | significant weather **event-county pair** / (`event_id`, `county_fips`) | event attributes, `fema_region`, SVI, matched DR (`matched_area_level` county or statewide, `matched_hazard_family`, `matched_hazard_rule`), `covered_by_major_disaster`, `covered_by_emergency`, days to declaration | exactly the weather pairs with constant-dollar damage ≥ `significant_damage_real_usd` or casualties (critical); at most one matched DR; matches only through a county or literal statewide designation with a compatible hazard family, never a tribal area (critical) |
| `serving.declaration_timeliness` | weather DR disaster / `disaster_number` | dates, lags in days, `fema_region`, `hazard_family`, designated counties, `tribal_areas_designated`, `has_statewide_designation`, population-weighted SVI, `svi_group`, `analysis_universe`, NOAA damage (nominal, real) of compatible hazards in the incident window | lags computed from FEMA dates only; one `svi_group` per disaster |
| `serving.declaration_timeliness_summary` | breakdown × group / (`breakdown`, `group_label`); breakdown ∈ `all`, `svi_group`, `hazard_family`, `fema_region` | disasters, P25/median/P75 days incident→declaration, median request→declaration and incident→request, `top_hazard_family` + share, weather DR turndowns (not for SVI groups), `sample_note` | each breakdown partitions the same disasters (critical); descriptive, unadjusted; groups < 10 disasters flagged "do not compare" |
| `serving.declaration_request_outcomes` | state × incident year × declaration type (DR, EM) × hazard family / all four | `fema_region`, declared (disaster numbers), turned_down (denial records), tribal counts, `turndown_share` = turned down ÷ (declared + turned down), median days request→turndown | weather hazards only; totals reconcile with the curated facts (critical); a turndown share is a share of recorded decisions, not of damaging events |
| `serving.state_year_summary` | state × year / (`state_abbr`, `year`) | `fema_region`, storm events, damage (nominal, real), deaths, DR/EM counts, weather DR turndowns (incident year; declaration requests, not HMA), HMA observed and positive-obligation subapplications (any area level, fiscal year), recorded obligation (nominal, real; county vs non-county) | every state × year in scope present (zeros, not gaps). **Not a national HMA total**: subapplications with no applicant state, or an applicant outside `curated.dim_county` (e.g. FM), have no row; nationally 906 subapplications / about $82.2 M (2025 $): 883 without an applicant state and 23 from FM (measured on the national data, 2026-11-08). Counted by `state_year_hma_out_of_scope` (info) and reconciled exactly by `state_year_hma_reconciles` (critical) |
| `serving.svi_quartile_summary` | analysis universe × SVI group / unique (`analysis_universe`, `svi_quartile_label`) | counties, population (every unit); `counties_history_not_comparable`, `counties_history_comparable`, `history_comparable_population`; damage (nominal, real, per capita), `significant_event_counties`, `covered_event_counties`, coverage rate (comparable-history units only); HMA stage counts and shares (`counties_hma_geography_not_comparable`, `counties_hma_comparable`, `counties_hma_not_observed`, `counties_hma_observed_no_positive_obligation`, `counties_hma_obligated`), `hma_comparable_population`, `hma_comparable_damage_real_usd`, distinct observed and positive-obligation subapplications, recorded obligation total and weather-related (nominal, real, per capita), `high_need_low_obligation_counties` | national Q1–Q4 rows contain only states + DC; PR, island areas and legacy geography are separate rows; Q3 shares and per-capita / per-damage rates use HMA-comparable units only (CT planning regions excluded; per-damage rates also need comparable storm history); Q1/Q2 numerators and denominators use only units with comparable storm and declaration history (CT planning regions excluded; per-capita damage divides by `history_comparable_population`); equals an independent recount from the curated facts with comparability re-derived from `analysis_universe` and state (critical `history_summaries_reconcile`) |
| `serving.hma_county_period_activity` | county × five-year fiscal-year period, **dense** / (`county_fips`, `period_start_fy`) | county attributes, period bounds and label, `fiscal_years_in_period`, `is_partial_period`, `hma_activity_stage`, observed / positive / no-positive subapplications, programs observed, recorded obligation total and weather-related (nominal, real, real per capita), `status_mix`, NOAA damage of the same-numbered calendar years (NULL for the CT planning regions) | one row per profile county per period (critical); stages exclusive and consistent (critical); per-county sums equal the profile (critical); `not_observed` = no county-mapped subapplication observed in the period, never "did not apply"; CT planning regions are `geography_not_comparable` in every period (`hma_geography_comparable` column) |
| `serving.hma_county_program_hazard_activity` | county × period × program × hazard family, **observed combinations only** / unique (`county_fips`, `period_start_fy`, `program`, `hazard_family`) | `is_weather_hazard`, stage (`observed_no_positive_obligation` or `obligated`), observed and positive subapplications, recorded obligation (nominal, real), `status_mix` | never `not_observed` (absence is not claimed at this grain); rows add up exactly to the county-period (critical) |
| `serving.hma_status_summary` | period × program × verbatim status × county mapping / unique (`period_start_fy`, `program`, `status`, `county_mapping`) | subapplications (each once), positive / zero / negative / not-reported obligation counts, recorded obligation (whole subapplications and county-attributed; nominal, real) | every in-scope subapplication counted once; per-status counts equal staging; amounts equal the fact and the profile (critical) |
| `serving.hma_stage_summary` | analysis universe × SVI group × period / (`analysis_universe`, `svi_quartile_label`, `period_label`); `period_scope` = `all_periods` or `five_year_period` | counties per stage (incl. `counties_geography_not_comparable`), `counties_hma_comparable`, funnel shares over comparable counties, population and comparable population, county-subapplication links, distinct observed and positive subapplications, recorded obligation total and weather-related, obligation per resident of comparable counties, `counties_storm_history_comparable`, damage (units with comparable storm history; and those also HMA-comparable) | the four stages add up to counties; stage counts equal an independent recount from the curated fact and `curated.dim_county`; each period covers every profile county; all-period rows equal `svi_quartile_summary` (critical); all-period storm damage equals an independent recount (critical `history_summaries_reconcile`) |
| `serving.hma_damage_burden_summary` | analysis universe × damage-burden quartile / (`analysis_universe`, `damage_burden_quartile`) | `damage_burden_label` (D1–D4 of constant-dollar damage per resident, ranked within the universe), HMA-comparable counties, `counties_excluded_geography_not_comparable`, population, damage, stage counts and shares, recorded obligation total / per resident / per damage dollar, county median per resident, SVI Q4 and follow-up counts | only units with a population (states + DC, Puerto Rico) and comparable storm history (the CT planning regions have no damage rank and no row), each universe ranked on its own; equals an independent recount (quartile re-derived from fact damage and population over units whose storm-history comparability is re-derived from `analysis_universe` and state, stages from the fact, HMA comparability from the dimension) (critical `hma_damage_burden_reconciles`); descriptive, not causal |
| `serving.zone_mapping_coverage` | event year / `event_year` | county events, zone events, zone events mapped by rule, mapped via archive versions, unmapped by reason, mapped rate | profile scope; marine events counted separately |
| `serving.inflation_reference` | calendar year | CPI-U, basis (published / mean of 12 months), base year, factor to base | base-year factor exactly 1 |

Curated facts (`fact_storm_event_county`, `fact_declaration_area`, `fact_hma_allocation`) have
primary keys and foreign keys to `curated.dim_county`; allocation weights sum to 1 per event and
per HMA subapplication (critical checks). `fact_hma_allocation` also has a foreign key to
`curated.dim_hma_period` and a check constraint tying `has_positive_obligation` to
`obligation_amount_class = 'positive'`; `status` is never recoded. `fact_declaration_area` also has a check constraint:
a `county_fips` exists exactly for `county` rows. `fact_declaration_denial` (one turned-down
request, PK `denial_id`) is state-level: denial records carry no county.
