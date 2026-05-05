from pgsleuth.checkers.missing_replica_identity import MissingReplicaIdentity


def test_clean_when_table_has_pk(ctx, conn, schema):
    """Default replica identity + PK → working identity, not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, v int)")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_table_with_default_identity_and_no_pk(ctx, conn, schema):
    """Default identity but no PK → effective identity is empty, flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int)")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name.endswith(".t")
    assert "no primary key" in issues[0].message
    assert "ADD PRIMARY KEY" in issues[0].suggestion


def test_clean_with_replica_identity_full(ctx, conn, schema):
    """FULL works without a PK — not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int)")
        cur.execute("ALTER TABLE t REPLICA IDENTITY FULL")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_with_replica_identity_using_index(ctx, conn, schema):
    """USING INDEX on a unique non-null index works without a PK — not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (k int NOT NULL, v int)")
        cur.execute("CREATE UNIQUE INDEX t_k_idx ON t (k)")
        cur.execute("ALTER TABLE t REPLICA IDENTITY USING INDEX t_k_idx")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_explicit_replica_identity_nothing(ctx, conn, schema):
    """Even with a PK, explicit NOTHING disables logical-rep identity — flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, v int)")
        cur.execute("ALTER TABLE t REPLICA IDENTITY NOTHING")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "NOTHING" in issues[0].message
    assert "REPLICA IDENTITY DEFAULT" in issues[0].suggestion


def test_clean_for_unlogged_table_without_pk(ctx, conn, schema):
    """Unlogged tables aren't replicated — not flagged."""
    with conn.cursor() as cur:
        cur.execute("CREATE UNLOGGED TABLE t (a int, b int)")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_does_not_double_flag_partitions(ctx, conn, schema):
    """A partitioned table without PK is flagged once at the parent — not per partition."""
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int) PARTITION BY RANGE (a)")
        cur.execute("CREATE TABLE t_p1 PARTITION OF t FOR VALUES FROM (0) TO (100)")
        cur.execute("CREATE TABLE t_p2 PARTITION OF t FOR VALUES FROM (100) TO (200)")

    issues = [i for i in MissingReplicaIdentity().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name.endswith(".t")
