import pytest

from resgap.config import load_profile, load_settings, load_sources
from resgap.dq.runner import evaluate, load_checks


def test_profiles_load():
    p = load_profile("sample")
    assert p.start_year <= p.end_year
    assert "PA" in p.states
    assert load_profile("full").states is None
    assert load_profile("fixture").years == [2024]


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        load_profile("nope")


def test_sources_registry_has_required_fields():
    src = load_sources()
    for name in (
        "noaa_storm_events",
        "nws_zone_county",
        "openfema",
        "cdc_svi",
        "census_popest",
        "census_county_reference",
    ):
        assert src[name]["provider"]
        assert src[name]["terms"]
    assert set(src["openfema"]["datasets"]) == {
        "DisasterDeclarationsSummaries",
        "HmaSubapplications",
        "DeclarationDenials",
    }


def test_dq_checks_are_well_formed():
    checks = load_checks()
    assert len(checks) >= 20
    assert any(c["severity"] == "critical" for c in checks)
    for c in checks:
        assert c["sql"].strip()
        assert c.get("handling"), f"{c['name']} must document how the issue is handled"


def test_hma_funnel_checks_are_registered_and_critical():
    checks = {c["name"]: c for c in load_checks()}
    for name in (
        "hma_raw_status_preserved",
        "hma_positive_obligation_matches_source",
        "hma_period_boundaries",
        "hma_stage_exclusive_exhaustive",
        "hma_period_grid_complete",
        "hma_period_reconciles_profile",
        "hma_county_amounts_reconcile_fact",
        "hma_status_summary_reconciles",
        "hma_program_hazard_reconciles",
        "high_need_low_obligation_flag_exact",
        "hma_stage_summary_reconciles",
        "hma_geography_break_guard",
        "state_year_hma_reconciles",
        "hma_damage_burden_reconciles",
    ):
        assert checks[name]["severity"] == "critical", name
    for name in (
        "hma_counties_not_observed",
        "hma_positive_obligation_under_other_status",
        "hma_geography_not_comparable_units",
        "state_year_hma_out_of_scope",
        "hma_weather_lower_bound_not_specified",
    ):
        assert checks[name]["severity"] == "info", name
    assert checks["hma_records_on_noncomparable_geography"]["severity"] == "warn"
    assert checks["hma_records_on_noncomparable_geography"]["max_fail_ratio"] == 0
    # the retired, misleading names must not come back in any check
    text = " ".join(c["sql"] + c.get("description", "") + c["handling"] for c in checks.values())
    for gone in ("is_funded", "is_priority_gap", "is_low_mitigation", "priority_gap"):
        assert gone not in text, gone


def test_dq_check_count_and_unique_names():
    checks = load_checks()
    names = [c["name"] for c in checks]
    assert len(names) == len(set(names))
    # 64 checks before the Connecticut geography / scope / alignment checks, 7 added with them,
    # 4 added with the storm / declaration geography break
    assert len(checks) == 75


def test_storm_declaration_geography_checks_are_registered():
    checks = {c["name"]: c for c in load_checks()}
    for name in ("storm_declaration_geography_break_guard", "history_summaries_reconcile"):
        assert checks[name]["severity"] == "critical", name
    assert checks["storm_declaration_geography_not_comparable_units"]["severity"] == "info"
    assert checks["history_facts_on_noncomparable_geography"]["severity"] == "warn"
    assert checks["history_facts_on_noncomparable_geography"]["max_fail_ratio"] == 0


def test_storm_history_checks_do_not_merely_repeat_the_stored_flag():
    """The guard is triggered by facts on legacy geography, and the recounts re-derive storm
    history comparability from analysis_universe + state; none of them may take the stored
    storm_declaration_geography_comparable flag as the definition of comparability."""
    checks = {c["name"]: c for c in load_checks()}
    guard = checks["storm_declaration_geography_break_guard"]["sql"]
    assert "curated.fact_storm_event_county" in guard
    assert "curated.fact_declaration_area" in guard
    assert "legacy_geography" in guard
    # the guard checks the stored flags, it does not select units by them
    assert "dim_flag is distinct from false" in guard
    for name in (
        "history_summaries_reconcile",
        "high_need_low_obligation_flag_exact",
        "hma_damage_burden_reconciles",
        "serving_damage_reconciles",
    ):
        sql = checks[name]["sql"]
        assert "legacy_geography" in sql and "state_fips" in sql, name
        assert "curated.fact_storm_event_county" in sql, name
        assert "storm_declaration_geography_comparable" not in sql, name


def test_geography_comparability_reaches_every_hma_stage_check():
    checks = {c["name"]: c for c in load_checks()}
    for name in (
        "hma_stage_exclusive_exhaustive",
        "hma_stage_summary_reconciles",
        "high_need_low_obligation_flag_exact",
        "hma_damage_burden_reconciles",
    ):
        # comparability is taken from the dimension, not from the serving table being checked
        assert "curated.dim_county" in checks[name]["sql"], name
        assert "hma_geography_comparable" in checks[name]["sql"], name
    guard = checks["hma_geography_break_guard"]["sql"]
    assert "legacy_geography" in guard and "geography_not_comparable" in guard
    # every Q1/Q2 recount in a stage check also excludes units without comparable storm history
    assert "legacy_geography" in checks["high_need_low_obligation_flag_exact"]["sql"]
    assert "legacy_geography" in checks["hma_damage_burden_reconciles"]["sql"]
    # the positive-obligation check reads the raw OpenFEMA JSON, not only staging
    assert "raw.openfema_records" in checks["hma_positive_obligation_matches_source"]["sql"]


@pytest.mark.parametrize(
    "failing,total,max_ratio,ok",
    [
        (0, 100, 0, True),
        (1, 100, 0, False),
        (5, 100, 0.05, True),
        (6, 100, 0.05, False),
        (0, 0, 0, True),
        (3, 0, 0, False),
    ],
)
def test_evaluate(failing, total, max_ratio, ok):
    assert evaluate(failing, total, max_ratio)[1] is ok


def test_lake_dir_expands_home(monkeypatch, tmp_path):
    # e.g. RESGAP_LAKE_DIR=~/Library/CloudStorage/Dropbox/resgap-lake for the shared folder
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESGAP_LAKE_DIR", "~/Dropbox/resgap-lake")
    assert load_settings().lake_dir == tmp_path / "Dropbox" / "resgap-lake"
