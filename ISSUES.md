# Open issues from the CipherStash port

This file lists the problems found while porting Endurain to the CipherStash Python SDK, `stackencrypt`.
None of them is filed anywhere else yet.

Terms used here:

- **EQL** (Encrypt Query Language) is the SQL library installed in the database. It defines the encrypted column types, such as `public.eql_v4_text_eq`, and the functions that search them.
- **The SDK** is `stackencrypt`, from `languages/python` in the `cipherstash/stack` repository.
- An **order term** is a value stored with an encrypted value. It sorts in the same order as the plaintext. `eql_v4.ord_term(col)` returns it.

| Number | Where the fix belongs | Summary |
|---|---|---|
| 1 | EQL | A plain `ORDER BY` on an encrypted column returns a wrong order. |
| 2 | EQL and the SDK docs | A second install of the EQL bundle silently drops indexes. |
| 3 | The SDK | An app cannot build a query term without credentials. |
| 4 | Endurain | The monthly summary fails on PostgreSQL with `GroupingError`. |
| 5 | Endurain | Alembic autogenerate finds differences that stock Endurain already has. |

## 1. A plain ORDER BY on an encrypted column returns a wrong order

### What happens

The EQL v4 ord column types are domains over `jsonb`. EQL defines `<`, `<=`, `>`, and `>=` for them, but no sort order. So `ORDER BY col` sorts the stored JSON. The rows come back in a wrong order, and there is no error.

`ORDER BY eql_v4.ord_term(col)` returns the correct order.

### Effect on Endurain

None today. Endurain sorts by an encrypted column in one place: `get_users_with_pagination` sorts by `username` (`backend/app/modules/users/users/crud.py:328`). The SDK's SQLAlchemy layer changes that sort to `eql_v4.ord_term(username)`. ADR-0004, decision 9, in the SDK records this.

The problem still affects any SQL that does not go through SQLAlchemy's expression layer. Examples are raw SQL, `psql`, reports, and other SDKs.

No Endurain test checks the username order. The tests of `get_users_with_pagination` use a mock session.

### Evidence

This was tested on `cipherstash/stack` commit `7073225cd` with PostgreSQL 17.11. It needs Docker, mise, and Python 3, and no CipherStash credentials. It stores three rows from the committed fixture `packages/eql/tests/encryption/fixtures/v4_ordered_rows.json`. In each column, the correct order is 1, 2, 3.

Run this from the root of the `stack` checkout:

```bash
mise -C packages/eql run build

docker run -d --rm --name eql-order-repro -e POSTGRES_PASSWORD=x postgres:17
until docker exec eql-order-repro pg_isready -U postgres -q; do sleep 1; done
sleep 2
docker exec -i eql-order-repro psql -U postgres -q -v ON_ERROR_STOP=1 \
  < packages/eql/release/cipherstash-encrypt-v4.sql > /dev/null

python3 - <<'EOF' | docker exec -i eql-order-repro psql -U postgres -q -v ON_ERROR_STOP=1
import json
fixture = json.load(open("packages/eql/tests/encryption/fixtures/v4_ordered_rows.json"))
columns = [("level", "integer_ord"), ("floor", "integer_ord_ope"), ("title", "text_ord"), ("handle", "text_ord_ope")]
print("CREATE TABLE rankings (id int PRIMARY KEY, "
      + ", ".join(f"{c} public.eql_v4_{t}" for c, t in columns) + ");")
for row in fixture["rows"]:
    values = ", ".join(
        "'" + json.dumps(row["stored"][c]).replace("'", "''") + f"'::jsonb::public.eql_v4_{t}"
        for c, t in columns
    )
    print(f"INSERT INTO rankings VALUES ({row['id']}, {values});")
EOF

for column in level floor title handle; do
  bare=$(docker exec eql-order-repro psql -U postgres -At -c "SELECT string_agg(id::text, ',') FROM (SELECT id FROM rankings ORDER BY $column) s")
  term=$(docker exec eql-order-repro psql -U postgres -At -c "SELECT string_agg(id::text, ',') FROM (SELECT id FROM rankings ORDER BY eql_v4.ord_term($column)) s")
  echo "$column: bare ORDER BY gives $bare; ORDER BY eql_v4.ord_term gives $term"
done

docker stop eql-order-repro > /dev/null
```

Output:

```text
level: bare ORDER BY gives 2,3,1; ORDER BY eql_v4.ord_term gives 1,2,3
floor: bare ORDER BY gives 3,1,2; ORDER BY eql_v4.ord_term gives 1,2,3
title: bare ORDER BY gives 2,1,3; ORDER BY eql_v4.ord_term gives 1,2,3
handle: bare ORDER BY gives 2,3,1; ORDER BY eql_v4.ord_term gives 1,2,3
```

### Why nothing catches it

- `packages/eql/docs/reference/database-indexes.md` says that a bare `ORDER BY col` works. That text was written for EQL v3.
- The order tests in `packages/eql/tests/encryption/tests/v4_order.rs` sort only by `eql_v4.ord_term(column)`.

EQL v3 has not been checked.

### Proposal

1. Add a test to `v4_order.rs` that sorts by the bare column, for each v4 ord type.
2. Correct the EQL docs. Say that a bare `ORDER BY col` gives a wrong order.
3. Make a bare `ORDER BY col` either sort by the plaintext or fail with an error.
4. Check EQL v3 with the same test.

## 2. A second install of the EQL bundle silently drops indexes

