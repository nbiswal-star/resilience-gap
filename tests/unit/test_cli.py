import pytest

from resgap.cli import SOURCES, build_parser


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--profile", "full", "run"], "full"),
        (["run", "--profile", "full"], "full"),
        (["--profile", "fixture", "ingest", "openfema"], "fixture"),
        (["transform"], None),  # falls back to RESGAP_PROFILE / 'sample' in load_settings
    ],
)
def test_profile_accepted_before_or_after_command(argv, expected):
    assert build_parser().parse_args(argv).profile == expected


def test_ingest_source_names_match_registry():
    from resgap.config import load_sources

    registry = set(load_sources())
    assert set(SOURCES) == registry
