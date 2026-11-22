# Test fixtures — real data excerpts

These files are **subsets of real source files and real API responses**, produced by
`scripts/build_fixtures.py` on 2026-11-08. Nothing here is synthetic or edited by hand.

| Directory | Source | Excerpt rule |
|---|---|---|
| `noaa_storm_events/` | NOAA NCEI Storm Events, `StormEvents_details-ftp_v1.0_d2024_c20260728.csv.gz` | Header + Pennsylvania, Puerto Rico (code 99) and Virgin Islands (code 96) events that began July–September 2024 (789 rows; includes Tropical Storm Debby and Hurricane Ernesto) |
| `nws_zone_county/` | NWS-hosted correlation files `bp05mr24`, `bp10se24`, `bp16ap26` | PA, PR and VI rows (the versions valid during 2024 plus the current one) |
| `nws_zone_county/internet_archive/` | Internet Archive capture of NWS `bp04ap17.dbx` (SHA-1 verified at build time) | PA, PR and VI rows (exercises the archive tier) |
| `openfema/<dataset>/page-00001.json` | OpenFEMA API, unmodified response bodies | `fixture` profile filter: PA, PR, VI; 2024 |
| `openfema_examples/DisasterDeclarationsSummaries/code000-statewide-and-tribal.json` | OpenFEMA API, one unmodified response body (retrieved 2026-11-08) | Every `fipsCountyCode = '000'` record of DR-1485-PA and DR-4669-AS (literal "Statewide") and of DR-4013-NE (Omaha Indian Reservation), DR-4844-FL (seven Seminole reservations, tribal request), DR-1423-AK (four Alaska Native villages) and DR-4802-OK (Chickasaw OTSA): 15 records. **Not read by the offline run**; used by the statewide/tribal regression tests |
| `cdc_svi/` | CDC/ATSDR SVI 2022 U.S. county CSV | Header + Pennsylvania rows |
| `cdc_svi_pr/` | CDC/ATSDR SVI 2022 Puerto Rico file | Complete file (78 municipios) |
| `census_popest/` | Census Vintage 2025 `co-est2025-alldata.csv` | Header + Pennsylvania state and county rows |
| `census_popest_pr/` | Census `cc-est2025-agesex-72.csv` | Complete file (78 municipios × 7 years) |
| `census_county_reference/` | Census `national_county2020.txt` | Header + PA, PR and VI rows |
| `bls_cpi/` | BLS Public Data API v1 response, series CUUR0000SA0, 2020–2029 request | Complete response |

Because the fixture deliberately contains no version between `bp10se24` and `bp16ap26`, `bp10se24`
appears to sit in a gap in the fixture, so its matches must be name-verified. Two real events in
zones PA105/PA106 ("UPPER/LOWER BUCKS" vs the crosswalk name "Bucks") therefore stay unmapped in
the fixture run. The tests assert exactly that "no guessing" behaviour.

The integration tests run the production ingestion code against this directory with
`--offline-dir tests/fixtures --profile fixture`, so CI never depends on the network.
Regenerate with `make fixtures` (network). When the agencies have not revised their data, the
only change is the `metadata.rundate` timestamp inside the three OpenFEMA pages and the OpenFEMA
example file (checked 2026-11-08 for the pages). If records do change, update test expectations deliberately.
