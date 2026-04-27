from pgsleuth.checkers.primary_key_type import PrimaryKeyType


def test_clean_when_bigint(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY)")

    issues = [i for i in PrimaryKeyType().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_int_pk(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id serial PRIMARY KEY)")

    issues = [i for i in PrimaryKeyType().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "id" in issues[0].object_name
