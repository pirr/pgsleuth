from pgsleuth.checkers.missing_fk_index import MissingForeignKeyIndex


def test_clean_when_fk_has_index(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child (id bigserial PRIMARY KEY, parent_id bigint REFERENCES parent(id))"
        )
        cur.execute("CREATE INDEX ON child (parent_id)")

    issues = [i for i in MissingForeignKeyIndex().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_missing_index(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child (id bigserial PRIMARY KEY, parent_id bigint REFERENCES parent(id))"
        )

    issues = [i for i in MissingForeignKeyIndex().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "child" in issues[0].object_name
    assert "parent_id" in issues[0].object_name
