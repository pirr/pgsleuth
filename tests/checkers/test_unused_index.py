from pgsleuth.checkers.unused_index import UnusedIndex


def test_flags_never_scanned_index(ctx, conn, schema):
    """A vanilla index with zero scans is flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int)")
        cur.execute("CREATE INDEX cold_idx ON t (b)")

    issues = [i for i in UnusedIndex().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name.endswith(".cold_idx")
    assert "idx_scan = 0" in issues[0].message
    assert "DROP INDEX" in issues[0].suggestion


def test_clean_when_index_is_primary_key(ctx, conn, schema):
    """Primary-key indexes enforce correctness — never flagged as unused."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, a int)")

    issues = [i for i in UnusedIndex().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_when_index_is_unique_constraint(ctx, conn, schema):
    """UNIQUE constraints' backing indexes enforce correctness — not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, email text UNIQUE)")

    issues = [i for i in UnusedIndex().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_when_index_is_exclusion_constraint(ctx, conn, schema):
    """EXCLUDE constraints' backing indexes enforce correctness — not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        cur.execute(
            "CREATE TABLE bookings ("
            "  id bigserial PRIMARY KEY,"
            "  room_id int,"
            "  during tsrange,"
            "  EXCLUDE USING gist (room_id WITH =, during WITH &&)"
            ")"
        )

    issues = [i for i in UnusedIndex().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_only_unused_when_mixed(ctx, conn, schema):
    """A table with one unused secondary index and one PK — only the secondary is flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, a int, b int)")
        cur.execute("CREATE INDEX cold_a ON t (a)")
        cur.execute("CREATE INDEX cold_b ON t (b)")

    issues = [i for i in UnusedIndex().run(ctx) if i.object_name.startswith(schema)]
    flagged = sorted(i.object_name.rsplit(".", 1)[-1] for i in issues)
    assert flagged == ["cold_a", "cold_b"]
