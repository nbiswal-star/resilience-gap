# Resilience Gap: How Do Federal Disaster Declarations and Mitigation Obligations Align with County Storm Losses and Social Vulnerability?

**95-815 Data Engineering · Fall 2026 Mini 2 · Milestone 1 Project Proposal**

| | |
|---|---|
| **Team** | Team [TEAM NUMBER], Section [A2 / B2 / C2] |
| **Members** | [Full name 1], [Full name 2], [Full name 3], [Full name 4] |
| **Project manager** | [Full name of elected PM] |
| **Meeting notes (Google Doc)** | [Link — shared with all instructors and TAs via Andrew email; contains Meeting 1 on [date]] |

## Context

Severe storms, floods, hurricanes and winter storms damage U.S. communities every year. In NOAA's
Storm Events Database, events that can be placed in a U.S. county from 2000 to 2025 record at least
$818 billion in property and crop damage in 2025 dollars ($575 billion nominal; our own tally from
the bulk files, adjusted with the BLS Consumer Price Index). Two federal channels respond, and in
both a county usually reaches FEMA through a state-level process. **After** an incident, a governor
or tribal chief executive requests a presidential declaration; a *major disaster declaration*
designates specific counties (rarely a whole state) for FEMA assistance, or the request is turned
down. **Before** the next incident, FEMA's *Hazard Mitigation Assistance (HMA)* programs fund
projects such as buyouts, drainage and safe rooms. States, territories and tribes act as
applicants: they solicit, screen and prioritize local *subapplications* and forward selected ones
to FEMA for review; funds are *obligated* only later. Communities also differ in their capacity to
prepare and recover, which the CDC/ATSDR *Social Vulnerability Index (SVI)* measures for most U.S.
counties and all 78 Puerto Rico municipios.

## Problem statement

Central question: **how do presidential disaster declarations and recorded HMA obligations align
with county storm losses and social vulnerability?** We will answer it with an end-to-end pipeline
that integrates 2000–2025 NOAA and FEMA records with 2022 CDC/ATSDR vulnerability scores and July
2022 Census population estimates at the county level, and show where recorded federal response and
recorded losses diverge most. The analysis is descriptive, not causal, and keeps four measures
separate because each is observed at a different stage:

1. **Declaration coverage:** a significant event-county pair falls inside a weather-related major
   disaster declaration that designates its county (or literally the whole state), with a
   compatible hazard whose event date falls within the declaration incident period, allowing the
   implemented one-day boundary tolerance.
2. **Observed HMA subapplication activity:** the county is named as a benefiting area in a
   subapplication published in OpenFEMA, i.e. one that reached FEMA. Proposals that stopped at the
   local or state stage are invisible.
3. **Approval / status:** the subapplication's current recorded status (e.g. "Submitted to FEMA",
   "Approved", "Obligated"), a snapshot rather than a final outcome.
4. **Obligation:** FEMA's recorded obligated amount in 2025 dollars, split equally across every
   benefiting-area entry a subapplication lists. Shares for statewide, out-of-state, unresolved or
   missing areas stay at state level rather than being guessed; these are about 18 % of obligated
   dollars.

## Specific questions

1. **Exposure.** How is recorded storm damage per resident distributed across SVI quartiles, and
   which counties carry the heaviest recorded burden?
2. **Declaration coverage.** What share of significant event-county pairs (≥ $100,000 of county
   damage in 2025 dollars, or any casualty) has declaration coverage, and how does it vary by SVI
   quartile, hazard family, FEMA region and state? As government-level context only: how many
   weather-related declaration requests were turned down? (`DeclarationDenials` records carry no
   county and are not HMA decisions.)
3. **Mitigation alignment, stage by stage.** By five-year period, program and hazard family, is a
   county's HMA activity (a) not observed, (b) observed without a positive obligation, or (c)
   obligated, and how do these stages, the status mix, total obligation and weather-related
   obligation per resident vary with recorded losses and SVI? Follow-up screen: top-quartile
   damage per resident, SVI Q4 and total obligation per resident ≤ the HMA-comparable states + DC
   median. It is not evidence that a county was overlooked or never applied.
4. **Timeliness.** How many days pass from incident start to the request and to the declaration,
   and how do these lags differ by SVI group, hazard family and FEMA region?

