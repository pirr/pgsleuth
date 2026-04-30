from pgsleuth.checkers.json_over_jsonb import JsonOverJsonb


def test_clean_when_jsonb(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, payload jsonb)")

    issues = [i for i in JsonOverJsonb().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_json(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, payload json)")

    issues = [i for i in JsonOverJsonb().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.payload"


def test_flags_only_json_columns(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE t ("
            "  id      bigserial PRIMARY KEY,"
            "  payload json,"
            "  meta    jsonb,"
            "  notes   text"
            ")"
        )

    issues = [i for i in JsonOverJsonb().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.payload"


def test_dropped_column_is_ignored(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, payload json)")
        cur.execute("ALTER TABLE t DROP COLUMN payload")

    issues = [i for i in JsonOverJsonb().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []
