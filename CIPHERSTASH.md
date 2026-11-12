# Run Endurain with CipherStash encryption

Endurain encrypts four columns of the `users` table: `name`, `username`, `email`, and `city`.
The encryption uses the CipherStash Python SDK, `stackencrypt`.

The database needs EQL v4 before Endurain starts.
EQL is the SQL library that defines the encrypted column types.
Endurain does not install it. You install it by hand, once for each database.

## What you need

- A checkout of `cipherstash/stack` next to this repository, at `../stack`.
  `backend/pyproject.toml` builds `stackencrypt` from that checkout.
- `mise` and `psql`, or Docker.
- CipherStash credentials in `.env` at the root of this repository:
  `CS_WORKSPACE_CRN`, `CS_CLIENT_ID`, `CS_CLIENT_KEY`, and `CS_CLIENT_ACCESS_KEY`.
  Endurain loads `.env` when it starts.

## 1. Build the EQL v4 bundle

Run this from the root of the `stack` checkout:

```sh
mise -C packages/eql run build
```

The bundle is `packages/eql/release/cipherstash-encrypt-v4.sql`.

## 2. Install the bundle

Install it before the first Endurain start.
Migration 29 (`29_encrypt_user_identity_columns.py`) stops with an error if EQL v4 is not installed.

With `psql`, from the root of the `stack` checkout:

```sh
psql "postgresql://endurain:PASSWORD@localhost:5432/endurain" \
  -v ON_ERROR_STOP=1 -f packages/eql/release/cipherstash-encrypt-v4.sql
```

With Docker, when PostgreSQL runs in a container named `postgres`:

```sh
docker exec -i postgres psql -U endurain -d endurain -v ON_ERROR_STOP=1 \
  < packages/eql/release/cipherstash-encrypt-v4.sql
```

Use a role that can create operator classes, such as the database owner.
If the role cannot, the `ore_block` indexes still build, but PostgreSQL never uses them.
PostgreSQL gives no error in that case.

## 3. Install it in the test database too

The tests marked `postgres` need EQL v4 in the database that `TEST_DATABASE_URL` names.
Run step 2 again with that database.
The tests drop and recreate the `public` tables, but the EQL schemas stay.

The tests marked `distributed` also need Redis and an S3-compatible store.
Each of them creates a new database and installs EQL v4 in it.
Set `TEST_EQL_BUNDLE` to the path of the bundle from step 1.
`backend/tests/distributed/conftest.py` lists the other variables and the containers to start.

## 4. Start Endurain

Start Endurain as usual. Migration 29 encrypts the existing users rows.

## Upgrade EQL

Do not just run step 2 again on a database that Endurain already uses.

The bundle starts with `DROP SCHEMA eql_v4 CASCADE`.
That drop also removes every index that uses an `eql_v4` function.
In Endurain, these are the four indexes on `users` that migration 29 creates:

- `ix_users_email`, to refuse a duplicate email;
- `ix_users_username`, to refuse a duplicate username;
- `ix_users_username_ope`, for sorting by username;
- `ix_users_username_match`, for username search.

PostgreSQL gives no error. The encrypted values stay, because their column types are in `public`.
But duplicate emails and usernames are no longer refused, and searches read every row.

To upgrade, save the index definitions first. Then run the bundle and the saved definitions in one transaction.
Run this from the root of the `stack` checkout, after you build the new bundle:

```sh
DB="postgresql://endurain:PASSWORD@localhost:5432/endurain"

# 1. Save each users index that uses an eql_v4 function.
psql "$DB" -At -c "SELECT pg_get_indexdef(indexrelid) || ';' FROM pg_index
  WHERE indrelid = 'public.users'::regclass
  AND pg_get_indexdef(indexrelid) LIKE '%eql_v4.%'" > users-eql-indexes.sql

# 2. Check that the file has the four indexes.
cat users-eql-indexes.sql

# 3. Reinstall EQL and create the indexes again, in one transaction.
psql "$DB" -v ON_ERROR_STOP=1 --single-transaction \
  -f packages/eql/release/cipherstash-encrypt-v4.sql -f users-eql-indexes.sql
```

If any statement fails, the transaction rolls back, and the old EQL and the indexes stay.
