from pgsleuth.checkers.not_valid_constraints import NotValidConstraints


def test_clean_when_validated(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, age int CHECK (age >= 0))")

    issues = [i for i in NotValidConstraints().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_not_valid_check(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, age int)")
        cur.execute("ALTER TABLE t ADD CONSTRAINT t_age_chk CHECK (age >= 0) NOT VALID")

    issues = [i for i in NotValidConstraints().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "t_age_chk" in issues[0].object_name


def test_flags_not_valid_fk(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE parent (id bigserial PRIMARY KEY)")
        cur.execute("CREATE TABLE child (id bigserial PRIMARY KEY, parent_id bigint)")
        cur.execute(
            "ALTER TABLE child ADD CONSTRAINT child_parent_fk "
            "FOREIGN KEY (parent_id) REFERENCES parent(id) NOT VALID"
        )

    issues = [i for i in NotValidConstraints().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert "child_parent_fk" in issues[0].object_name
