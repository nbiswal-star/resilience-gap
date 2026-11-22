import json

import pytest

from resgap.config import Profile
from resgap.ingest.openfema import (
    Scope,
    build_scope,
    canonical_record_hash,
    iter_pages,
    snapshot_hash,
)


def test_scope_filter_for_states_and_dates():
    p = Profile("t", 2022, 2024, ("PA", "OH"))
    s = build_scope({"state_field": "state", "date_field": "incidentBeginDate"}, p)
    assert s.key == "states=OH,PA;years=2022-2024"
    assert "(state eq 'PA' or state eq 'OH')" in s.filter
    assert "incidentBeginDate ge '2022-01-01T00:00:00.000Z'" in s.filter
    assert len(s.scope_id) == 10


def test_scope_without_states_uses_year_field():
    p = Profile("f", 2000, 2025, None)
    s = build_scope({"state_field": "x", "year_field": "fiscalYear"}, p)
    assert s.key == "states=ALL;years=2000-2025"
    assert s.filter == "fiscalYear ge 2000 and fiscalYear le 2025"


def test_snapshot_hash_is_order_independent_and_content_sensitive():
    a, b = {"id": "1", "v": 1}, {"id": "2", "v": 2}
    h1 = snapshot_hash([canonical_record_hash(a), canonical_record_hash(b)])
    h2 = snapshot_hash([canonical_record_hash(b), canonical_record_hash(a)])
    assert h1 == h2
    assert canonical_record_hash({"v": 1, "id": "1"}) == canonical_record_hash(a)
    assert snapshot_hash([canonical_record_hash({"id": "1", "v": 9})]) != h1


class FakeResponse:
    def __init__(self, doc):
        self.content = json.dumps(doc).encode()

    def raise_for_status(self):
        pass


class FakeSession:
    """Serves `total` records in pages, honouring $top/$skip like the real API."""

    def __init__(self, total):
        self.total = total
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        top, skip = params["$top"], params["$skip"]
        recs = [{"id": str(i)} for i in range(skip, min(skip + top, self.total))]
        meta = {"count": self.total if params.get("$count") else 0}
        return FakeResponse({"metadata": meta, "Things": recs})


def test_iter_pages_pages_until_short_page():
    sess = FakeSession(25)
    pages = list(iter_pages(sess, "u", "Things", Scope("k", None), 10, 5))
    assert [len(p[2]) for p in pages] == [10, 10, 5]
    assert pages[0][3] == 25 and pages[1][3] is None
    assert all(c["$orderby"] == "id" for c in sess.calls)
    assert "$count" in sess.calls[0] and "$count" not in sess.calls[1]


def test_iter_pages_exact_multiple_ends_with_empty_page():
    pages = list(iter_pages(FakeSession(20), "u", "Things", Scope("k", None), 10, 5))
    assert [len(p[2]) for p in pages] == [10, 10, 0]


def test_iter_pages_surfaces_api_errors():
    class ErrSession:
        def get(self, *a, **k):
            return FakeResponse({"error": [{"message": "bad filter"}]})

    from resgap.http import SourceUnavailableError

    with pytest.raises(SourceUnavailableError):
        list(iter_pages(ErrSession(), "u", "Things", Scope("k", "x"), 10, 5))
