# Data sources — verified facts

All facts below were checked against the live sources on **2026-11-08**. Volumes marked
*measured* come from this pipeline's national run (2000–2025, all states and territories) on that
date. `resgap source-check` (or `make source-check`) re-verifies every endpoint without
downloading large files. The live results from 2026-11-08 are in [`verification.md`](verification.md).

Every download goes through redirect validation. Each hop and the final URL must stay on the
source's `allowed_hosts` (see `config/sources.yml`) over HTTPS, and an HTML page served in place of a
data file is rejected.

## 1. NOAA NCEI Storm Events Database — structured (CSV)

| Item | Verified fact |
|---|---|
| Provider | NOAA National Centers for Environmental Information (NCEI) |
| Landing page | https://www.ncei.noaa.gov/access/storm-events-database/ (the former `/stormevents/` and `ncdc.noaa.gov/stormevents/` URLs redirect here) |
| Bulk download | https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/ (HTTP directory listing; 77 yearly *details* files, 1950–2026) |
| Format documentation | `Storm-Data-Bulk-csv-Format.pdf` and `README` in the same directory |
| Files used | `StormEvents_details-ftp_v1.0_dYYYY_cYYYYMMDD.csv.gz`, one per year, 51 columns (fingerprint `b96502785a9d4333`) |
| Naming / cadence | Per README: `dYYYY` = data year, `cYYYYMMDD` = creation date; the current year is regenerated monthly and earlier years can be revised (e.g. the 2024 file was re-created 2026-07-28, 2012 on 2026-10-26) |
| Size | ~12–13 MB compressed per recent year. *Measured* 2000–2025: 26 files, 257 MB compressed, **1,592,849 events** |
| Stable identifier | `EVENT_ID` (no duplicates in 2000–2025); `EPISODE_ID` groups events |
| Geography | `STATE_FIPS` + `CZ_FIPS` with `CZ_TYPE` = `C` (county) or `Z` (NWS forecast zone). *Measured*: 39 % of land events use zones. Territories use non-Census state codes (PR = 99, VI = 96, AS = 97, GU = 98); codes 81–95 are marine or Great Lakes areas |
| Quirks | Damage stored as text with a K/M/B suffix (`"1.50M"`); blank = not reported (*measured*: 28 % of events have both damage fields blank) |
| Terms | U.S. federal government data. The bulk README states no use restriction. We cite NCEI and imply no endorsement |
| Fallback | None independent: `www1.ncdc.noaa.gov/pub/...` only redirects to the same NCEI path. Files already in the object store are reused while the database holds their provenance |

## 2. NOAA NWS County–Public Forecast Zone correlation files — versioned crosswalk

| Item | Verified fact |
|---|---|
| Provider | NOAA National Weather Service GIS |
| Landing page | https://www.weather.gov/gis/ZoneCounty (links `bp18mr25.dbx` and `bp16ap26.dbx`) |
| NWS-hosted versions | 12 files at https://www.weather.gov/source/gis/Shapefiles/County/ (HTTP 200): `bp03mr20`, `bp10nv20`, `bp30mr21`, `bp08se21`, `bp22mr22`, `bp13se22`, `bp08mr23`, `bp19se23`, `bp05mr24`, `bp10se24`, `bp18mr25`, `bp16ap26` (2020-03-03 → current). There is no directory listing (HTTP 500), so names were discovered as described below |
| Older versions | Every pre-2020 name returns HTTP 404 from NWS, at both the current path and the legacy `/geodata/catalog/wsom/data/` path |
| Archive tier | **35 versions from 2004-12-28 to 2017-04-04** are available as Internet Archive captures of the original NWS URLs (`https://web.archive.org/web/<timestamp>id_/<original>`). Names were discovered through the archive's CDX index. Each download is verified against the archive's SHA-1 digest (the digest equals the SHA-1 of the file body; checked on 5 files and then on every download). Stored under `raw/nws_zone_county/internet_archive/` with `provenance_tier = internet_archive` |
| Excluded | `bp03mr26` (planned version; NWS moved implementation to 16 Apr 2026) and `bp07se22` (superseded six days later by `bp13se22`) were published but are not used as in-effect versions |
| Gaps | No version between **2017-04-04 and 2020-03-03**, nor between **2012-12-04 and 2014-10-07**, exists at NWS or in the archive. No version exists before 2004-12-28 |
| Format | Pipe-delimited, no header, 11 fields: STATE, ZONE, CWA, NAME, STATE_ZONE, COUNTY, FIPS, TIME_ZONE, FE_AREA, LAT, LON (fingerprint `bca5af0570305aea`) |
| Size | ~0.3–0.6 MB per version; *measured* 47 versions = 283,796 rows |
| Quirks | `LA152` carries ZONE `125` in the 2023 files; NWS serves byte-identical content for `bp03mr20` and `bp10nv20`; 32 malformed lines in 16 archived files are kept in `ops.raw_rejects` |
| Terms | https://www.weather.gov/disclaimer: NWS web information is public domain unless noted. Do not claim it as your own, imply endorsement, or modify it and present it as official |
| Fallback | For the NWS-hosted files `bp05mr24`, `bp18mr25` and `bp16ap26`, archive captures whose SHA-1 equals the current NWS bytes (other captures of hosted files differ from the NWS bytes and are not used) |

