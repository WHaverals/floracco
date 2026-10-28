"""workflows/backup_verify.py: the decisions that make a backup trustworthy.

    uv run --with pytest pytest tests/test_backup_verify.py
"""

from __future__ import annotations

import hashlib
import sqlite3

from workflows import backup_verify as bv


def _db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("create table t (id integer primary key, v text)")
    conn.executemany("insert into t values (?, ?)", rows)
    conn.commit()
    conn.close()


def test_diff_databases_names_the_differing_cell(tmp_path):
    _db(tmp_path / "rebuild.db", [(1, "x"), (2, "y")])
    _db(tmp_path / "live.db", [(1, "x"), (2, "z")])
    diff = bv.diff_databases(tmp_path / "rebuild.db", tmp_path / "live.db")
    assert diff["tables_compared"] == 1 and list(diff["differences"]) == ["t"]
    sample = diff["differences"]["t"]["samples"][0]
    assert sample["pk"] == [2] and sample["columns"] == ["v"]
    assert sample["rebuild"] == ["y"] and sample["live"] == ["z"]
    assert bv.equivalence_summary(diff) == "t: 1 row(s) differ (v)"


def test_diff_databases_identical_and_extra_rows(tmp_path):
    _db(tmp_path / "a.db", [(1, "x")])
    _db(tmp_path / "b.db", [(1, "x")])
    same = bv.diff_databases(tmp_path / "a.db", tmp_path / "b.db")
    assert not same["differences"] and bv.equivalence_summary(same) == "identical (1 tables)"
    _db(tmp_path / "c.db", [(1, "x"), (2, "extra")])
    more = bv.diff_databases(tmp_path / "a.db", tmp_path / "c.db")
    assert more["differences"]["t"]["rows_only_in_live"] == 1
    assert more["differences"]["t"]["samples"][0] == {"pk": [2], "only_in": "live"}


def test_fingerprint_tracks_human_work_only(tmp_path):
    (tmp_path / "corrections.db").write_bytes(b"log v1")
    (tmp_path / "main.db").write_bytes(b"db v1")
    first = bv.fingerprint(tmp_path)
    (tmp_path / "main.db").write_bytes(b"db v2")  # derived file: does not count
    assert bv.fingerprint(tmp_path) == first
    (tmp_path / "corrections.db").write_bytes(b"log v2")  # the log: counts
    assert bv.fingerprint(tmp_path) != first


def test_check_sums_catches_a_changed_byte_and_a_missing_file(tmp_path):
    (tmp_path / "corrections.db").write_bytes(b"abc")
    (tmp_path / "x.jsonl").write_bytes(b"{}\n")
    listing = "".join(
        f"{hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}  {name}\n"
        for name in ("corrections.db", "x.jsonl")
    )
    (tmp_path / "SHA256SUMS").write_text(listing)
    assert bv.check_sums(tmp_path) == (["corrections.db", "x.jsonl"], [])
    (tmp_path / "x.jsonl").write_bytes(b"{ }\n")
    assert bv.check_sums(tmp_path)[1] == ["x.jsonl"]
    (tmp_path / "x.jsonl").unlink()
    assert bv.check_sums(tmp_path)[1] == ["x.jsonl"]


def test_unchanged_decision_drops_only_a_true_repeat():
    prev = {"backup_stamp": "20260101T000000Z", "fingerprint": "f",
            "equivalence": {"summary": "identical (11 tables)"}, "status": "OK"}
    assert bv.is_unchanged(prev, "f", "identical (11 tables)", "OK")
    assert not bv.is_unchanged(prev, "g", "identical (11 tables)", "OK")  # the log changed
    assert not bv.is_unchanged(prev, "f", "contract: 1 row(s) differ (firm_name)", "WARN")  # live db drifted
    assert not bv.is_unchanged(prev, "f", "identical (11 tables)", "FAIL")  # never drop a failing folder
    assert not bv.is_unchanged(None, "f", "identical (11 tables)", "OK")  # nothing to compare with
    prev_fail = dict(prev, status="FAIL")
    assert not bv.is_unchanged(prev_fail, "f", "identical (11 tables)", "OK")  # recovery run is kept


def test_previous_manifest_picks_the_latest_earlier_stamp(tmp_path):
    for stamp, fp in (("20260101T000000Z", "a"), ("20260301T000000Z", "b"), ("20260501T000000Z", "c")):
        (tmp_path / stamp).mkdir()
        (tmp_path / stamp / "manifest.json").write_text('{"fingerprint": "%s"}' % fp)
    (tmp_path / "notes").mkdir()  # not a stamp folder: ignored
    prev = bv.previous_manifest(tmp_path, "20260401T000000Z")
    assert prev["backup_stamp"] == "20260301T000000Z" and prev["fingerprint"] == "b"
    assert bv.previous_manifest(tmp_path, "20250101T000000Z") is None
