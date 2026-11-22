"""Static checks of the FEMA area/hazard/region rules, their real-data fixture, and the guard
that keeps integration tests away from the pipeline database (no database needed)."""

import csv
import json

import pytest

import tests.conftest as root_conftest
from resgap.config import SEEDS_DIR
from resgap.transform import SEEDS, significant_damage_real_usd
from tests.conftest import FIXTURES, assert_not_pipeline_database, database_identity

EXAMPLES = (
    FIXTURES
    / "openfema_examples"
    / "DisasterDeclarationsSummaries"
    / "code000-statewide-and-tribal.json"
)


def _seed(name):
    with open(SEEDS_DIR / f"{name}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_code000_example_is_an_unmodified_api_response():
    doc = json.loads(EXAMPLES.read_bytes())
    recs = doc["DisasterDeclarationsSummaries"]
    assert len(recs) == doc["metadata"]["count"] == 15
    assert doc["metadata"]["entityname"] == "DisasterDeclarationsSummaries"
    assert {r["fipsCountyCode"] for r in recs} == {"000"}
    statewide = {r["femaDeclarationString"] for r in recs if r["designatedArea"] == "Statewide"}
    assert statewide == {"DR-1485-PA", "DR-4669-AS"}
    tribal = [r for r in recs if r["designatedArea"] != "Statewide"]
    # the bug this guards against: code 000 rows that are reservations, OTSAs and Alaska Native
    # villages, most of them WITHOUT tribalRequest
    assert len(tribal) == 13
    assert any(not r["tribalRequest"] for r in tribal)
    assert all(r["placeCode"] not in ("0", "", None) for r in tribal)
    assert all(r["placeCode"] == "0" for r in recs if r["designatedArea"] == "Statewide")


def test_hazard_match_rules_are_consistent():
    families = {r["hazard_family"] for r in _seed("hazard_crosswalk")}
    fema_weather = {
        r["hazard_family"]
        for r in _seed("hazard_crosswalk")
        if r["source"] == "fema" and r["is_weather"] == "true"
    }
    rules = _seed("hazard_match_rules")
    pairs = {(r["declaration_hazard_family"], r["event_hazard_family"]) for r in rules}
    assert len(pairs) == len(rules)
    for r in rules:
        assert r["declaration_hazard_family"] in families
        assert r["event_hazard_family"] in families
        assert r["rule"] == (
            "same_family"
            if r["declaration_hazard_family"] == r["event_hazard_family"]
            else "companion_hazard"
        )
    # every weather declaration family covers at least its own family
    assert {(f, f) for f in fema_weather} <= pairs
    # broad or unknown NOAA families never match anything
    for never in ("Heat", "Other Weather", "Unmapped", "Other", "Non-natural", "Geologic"):
        assert not any(e == never for _, e in pairs)
    # examples of cross-hazard matches that must stay impossible
    assert ("Flood", "Wildfire") not in pairs
    assert ("Severe Storm & Wind", "Drought") not in pairs
    assert ("Wildfire", "Severe Storm & Wind") not in pairs


def test_fema_region_seed():
    rows = _seed("fema_regions")
    states = [r["state_abbr"] for r in rows]
    assert len(states) == len(set(states))
    assert {int(r["fema_region"]) for r in rows} == set(range(1, 11))
    region = {r["state_abbr"]: int(r["fema_region"]) for r in rows}
    assert (region["PA"], region["PR"], region["VI"], region["AK"]) == (3, 2, 2, 10)
    assert SEEDS["fema_regions"] == ("state_abbr",)


def test_significance_threshold_is_in_real_dollars():
    assert significant_damage_real_usd({"significant_damage_real_usd": 250000}) == 250000
    assert significant_damage_real_usd({"significant_damage_usd": 50000}) == 50000  # old key
    assert significant_damage_real_usd({}) == 100000


@pytest.mark.parametrize(
    "a,b,same",
    [
        (
            "postgresql://resgap:resgap@localhost:5433/resgap",
            "postgresql://resgap:resgap@127.0.0.1:5433/resgap",
            True,
        ),
        ("postgresql://u@localhost/resgap", "postgresql://u@localhost:5432/resgap", True),
        (
            "postgresql://resgap:resgap@localhost:5433/resgap",
            "postgresql://resgap:resgap@localhost:5433/resgap_test",
            False,
        ),
        ("postgresql://u@localhost:5433/resgap", "postgresql://u@localhost:5432/resgap", False),
    ],
)
def test_database_identity(a, b, same):
    assert (database_identity(a) == database_identity(b)) is same


def test_guard_refuses_the_pipeline_database(monkeypatch):
    monkeypatch.setattr(root_conftest, "_server_identity", lambda url: None)
    pipeline = ["postgresql://resgap:resgap@localhost:5433/resgap"]
    with pytest.raises(RuntimeError, match="same database"):
        assert_not_pipeline_database("postgresql://resgap:x@127.0.0.1:5433/resgap", pipeline)
    assert_not_pipeline_database("postgresql://resgap:x@localhost:5433/resgap_test", pipeline)


def test_guard_detects_the_same_server_database_under_another_name(monkeypatch):
    # e.g. the Compose service name "db" vs localhost: same live server and database
    monkeypatch.setattr(root_conftest, "_server_identity", lambda url: ("resgap", 5432, "t0"))
    with pytest.raises(RuntimeError):
        assert_not_pipeline_database(
            "postgresql://resgap@db:5432/resgap", ["postgresql://resgap@localhost:5433/resgap"]
        )