*Robustness:* equal vs. population-weighted allocation of county-attributed HMA shares; alternative
significance thresholds.
*Stretch:* National Flood Insurance Program claims vs. recorded flood damage.

## What the data can and cannot show

An observed zero obligation is ambiguous: no subapplication naming the county reached FEMA; the
state screened out or deprioritized one; local capacity (staff, cost share, benefit-cost analysis)
limited what was prepared; a subapplication is pending, withdrawn or unsuccessful; the county is
covered only by a statewide or unparsed entry; or it is a true zero. The data cannot separate these,
so we report the stage at which activity is or is not observed and never infer that a county did
not apply, or why its funding is low. Declarations follow state requests and FEMA criteria, not
NOAA records, so low coverage is a divergence to explain, not neglect. NOAA damage figures are
estimates, and SVI comparisons are unadjusted associations, not effects of vulnerability.

## Why it matters, and to whom

* **State hazard mitigation officers and county emergency managers** can see where recorded losses
  and vulnerability are high but recorded obligations low: a starting point for outreach and
  technical assistance within the state's own HMA process.
* **FEMA regional staff, legislators and auditors** get a transparent, stage-by-stage county
  measure of how declarations and mitigation obligations track recorded need, which bears on
  ongoing debates about equity in disaster programs.
* **Community organizations in highly vulnerable counties** get reproducible evidence, with its
  limits stated, to support questions to state and federal agencies.
* **Researchers and insurers** get a cleaned, integrated county panel that links hazards, federal
  response and vulnerability, which no single public source provides.

## Motivation

[TEAM TO PERSONALIZE — 2–3 sentences in your own words, e.g. a member's professional background
in emergency management, public policy or insurance, or personal experience of a disaster.]
We are studying in Pittsburgh, and Pennsylvania illustrates the question: in August 2024
Tropical Storm Debby flooded counties across northern and central Pennsylvania; 13 were later designated under
major disaster declaration DR-4815-PA. The topic also tests core data engineering skills. Five
federal agencies publish these data in different formats (CSV, pipe-delimited text and JSON
APIs), with different geographic keys and vocabularies, free-text county lists, and forecast zones
that were redrawn over time. Linking them reliably is the hard part, and it is what makes the
answers trustworthy.

## Data and feasibility (verified 8 Nov 2026)

| Dataset (provider) | Format | Scale | Role |
|---|---|---|---|
| Storm Events Database (NOAA NCEI) | CSV, yearly files | 1.59 M events, 2000–2025 | Losses, deaths, injuries |
| Zone–county correlation files (NOAA NWS) | Pipe-delimited | 47 dated versions, 2004–2026 | Places the 39 % of land events recorded by forecast zone, as of the event date |
| Disaster Declarations, HMA Subapplications, Declaration Denials (FEMA OpenFEMA API) | **JSON (semi-structured)** | 104 k records | Designated areas and turndowns by incident year; HMA status, areas and obligations by fiscal year |
| Social Vulnerability Index 2022, U.S. and Puerto Rico files (CDC/ATSDR) | CSV | 3,144 counties + 78 municipios × 158 fields | Vulnerability (Puerto Rico ranked separately) |
| County and Puerto Rico population, Vintage 2025 (U.S. Census Bureau) | CSV | 3,195 + 546 rows | July 2022 per-capita denominators |
| Consumer Price Index CPI-U (U.S. Bureau of Labor Statistics API) | JSON | monthly, 1990–2026 | Constant-dollar comparisons |

All sources are public and need no API key. We have already confirmed access and ingested every
source; a working pipeline builds the Q1–Q4 serving tables, and the per-county HMA stage and status
breakdown is the main extension for Milestones 2–3. Structured data goes into PostgreSQL in
separate raw, staging, curated and serving layers; original files and JSON responses are kept
unchanged in object storage with an S3-compatible path. *This product uses the Federal Emergency
Management Agency's OpenFEMA API, but is not endorsed by FEMA. The Federal Government or FEMA
cannot vouch for the data or analyses derived from these data after the data have been retrieved
from the Agency's website(s).* *BLS.gov cannot vouch for the data or analyses derived from these
data after the data have been retrieved from BLS.gov.*
