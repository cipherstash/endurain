"""Follow events published by an API replica are delivered by separate worker processes.

The replica commits the follow row together with its outbox event; a worker in another
process turns each event into exactly one notification for the right user. Nothing here
is mocked: the processes only share PostgreSQL, Redis and storage.
"""

import subprocess
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session
from stackencrypt.sqlalchemy import configure_encryption

import encrypted_models
import model_registry
import modules.users.users.models as users_models
from tests.distributed.cluster import _APP_DIR, _SUPPORT_DIR

_SUBSCRIBERS = {"followers.notify_requested", "followers.notify_accepted"}


def _follow_and_accept(cluster, *, distributed: bool) -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv built from this repo's own entry points
        [sys.executable, str(_SUPPORT_DIR / "follow_users.py")],
        cwd=_APP_DIR,
        env=cluster.env(distributed=distributed),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"follow driver failed:\n{result.stderr[-2000:]}"


def _notifications_by_username(cluster) -> dict[str, int]:
    """Count each user's notifications, by username.

    The username column is encrypted, so raw SQL would read the stored EQL value.
    The ORM decrypts it, once every model is imported. A repeat
    configure_encryption call does nothing.
    """
    model_registry.import_all_models()
    configure_encryption(encrypted_models.classes())
    counts = dict(cluster.query("SELECT user_id, count(*) FROM notifications GROUP BY 1"))
    with Session(cluster.engine) as session:
        users = session.scalars(select(users_models.Users).where(users_models.Users.id.in_(counts))).all()
        return {user.username: counts[user.id] for user in users}


def _assert_each_user_notified_once(cluster, *, workers: int) -> None:
    assert cluster.wait_for_durable_jobs(len(_SUBSCRIBERS)), cluster.query(
        "SELECT subscriber_id, status FROM processing_jobs"
    )
    assert {row[0] for row in cluster.query("SELECT subscriber_id FROM processing_jobs")} == _SUBSCRIBERS
    notified = _notifications_by_username(cluster)
    assert notified == {"followee-user": 1, "follower-user": 1}, (
        "the target hears of the request, the requester of the accept"
    )
    assert not cluster.query("SELECT job_id FROM job_attempts GROUP BY 1 HAVING count(*) > 1"), "no job ran twice"
    assert cluster.scalar("SELECT count(*) FROM job_workers WHERE stopped_at IS NULL") == workers
    assert cluster.error_lines() == []


def test_a_standalone_worker_notifies_what_the_api_published(cluster):
    """Local profile: Redis events and local storage, the worker a separate process."""
    cluster.start_api(distributed=False)
    cluster.start_worker(distributed=False)

    _follow_and_accept(cluster, distributed=False)

    _assert_each_user_notified_once(cluster, workers=1)


def test_two_workers_notify_each_user_exactly_once(cluster):
    """Distributed profile: workers compete for the jobs and never share a disk."""
    cluster.start_api(distributed=True)
    cluster.start_worker(distributed=True)
    cluster.start_worker(distributed=True)

    _follow_and_accept(cluster, distributed=True)

    _assert_each_user_notified_once(cluster, workers=2)
