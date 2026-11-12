"""Reusable SQL building blocks for querying activities.

The activities table is queried from more than one persistence layer — the
activities CRUD itself, the summary aggregations, and gear-component usage
totals. The rules those queries must agree on (above all: *which local day did
this activity happen on?*) live here as an explicit public surface, instead of
each caller reaching into ``activity/crud.py`` for a helper that reads as
private.

Only SQL expressions and conditions belong here — no sessions opened, no rows
fetched, no ORM instances returned.
"""

from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import unquote

from sqlalchemy import func, or_
from stackencrypt import StashError

import core.timezone as core_timezone
import modules.activities.activity.models as activity_models

#: Re-exported for the callers that reason about the widened pre-filter below.
MAX_UTC_OFFSET = core_timezone.MAX_UTC_OFFSET


def name_search_condition(name_search: str):
    """A case-insensitive search across an activity's name and location.

    The four columns are encrypted, so this is a CipherStash match search on
    each, not ``LIKE``. A match finds the activities whose field holds every
    3-character piece of the term, in any order, ignoring case. A word of 2
    characters, such as ``"uk"``, must match a whole word. It goes in the
    ``WHERE`` of a ``select`` of whole ``Activity`` objects run through a
    session. The SDK then removes the rows that only seemed to match.

    Args:
        name_search: URL-encoded search term, as it arrives on the query string.

    Returns:
        A SQLAlchemy condition matching the term against name, town, city or
        country. ``None`` when the term has nothing a match search can use,
        such as ``"r"``. The caller then applies no name filter. The old LIKE
        search for one letter matched almost every row.
    """
    term = unquote(name_search).replace("+", " ")
    try:
        return or_(
            activity_models.Activity.name.match(term),
            activity_models.Activity.town.match(term),
            activity_models.Activity.city.match(term),
            activity_models.Activity.country.match(term),
        )
    except StashError as error:
        if error.code == "stack_encrypt_empty_term_text":
            return None
        raise


def local_start_time_expression():
    """``Activity.start_time`` as a naive wall clock in the activity's own timezone.

    "Which day did this activity happen on?" is a *local* question: a 07:00 ride
    in UTC+9 belongs to that local day, not to the previous UTC one. ``start_time``
    is stored as ``timestamptz`` and the session runs in UTC, so comparing or
    truncating it directly (``func.date(...)``, ``extract(...)``) silently answers
    in UTC — which put early-morning activities in eastern timezones and late-night
    ones in western timezones into the wrong day, week, month and year.

    Converting through the activity's stored IANA ``timezone`` first makes date
    filters and summary buckets match what the athlete actually experienced.
    Activities with no stored timezone (indoor imports that carried no GPS) fall
    back to UTC.

    Returns:
        A SQL expression yielding each activity's local wall clock.
    """
    # ``timezone(zone, timestamptz) -> timestamp`` is Postgres' AT TIME ZONE.
    return func.timezone(
        func.coalesce(activity_models.Activity.timezone, "UTC"),
        activity_models.Activity.start_time,
    )


def local_date_range_conditions(
    start_date: date | None,
    end_date: date | None,
    *,
    end_exclusive: bool,
) -> list:
    """Restrict rows to a date range evaluated in each activity's *local* timezone.

    Pairs the exact local-time predicate with an indexable pre-filter on the raw
    ``start_time`` column, widened by the maximum real UTC offset so it can never
    exclude a row the exact predicate would keep. Without the pre-filter the
    functional timezone expression would stop the query using the ``start_time``
    index at all.

    Args:
        start_date: Inclusive local start of the range, or ``None`` for open-ended.
        end_date: End of the local range, or ``None`` for open-ended.
        end_exclusive: Whether ``end_date`` is excluded from the range.

    Returns:
        The conditions to apply to the statement. Empty when both bounds are
        ``None``.
    """
    local = local_start_time_expression()
    conditions: list = []

    if start_date is not None:
        start_dt = datetime.combine(start_date, time.min)
        conditions.append(activity_models.Activity.start_time >= start_dt.replace(tzinfo=UTC) - MAX_UTC_OFFSET)
        conditions.append(local >= start_dt)

    if end_date is not None:
        end_dt = datetime.combine(end_date if end_exclusive else end_date + timedelta(days=1), time.min)
        conditions.append(activity_models.Activity.start_time < end_dt.replace(tzinfo=UTC) + MAX_UTC_OFFSET)
        conditions.append(local < end_dt)

    return conditions
