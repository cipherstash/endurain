import os
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from stackencrypt.sqlalchemy import configure_encryption

import model_registry as orm_model_registry


def setup_mock_execute(
    mock_db: MagicMock,
    return_all: Sequence[Any] | None = None,
    return_scalar: Any | None = None,
    return_one_or_none: Any | None = None,
    return_scalars_all: Sequence[Any] | None = None,
):
    execute_mock = MagicMock()
    scalars_mock = MagicMock()
    execute_mock.scalars.return_value = scalars_mock
    if return_scalars_all is not None:
        scalars_mock.all.return_value = return_scalars_all
    elif return_all is not None:
        scalars_mock.all.return_value = return_all
    if return_scalar is not None:
        scalars_mock.scalar.return_value = return_scalar
    scalars_mock.first.return_value = return_one_or_none
    if return_one_or_none is not None:
        scalars_mock.one_or_none.return_value = return_one_or_none
        execute_mock.scalar_one_or_none.return_value = return_one_or_none
    else:
        execute_mock.scalar_one_or_none.return_value = None
    execute_mock.scalar.return_value = return_scalar
    mock_db.execute.return_value = execute_mock
    # Support db.scalars(stmt) pattern (used by some sub-module CRUDs)
    scalars_direct_mock = MagicMock()
    scalars_direct_mock.first.return_value = return_one_or_none
    if return_scalars_all is not None:
        scalars_direct_mock.all.return_value = return_scalars_all
    elif return_all is not None:
        scalars_direct_mock.all.return_value = return_all
    mock_db.scalars.return_value = scalars_direct_mock
    return execute_mock


def setup_mock_query(
    mock_db: MagicMock,
    model_class: type,
    return_all: Sequence[Any] | None = None,
    return_one: Any | None = None,
    return_first: Any | None = None,
    return_scalar: Any | None = None,
    return_count: int = 0,
):
    mock_query = mock_db.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.filter_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = return_all if return_all is not None else []
    mock_query.first.return_value = return_first
    mock_query.one.return_value = return_one
    mock_query.one_or_none.return_value = return_one
    mock_query.scalar.return_value = return_scalar if return_scalar is not None else return_count
    mock_query.count.return_value = return_count
    return mock_query


def _import_all_models() -> None:
    """Import model contributions through the application composition root."""
    orm_model_registry.import_all_models()


#: Environment variable naming a *disposable* PostgreSQL database for the tests
#: marked ``postgres``. Unset, those tests are skipped.
POSTGRES_TEST_URL_ENV = "TEST_DATABASE_URL"


def postgres_test_url() -> str | None:
    """Return the configured PostgreSQL test database URL, or ``None``."""
    return os.environ.get(POSTGRES_TEST_URL_ENV) or None


def create_postgres_session_factory() -> sessionmaker[Session]:
    """Create a sessionmaker over a real PostgreSQL test database.

    Sessions here get their own pooled connection, so
    one session's uncommitted work is invisible to another and a failed flush
    leaves the session unusable until it is rolled back, exactly as in
    production. The schema is dropped and recreated, so the database named by
    ``TEST_DATABASE_URL`` must be disposable: anything whose name does not
    contain ``test`` is refused. Dispose in teardown with
    :func:`drop_postgres_schema`.

    Raises:
        RuntimeError: If the database name does not contain ``test``.
    """
    from core.database import Base

    url = postgres_test_url()
    if url is None:
        raise RuntimeError(f"{POSTGRES_TEST_URL_ENV} is not set")
    database = make_url(url).database or ""
    if "test" not in database:
        raise RuntimeError(
            f"Refusing to recreate the schema of '{database}': {POSTGRES_TEST_URL_ENV} must name a "
            "disposable database whose name contains 'test'."
        )

    import modules.users.users.models as users_models

    _import_all_models()
    # The users' identity columns are CipherStash EQL columns, as in the app:
    # the database must have EQL v4 installed, and the tests need CipherStash
    # credentials. A repeat call does nothing.
    configure_encryption([users_models.Users])
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def drop_postgres_schema(factory: sessionmaker[Session]) -> None:
    """Drop everything the factory created and release its connections."""
    from core.database import Base

    engine = factory.kw["bind"]
    Base.metadata.drop_all(engine)
    engine.dispose()


def ensure_user(factory: sessionmaker[Session], user_id: int) -> None:
    """Insert a minimal user row if it is missing.

    PostgreSQL enforces the ``users.id`` foreign keys that SQLite leaves
    unchecked, so a row that references a user needs the user to exist.
    """
    import modules.users.users.models as users_models

    with factory() as db:
        if db.get(users_models.Users, user_id) is None:
            db.add(
                users_models.Users(
                    id=user_id,
                    name=f"User {user_id}",
                    username=f"user{user_id}",
                    email=f"user{user_id}@example.com",
                    preferred_language="us",
                    access_type="regular",
                )
            )
            db.commit()
