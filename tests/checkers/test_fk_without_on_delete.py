from pgsleuth.checkers.fk_without_on_delete import ForeignKeyWithoutOnDelete


def test_clean_when_on_delete_cascade(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id) ON DELETE CASCADE"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_when_on_delete_restrict(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id) ON DELETE RESTRICT"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_clean_when_on_delete_set_null(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id) ON DELETE SET NULL"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_implicit_no_action(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id)"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "child" in issues[0].object_name
    assert "parent_id" in issues[0].object_name


def test_flags_explicit_no_action(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id) ON DELETE NO ACTION"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1


def test_composite_fk_columns_listed_in_order(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (a bigint, b bigint, PRIMARY KEY (a, b))")
        cur.execute(
            "CREATE TABLE child ("
            "  id  bigserial PRIMARY KEY,"
            "  pa  bigint,"
            "  pb  bigint,"
            "  FOREIGN KEY (pa, pb) REFERENCES parent(a, b)"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "(pa, pb)" in issues[0].object_name


def test_no_fks_means_no_findings(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE solo (id bigserial PRIMARY KEY, name text)")

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_suggestion_references_actual_parent(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child ("
            "  id bigserial PRIMARY KEY,"
            "  parent_id bigint REFERENCES parent(id)"
            ")"
        )

    issues = [i for i in ForeignKeyWithoutOnDelete().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert f"REFERENCES {schema}.parent(id)" in issues[0].suggestion


def test_suggestion_qualifies_cross_schema_parent(ctx, conn, schema):
    other = f"{schema}_other"
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {other}")
        try:
            cur.execute(f"CREATE TABLE {other}.parent (id bigserial PRIMARY KEY)")
            cur.execute(
                f"CREATE TABLE child ("
                f"  id bigserial PRIMARY KEY,"
                f"  parent_id bigint REFERENCES {other}.parent(id)"
                f")"
            )

            issues = [
                i
                for i in ForeignKeyWithoutOnDelete().run(ctx)
                if i.object_name.startswith(schema)
            ]
            assert len(issues) == 1
            assert f"REFERENCES {other}.parent(id)" in issues[0].suggestion
        finally:
            cur.execute(f"DROP SCHEMA {other} CASCADE")
