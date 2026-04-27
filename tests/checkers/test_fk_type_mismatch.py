from pgsleuth.checkers.fk_type_mismatch import ForeignKeyTypeMismatch


def test_clean_when_types_match(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigint PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child (id bigserial PRIMARY KEY, parent_id bigint REFERENCES parent(id))"
        )

    issues = [i for i in ForeignKeyTypeMismatch().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_mismatch(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigint PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child (id bigserial PRIMARY KEY, parent_id integer REFERENCES parent(id))"
        )

    issues = [i for i in ForeignKeyTypeMismatch().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "parent_id" in issues[0].object_name
    assert "integer" in issues[0].message
    assert "bigint" in issues[0].message
