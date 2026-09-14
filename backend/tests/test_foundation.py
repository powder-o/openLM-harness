def test_sample_notebook_and_fts(conn, sample_notebook):
    hits = conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'entropy'").fetchall()
    assert len(hits) >= 3
    conn.execute("DELETE FROM documents WHERE id = ?", (sample_notebook["doc_ids"][1],))
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'carnot'").fetchone()[0] == 0


def test_app_boots(client):
    assert client.get("/docs").status_code == 200
