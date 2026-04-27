from pgsleuth.checkers.redundant_index import RedundantIndex


def test_clean_when_indexes_disjoint(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int, c int)")
        cur.execute("CREATE INDEX ON t (a)")
        cur.execute("CREATE INDEX ON t (b, c)")

    issues = [i for i in RedundantIndex().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_prefix_index(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int)")
        cur.execute("CREATE INDEX prefix_idx ON t (a)")
        cur.execute("CREATE INDEX wider_idx ON t (a, b)")

    issues = [i for i in RedundantIndex().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "prefix_idx" in issues[0].object_name
    assert "wider_idx" in issues[0].message
