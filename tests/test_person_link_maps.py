"""Read-time folding of reviewed person identities."""

from __future__ import annotations

import sqlite3

from workflows import corrections_db, review_server


def test_hidden_bridge_does_not_break_visible_identity_family(tmp_path, monkeypatch):
    corr_path = tmp_path / "corrections.db"
    clog = corrections_db.connect(corr_path)
    corrections_db.add_reference_link(
        clog, kind="person", rel="same_as", from_id=1, to_id=2, by="FT"
    )
    corrections_db.add_reference_link(
        clog, kind="person", rel="same_as", from_id=2, to_id=3, by="FT"
    )
    clog.close()

    main = sqlite3.connect(":memory:")
    main.row_factory = sqlite3.Row
    main.executescript(
        """
        CREATE TABLE person (person_id INTEGER PRIMARY KEY, is_deleted INTEGER);
        INSERT INTO person VALUES (1,0),(2,1),(3,0);
        """
    )
    monkeypatch.setattr(
        review_server, "open_corrections", lambda: corrections_db.connect(corr_path)
    )
    mapping = review_server.person_link_maps(main)
    assert mapping[3] == 1
    assert 2 not in mapping
    main.close()

