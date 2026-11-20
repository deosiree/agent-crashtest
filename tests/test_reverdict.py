"""The re-verdict tool: re-judges recorded trials without editing the files."""

from __future__ import annotations

import json

from crashtest.results import reverdict


def _trial(order_id, effects, outcome, verdict):
    return {
        "order_id": order_id,
        "effects_committed": effects,
        "subject_outcome": outcome,
        "verdict": verdict,
    }


def test_unverifiable_trials_are_counted_apart_not_as_changes(tmp_path, monkeypatch):
    path = tmp_path / "results" / "single_call" / "x.json"
    path.parent.mkdir(parents=True)
    doc = {
        "experiment": "mcp_single_call",
        "trials": [
            _trial("ord-ok", 1, "ok", "correct"),
            _trial("ord-wrong", 1, "ok", "correct"),
            _trial("ord-gone", 2, "ok", "duplicate"),
        ],
    }
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    # ord-gone's refunds are not in this database
    monkeypatch.setattr(
        reverdict, "_payloads", lambda conn, ids: {"ord-ok": [2500], "ord-wrong": [999]}
    )

    report = reverdict.review_file(None, path)  # type: ignore[arg-type]

    assert report["payload_not_verifiable"] == 1
    assert report["changed"] == [
        {"position": 1, "order_id": "ord-wrong", "v0.1": "correct", "v0.2": "wrong_payload"}
    ]
    assert report["v0.2"] == {"correct": 1, "payload_not_verifiable": 1, "wrong_payload": 1}
    assert path.read_bytes() == before  # the results file is never edited