## 3. FEMA OpenFEMA API — semi-structured (JSON)

| Item | Verified fact |
|---|---|
| Provider | Federal Emergency Management Agency |
| Catalog | https://www.fema.gov/api/open/v1/OpenFemaDataSets (machine-readable; 44 datasets) |
| API docs | https://www.fema.gov/about/openfema/api: `$top` default 1,000, **maximum 10,000**; `$skip`; `$filter`; `$orderby`; `$count` (replaces the deprecated `$inlinecount`) |
| Terms | https://www.fema.gov/about/openfema/terms-conditions requires the statement “This product uses the Federal Emergency Management Agency’s OpenFEMA API, but is not endorsed by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these data after the data have been retrieved from the Agency's website(s).” Also cite the dataset, URL and retrieval date |
| Fallback | Line-delimited bulk exports `https://www.fema.gov/api/open/<version>/<Dataset>.jsonl` (content type `application/jsonl+json`; 64.6 MB declarations, 31.6 MB HMA, 0.9 MB denials). Field names **and values** equal the API's (compared record by record), so a fallback snapshot hashes identically. Used automatically if API paging fails; the original export is stored and filtered with the same scope rules |

| Dataset | Version / data page | Records | Refresh | Key | Fingerprint |
|---|---|---|---|---|---|
| DisasterDeclarationsSummaries | v2, https://www.fema.gov/openfema-data-page/disaster-declarations-summaries-v2 | 70,599 total; *measured* 48,963 with incident 2000–2025 | `R/PT20M` | `id` | `30a5033288a03bea` |
| HmaSubapplications | v2, https://www.fema.gov/openfema-data-page/hma-subapplications-v2 | 63,635 total; *measured* 54,638 for FY2000–2025 | `R/P1D` | `id` | `2077b207d17cd0e1` |
| DeclarationDenials | v1, https://www.fema.gov/openfema-data-page/declaration-denials-v1 | 1,303 total; *measured* 352 requested 2000–2025 (351 with the requested incident in 2000–2025; 301 weather-related) | `R/P1D` | `id` | `c957f0284494240c` |

Field semantics used by the analysis (checked in the data on 2026-11-08):
* **Declarations, `designatedArea` / `fipsCountyCode` / `placeCode`.** `fipsCountyCode` `000` is
  used both for the literal "Statewide" designation (`placeCode` 0; 20 rows with incidents in
  2000–2025) and for tribal areas such as reservations, OTSAs and Alaska Native villages (non-zero
  `placeCode`; 1,102 rows, most with `tribalRequest` false). Only the literal "Statewide" means
  the whole state.
* **Declarations, `designatedIncidentTypes`.** Filled for 391 of about 1,600 major disasters, so
  it cannot drive hazard matching; `incidentType` is used.
* **Declarations and denials, `region`.** One FEMA region per state in every record.
* **Denials.** One record per turned-down presidential declaration request
  (`currentRequestStatus` "Turndown") by a state, territory or tribe; state, requested incident
  type and dates, programs requested; no county. These are government-level request outcomes,
  not decisions on HMA subapplications.
* **HMA subapplications.** One record per subapplication that a state, territory or tribe (the
  applicant) forwarded to FEMA; proposals that stopped at the local or state stage are not
  published. `benefitingCounties` is free text; `status` is the current recorded status (values
  seen include "Submitted to FEMA", "Pending", "Approved", "Subaward Accepted", "Obligated"),
  a snapshot rather than a final outcome; `totalObligatedAmount` is FEMA's recorded obligation
  (NULL when none is recorded; a few negative de-obligation values). Status and amount are
  independent fields: positive amounts appear under statuses other than "Obligated". The
  pipeline keeps `status` verbatim and derives stages from the amount only.

## 4. CDC/ATSDR Social Vulnerability Index 2022 — structured (CSV)

