# Seeds (team-authored reference mappings)

These small CSVs are **reference mappings written by the project team**, not source data.
They are loaded into `staging.seed_*` tables on every `resgap transform`.

| File | Purpose |
|---|---|
| `storm_events_state_codes.csv` | NOAA Storm Events uses its own `STATE_FIPS` codes for territories (verified in the 2022-2024 files): Puerto Rico `99` (Census `72`), Virgin Islands `96` (`78`), American Samoa `97` (`60`), Guam `98` (`66`). Codes 81-95 are marine / Great Lakes pseudo-states (e.g. `85` GULF OF MEXICO, `88` ATLANTIC NORTH, `95` LAKE ERIE); any code >= 80 not listed here is treated as a marine area with no county. |
| `county_name_aliases.csv` | Explicit aliases for names that the normalised name key cannot match, verified against the Census reference file (e.g. FEMA HMA writes `St. Croix`; Census names the county-equivalent `St. Croix Island`, FIPS 78010). |
| `hazard_match_rules.csv` | Which NOAA event hazard families a FEMA declaration of a given hazard family can cover when matching events to declarations (Q2) and summing damage in a declaration's window (Q4): `same_family`, or `companion_hazard` where declarations of that type routinely name the other hazard (evidence in `docs/implementation-plan.md` §4). Pairs not listed never match; Heat, Other Weather and unmapped families have no rule. |
| `fema_regions.csv` | State/territory → FEMA region (1–10). Agrees with the `region` field of every OpenFEMA declaration and denial record (checked 2026-11-08 and on every run by the critical check `fema_region_matches_source`). UM has no FEMA region in the data and is not listed. |
| `hazard_crosswalk.csv` | Maps three independent hazard vocabularies onto one `hazard_family`: NOAA Storm Events `EVENT_TYPE` (`source=noaa`), FEMA declaration `incidentType` (`source=fema`), and FEMA HMA `primaryHazard` (`source=hma`). `is_weather=false` marks non-weather incidents (e.g. COVID-19 "Biological" declarations, earthquakes) that are excluded from weather-hazard measures (declaration coverage, weather-related HMA obligation). |

The source vocabularies were enumerated from the live data on 2026-11-08 (NOAA 2024 details file;
all 70,599 declaration rows; all 63,635 HMA subapplications). Values that appear later and are
not in this file are mapped to `Unmapped` and reported by the `seed_unmapped_hazard_values`
data-quality check, so the mapping can be extended deliberately rather than silently.
