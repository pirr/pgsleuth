from pgsleuth.checkers.missing_primary_key import MissingPrimaryKey


def test_clean_when_pk_present(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY)")

    issues = [i for i in MissingPrimaryKey().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_table_without_pk(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (a int, b int)")

    issues = [i for i in MissingPrimaryKey().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t"
