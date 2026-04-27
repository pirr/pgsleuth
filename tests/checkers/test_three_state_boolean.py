from pgsleuth.checkers.three_state_boolean import ThreeStateBoolean


def test_clean_when_not_null(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, active boolean NOT NULL DEFAULT false)")

    issues = [i for i in ThreeStateBoolean().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_nullable_boolean(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, active boolean)")

    issues = [i for i in ThreeStateBoolean().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "active" in issues[0].object_name
