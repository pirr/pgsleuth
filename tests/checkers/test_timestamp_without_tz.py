from pgsleuth.checkers.timestamp_without_tz import TimestampWithoutTz


def test_clean_when_timestamptz(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, occurred_at timestamptz)")

    issues = [i for i in TimestampWithoutTz().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_timestamp_without_tz(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, occurred_at timestamp)")

    issues = [i for i in TimestampWithoutTz().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.occurred_at"


def test_flags_only_timestamp_columns(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE t ("
            "  id          bigserial PRIMARY KEY,"
            "  occurred_at timestamp,"
            "  created_at  timestamptz,"
            "  date_only   date"
            ")"
        )

    issues = [i for i in TimestampWithoutTz().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t.occurred_at"


def test_dropped_column_is_ignored(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, occurred_at timestamp)")
        cur.execute("ALTER TABLE t DROP COLUMN occurred_at")

    issues = [i for i in TimestampWithoutTz().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []
