from pgsleuth.checkers.varchar_length import VarcharLength


def test_clean_when_text(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, name text)")

    issues = [i for i in VarcharLength().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_when_unbounded_varchar(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, name varchar)")

    issues = [i for i in VarcharLength().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_varchar_with_length(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, email varchar(255))")

    issues = [i for i in VarcharLength().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.email"
    assert issues[0].extra["length"] == "255"


def test_flags_only_capped_columns(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE t ("
            "  id      bigserial PRIMARY KEY,"
            "  email   varchar(320),"
            "  name    varchar,"
            "  bio     text"
            ")"
        )

    issues = [i for i in VarcharLength().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.email"


def test_dropped_column_is_ignored(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, email varchar(255))")
        cur.execute("ALTER TABLE t DROP COLUMN email")

    issues = [i for i in VarcharLength().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []
