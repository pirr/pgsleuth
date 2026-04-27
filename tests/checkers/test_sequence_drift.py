from pgsleuth.checkers.sequence_drift import SequenceDrift


def test_clean_when_sequence_in_sync(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, name text)")
        cur.execute("INSERT INTO t (name) VALUES ('a'), ('b'), ('c')")

    issues = [i for i in SequenceDrift().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_sequence_drift(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, name text)")
        # Insert with explicit ids past the sequence head; do NOT advance the
        # sequence, mimicking a pg_restore that loaded data via COPY.
        cur.execute("INSERT INTO t (id, name) VALUES (1000, 'a'), (1001, 'b')")
        cur.execute(f"SELECT setval('{schema}.t_id_seq', 1, false)")

    issues = [i for i in SequenceDrift().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "t_id_seq" in issues[0].object_name
    assert issues[0].extra["max_value"] == "1001"
    assert int(issues[0].extra["next_value"]) <= 1001