| Item | Verified fact |
|---|---|
| Provider | CDC / ATSDR Geospatial Research, Analysis, and Services Program (GRASP) |
| Landing page | https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html (dated May 11, 2026) |
| Documentation | https://www.atsdr.cdc.gov/place-health/media/pdfs/2024/10/SVI2022Documentation.pdf: “we’ve added a stand-alone, state-specific Commonwealth of Puerto Rico database. **Puerto Rico is not included in the U.S.-wide ranking.**” Percentile ranks are relative within a release; do not compare across releases |
| U.S. county file | https://svi.cdc.gov/Documents/Data/2022/csv/states_counties/SVI_2022_US_county.csv: 3,144 counties × 158 columns, UTF-8 with BOM; Connecticut uses its 9 planning regions; no `-999` values in this file |
| Puerto Rico file | https://svi.cdc.gov/Documents/Data/2022/csv/states_counties/PuertoRico_county.csv: 78 municipios, same 158 columns (fingerprint `5e0a192d79f3bace` for both). `RPL_THEMES` spans 0–1 **within Puerto Rico** |
| Other territories | The official download tool (https://svi.cdc.gov/dataDownloads/data-download.html, menu from `xmldata/SVI_StatesForDownload.xml`) lists Puerto Rico as the only territory, and the documentation mentions no other. No SVI exists for AS, GU, MP or VI |
| Terms | U.S. government data; CDC provides a suggested citation on the landing page |
| Fallback | None independent; fail fast. The 2022 release is versioned, so the lake copy is sufficient |

## 5. U.S. Census Bureau — structured (CSV / pipe)

| Item | Verified fact |
|---|---|
| County population | Vintage 2025 `co-est2025-alldata.csv`: https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv (3,195 rows, Latin-1; last modified 2026-03-26; landing https://www.census.gov/data/tables/time-series/demo/popest/2020s-counties-total.html, released March 2026) |
| Puerto Rico population | `cc-est2025-agesex-72.csv`: https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/puerto-rico/asrh/cc-est2025-agesex-72.csv (78 municipios × 7 years = 546 rows, Latin-1). Layout https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CC-EST2025-AGESEX.pdf: YEAR 1 = 4/1/2020 base, 2–7 = 7/1/2020–7/1/2025; release date June 2026. Quirk: the file uses columns `MUNICIPIO`/`NAME` where the layout documents `COUNTY`/`CTYNAME` |
| Island areas | No Vintage 2025 estimates file for AS, GU, MP or VI exists in the `popest/datasets/2020-2025/` directories (`counties/`, `puerto-rico/`, `state/`, …) |
| County reference | https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt (pipe-delimited, 3,235 rows incl. territories and legacy CT counties) |
| Access route | **Keyless flat files** (the supported route). The Census Data API is not used (see the decision record) |
| Terms | U.S. government work (public domain); cite the Census Bureau |
| Fallback | None independent; fail fast |

## 6. U.S. Bureau of Labor Statistics CPI-U — inflation deflator (JSON API)

| Item | Verified fact |
|---|---|
| Series | `CUUR0000SA0`: “All items in U.S. city average, all urban consumers, not seasonally adjusted”, base period 1982-84=100 (https://data.bls.gov/timeseries/CUUR0000SA0) |
| Endpoint | BLS Public Data API **v1** (no registration): `POST https://api.bls.gov/publicAPI/v1/timeseries/data/` with `{"seriesid":["CUUR0000SA0"],"startyear":"2000","endyear":"2009","annualaverage":true}` |
| Limits | https://www.bls.gov/developers/api_faqs.htm: v1 allows 25 queries/day, 10 years and 25 series per query, and lists “Optional annual averages: No” for v1. In practice v1 returned the published annual average (period `M13`) on 2026-11-08. The pipeline uses it when present, otherwise the mean of 12 months; the two agree exactly for 2020–2024 |
| Coverage | *Measured*: monthly values 1990–2026-09 (477 data points loaded). **October 2025 is not published** (value `-`, footnote “Data unavailable due to the 2025 lapse in appropriations”); 2025 uses the BLS-published annual average 321.943 |
| Terms | https://www.bls.gov/opub/copyright-information.htm: public domain; cite the Bureau of Labor Statistics. https://www.bls.gov/developers/termsOfService.htm: cite the retrieval date and state “BLS.gov cannot vouch for the data or analyses derived from these data after the data have been retrieved from BLS.gov.” |
| Fallback | None keyless. The BLS flat files (`download.bls.gov/pub/time.series/cu/`) return HTTP 403 to clients that do not send personal contact details; API v2 needs a personal key. The pipeline fails fast; raw responses are kept in the object store |
| Usage | A national run needs 4 queries (decades 1990s–2020s); `source-check` uses 1; the ingestion preflight uses HEAD (no query) |