### What happens

The EQL v4 bundle starts with `DROP SCHEMA IF EXISTS eql_v4 CASCADE`. That drop also removes every index that uses an `eql_v4` function. PostgreSQL gives no error. The encrypted values stay, because their column types are in `public`.

### Effect on Endurain

Migration 29 creates four such indexes on `users`:

- `ix_users_email`, a unique index on `eql_v4.eq_term(email)`;
- `ix_users_username`, a unique index on `eql_v4.eq_term(username)`;
- `ix_users_username_ope`, on `eql_v4.ord_term(username)`;
- `ix_users_username_match`, a GIN index on `eql_v4.match_term(username)`.

After a second install, duplicate emails and usernames are no longer refused. Username searches read every row.

### Evidence

On a throwaway database, I installed the bundle and created a unique `eq_term` index and a GIN `match_term` index. A second install dropped both indexes. The columns and their types stayed.

### Why nothing catches it

`cipherstash/stack` issue #918 (closed) fixed this for EQL v3 in `stash eql upgrade`. The command saves the index definitions and creates them again. A plain `psql` install of the v4 bundle does not do that.

The SDK README says to install the bundle with `psql`. It does not say that a second install drops indexes.

### Proposal

1. Add a warning to the SDK README, in "Before you start".
2. Give Python users a safe upgrade path, such as a v4 version of the #918 procedure.

### Interim procedure

`CIPHERSTASH.md`, section "Upgrade EQL", has a tested procedure. It saves the index definitions. Then it runs the bundle and the saved definitions in one transaction.

## 3. An app cannot build a query term without credentials

### What happens

The SDK builds a query term when the Python code builds the filter, such as `Users.email == x` or `Users.username.match(x)`. That happens before `execute`, and it calls ZeroKMS. So the call needs a configured class and CipherStash credentials.

### Effect on Endurain

A unit test with a mock session cannot test a CRUD function that filters on an encrypted column. Eight tests in `backend/tests/users/users/test_crud_extra.py` had this problem. They now run on PostgreSQL with credentials, and they skip without them.

### Proposal

Add a test double to the SDK, such as a fake `Cipher`. It would return fixed query terms with no ZeroKMS call.

## 4. The monthly summary fails on PostgreSQL with GroupingError

### What happens

`get_monthly_summary` (`backend/app/modules/activities/activity/summary_crud.py:214`) builds a week number expression, `week_expr`. It uses the same expression in `SELECT`, `GROUP BY`, and `ORDER BY`.

SQLAlchemy sends the constants in the expression as bind parameters. Each copy of the expression gets its own parameters, such as `$1` in `SELECT` and `$6` in `GROUP BY`. PostgreSQL compares the expressions before the parameter values are known. So the two copies do not match, and PostgreSQL raises this error:

```text
column "activities.timezone" must appear in the GROUP BY clause
or be used in an aggregate function
```

This bug is not related to encryption. I reproduced it in plain SQL with `PREPARE`. The same query with the constants written inline ran with no error.

### Why nothing caught it

The test `test_sunday_start_splits_monthly_buckets_on_sunday` ran on SQLite. SQLite does not apply this `GROUP BY` rule. The test also replaced `timezone()` with a function that returns the time unchanged.

### Current state

The test now runs on PostgreSQL. It is skipped with a "Solve later" reason.

The app connects with `postgresql+psycopg`, the same driver as the test. So the monthly summary endpoint probably fails the same way. I have not called the endpoint to confirm this.

### Possible fixes

Group and sort by the label, `GROUP BY week_number`, instead of by the expression. Or write the constants inline with `literal()` or `literal_column()`. Neither fix has been tested.

## 5. Alembic autogenerate finds differences that stock Endurain already has

### What happens

An autogenerate revision against a database at head has three groups of steps that are not related to the port:

- drop the `migrations` table;
- change the comments on three `activity_ingestion_jobs` columns;
- change the type of `activity_sets.start_time` to `DateTime(timezone=True)`.

### Evidence that stock Endurain has them

I made a clean checkout of the branch base, commit `e4871d6ec`, with none of the port's changes. I added only the migration 29 file, because the database was already at that revision. Autogenerate on that checkout wrote the same three groups of steps.

### The migrations table

`backend/app/migrations/models.py` defines the model for the table. `model_registry.import_all_models()` loads every `models.py` under `backend/app/modules/`. The `migrations` folder is outside `modules/`, so the registry does not load it. `env.py` loads models only through that function. So the comparison has the table but no model for it, and autogenerate writes a step to drop the table.

### activity_sets.start_time

The upgrade in `v0_18_0_migration.py` changes this column twice:

- line 362 changes it from `DateTime()` to `DateTime(timezone=True)`;
- line 657 changes it back to `DateTime()`.

Both steps are in `upgrade()`. That function runs from line 22 to the start of `downgrade()` at line 704. So after the migration, the column has no time zone. The other `start_time` columns, on `activities` and `activity_laps`, have one. The model says `DateTime(timezone=True)`, so autogenerate writes a step to change it.

### activity_ingestion_jobs

I have not compared the comment texts in the model and in the database.

### Proposal

1. Make `import_all_models()` load `migrations.models`.
2. Add a migration that changes `activity_sets.start_time` to `timestamp with time zone`, with the values read as UTC, as v0_18_0 does for the other columns.
3. Compare the `activity_ingestion_jobs` comments, and make the model and a migration agree.
