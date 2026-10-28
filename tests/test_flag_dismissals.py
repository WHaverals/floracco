"""Needs review: a dismissal can be undone (UI review 2026-10-14, item 2).

"Not an issue" appends a row to an append-only log; Undo appends a "restore"
row. A key's last row decides, and rows written before the undo existed carry
no action and still count as dismissals.

    uv run --with pytest pytest tests/test_flag_dismissals.py
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from workflows import review_server


@pytest.fixture()
def log(tmp_path, monkeypatch):
    path = tmp_path / "10_corrections" / "flag_dismissals.jsonl"
    monkeypatch.setattr(review_server, "FLAG_DISMISSALS_PATH", path)
    return path


def test_dismiss_then_undo(log) -> None:
    review_server.dismiss_flag("contract:1:no_investors", review_server.FlagDismiss(reviewer="WH", reason="checked"))
    assert review_server.dismissed_flag_keys() == {"contract:1:no_investors"}
    review_server.restore_flag("contract:1:no_investors", review_server.FlagRestore(reviewer="WH"))
    assert review_server.dismissed_flag_keys() == set()
    # Append-only: both rows stay in the log.
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["action"] for r in rows] == ["dismiss", "restore"]
    assert rows[0]["reason"] == "checked"


def test_old_rows_without_action_are_dismissals(log) -> None:
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"key": "contract:2:no_place", "reviewer": "FT", "reason": "", "at": "2026-10-01"}) + "\n")
    assert review_server.dismissed_flag_keys() == {"contract:2:no_place"}
    review_server.restore_flag("contract:2:no_place", review_server.FlagRestore(reviewer="WH"))
    assert review_server.dismissed_flag_keys() == set()
    # Dismissed again after the undo: the last row wins.
    review_server.dismiss_flag("contract:2:no_place", review_server.FlagDismiss(reviewer="WH"))
    assert review_server.dismissed_flag_keys() == {"contract:2:no_place"}


def test_undo_of_an_item_that_is_not_dismissed_is_refused(log) -> None:
    with pytest.raises(HTTPException) as err:
        review_server.restore_flag("contract:3:no_place", review_server.FlagRestore(reviewer="WH"))
    assert err.value.status_code == 409
    assert not log.exists()
