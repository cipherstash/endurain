"""CRUD operations for activity summary aggregations.

Every bucket (day / week / month / year) is derived from the activity's **local**
wall clock rather than the UTC value stored in the column — see
:mod:`modules.activities.activity.query`, which owns that rule for every query
over the activities table.

The metrics are encrypted with CipherStash, and SQL cannot add encrypted
values. So each summary reads one row per activity in the period: its bucket,
its type, and its metrics, decrypted. The buckets, the type breakdown and the
totals are then added up here, from that one query.

Named ``summary_crud`` rather than ``summary_query``: it opens a ``Session`` and
executes, which is the ``crud`` role, and the enforcement rules match on the
role.
"""

from collections.abc import Hashable, Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, extract, func, select
from sqlalchemy.orm import Session

import core.calendar as core_calendar
import modules.activities.activity.constants as activity_constants
import modules.activities.activity.models as activity_models
import modules.activities.activity.query as activity_query
import modules.activities.activity.summary_schema as activity_summary_schema


def _apply_activity_type_filter(
    stmt: Select[Any],
    activity_type: str | None,
) -> tuple[Select[Any], int | None]:
    """Apply activity type name filter to a statement.

    Args:
        stmt: SQLAlchemy select statement.
        activity_type: Optional activity type name.

    Returns:
        Tuple of filtered statement and resolved
        type ID (or None).
    """
    if not activity_type:
        return stmt, None
    type_id = activity_constants.ACTIVITY_NAME_TO_ID.get(activity_type.lower())
    if type_id is not None:
        return (
            stmt.where(activity_models.Activity.activity_type == type_id),
            type_id,
        )
    # Unknown type - force empty result set
    return stmt.where(activity_models.Activity.id == -1), None


def _activity_rows(
    user_id: int,
    db: Session,
    *,
    bucket: ColumnElement[Any] | None,
    start_date: date | None = None,
    end_date: date | None = None,
    activity_type: str | None = None,
) -> Sequence[Row[Any]]:
    """Read one row per activity: its bucket, its type, and its metrics.

    Args:
        user_id: Target user ID.
        db: Database session.
        bucket: The expression that names the activity's bucket, labelled
            ``bucket`` in each row. ``None`` for no bucket.
        start_date: Period start (inclusive), or ``None`` for no bound.
        end_date: Period end (exclusive), or ``None`` for no bound.
        activity_type: Optional type name filter.

    Returns:
        The rows. The metric values are decrypted.
    """
    columns: list[Any] = [
        activity_models.Activity.activity_type,
        activity_models.Activity.distance,
        activity_models.Activity.total_timer_time,
        activity_models.Activity.elevation_gain,
        activity_models.Activity.calories,
    ]
    if bucket is not None:
        columns.insert(0, bucket.label("bucket"))
    stmt = select(*columns).where(activity_models.Activity.user_id == user_id)
    if start_date is not None and end_date is not None:
        stmt = stmt.where(*activity_query.local_date_range_conditions(start_date, end_date, end_exclusive=True))
    stmt, _ = _apply_activity_type_filter(stmt, activity_type)
    return db.execute(stmt).all()


def _add(totals: activity_summary_schema.SummaryMetrics, row: Row[Any]) -> None:
    """Add one activity's metrics to running totals.

    A NULL elevation gain or calorie count adds nothing, as SQL ``SUM`` skips
    NULL.
    """
    totals.total_distance += float(row.distance)
    totals.total_duration += float(row.total_timer_time)
    totals.total_elevation_gain += float(row.elevation_gain or 0)
    totals.total_calories += float(row.calories or 0)
    totals.activity_count += 1


def _totals(rows: Sequence[Row[Any]]) -> activity_summary_schema.SummaryMetrics:
    """Add up every row's metrics."""
    totals = activity_summary_schema.SummaryMetrics()
    for row in rows:
        _add(totals, row)
    return totals


def _totals_by_bucket(rows: Sequence[Row[Any]]) -> dict[Hashable, activity_summary_schema.SummaryMetrics]:
    """Add up the rows' metrics for each bucket, keyed by ``int(row.bucket)``."""
    by_bucket: dict[Hashable, activity_summary_schema.SummaryMetrics] = {}
    for row in rows:
        _add(by_bucket.setdefault(int(row.bucket), activity_summary_schema.SummaryMetrics()), row)
    return by_bucket


def _metrics(totals: activity_summary_schema.SummaryMetrics) -> dict[str, Any]:
    """The totals as keyword arguments for a summary schema."""
    return totals.model_dump()


def _type_breakdown(rows: Sequence[Row[Any]]) -> list[activity_summary_schema.TypeBreakdownItem]:
    """Add up the rows' metrics for each activity type.

    Args:
        rows: Rows from :func:`_activity_rows`.

    Returns:
        One item per type, the most frequent first, then by type ID.
    """
    by_type: dict[int, activity_summary_schema.SummaryMetrics] = {}
    for row in rows:
        _add(by_type.setdefault(int(row.activity_type), activity_summary_schema.SummaryMetrics()), row)
    ordered = sorted(by_type.items(), key=lambda item: (-item[1].activity_count, item[0]))
    return [
        activity_summary_schema.TypeBreakdownItem(
            activity_type_id=type_id,
            activity_type=activity_constants.set_activity_name_based_on_activity_type(type_id),
            **_metrics(totals),
        )
        for type_id, totals in ordered
    ]


def get_weekly_summary(
    user_id: int,
    target_date: date,
    first_day_of_week: core_calendar.WeekdayValue,
    db: Session,
    *,
    activity_type: str | None = None,
) -> activity_summary_schema.WeeklySummaryRead:
    """Get weekly activity summary for a user.

    Args:
        db: Database session.
        user_id: Target user ID.
        target_date: Any date within the target
            week.
        first_day_of_week: User's configured first weekday.
        activity_type: Optional activity type
            filter name.

    Returns:
        Weekly summary with daily breakdowns.
    """
    first_day_index = core_calendar.get_weekday_index(first_day_of_week)
    start_of_week = core_calendar.get_week_start(target_date, first_day_of_week)
    end_of_week = start_of_week + timedelta(days=7)

    # ISO day of week (1-7, Mon-Sun) evaluated on the activity's LOCAL wall clock
    # so a ride lands on the day the athlete rode it.
    iso_dow: ColumnElement[Any] = extract("isodow", activity_query.local_start_time_expression())

    rows = _activity_rows(
        user_id, db, bucket=iso_dow, start_date=start_of_week, end_date=end_of_week, activity_type=activity_type
    )
    by_day = _totals_by_bucket(rows)

    breakdown: list[activity_summary_schema.DaySummary] = []
    for day_offset in range(7):
        day_index = (first_day_index + day_offset) % 7
        day_totals = by_day.get(day_index + 1)
        if day_totals:
            breakdown.append(activity_summary_schema.DaySummary(day_of_week=day_index, **_metrics(day_totals)))
        else:
            breakdown.append(activity_summary_schema.DaySummary(day_of_week=day_index))

    return activity_summary_schema.WeeklySummaryRead(
        **_metrics(_totals(rows)),
        breakdown=breakdown,
        type_breakdown=_type_breakdown(rows),
    )


def get_monthly_summary(
    user_id: int,
    target_date: date,
    first_day_of_week: core_calendar.WeekdayValue,
    db: Session,
    *,
    activity_type: str | None = None,
) -> activity_summary_schema.MonthlySummaryRead:
    """Get monthly activity summary for a user.

    Args:
        db: Database session.
        user_id: Target user ID.
        target_date: Any date within the target
            month.
        first_day_of_week: User's configured first weekday.
        activity_type: Optional activity type
            filter name.

    Returns:
        Monthly summary with weekly breakdowns.
    """
    start_of_month = target_date.replace(day=1)
    end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)

    first_day_index = core_calendar.get_weekday_index(first_day_of_week)
    leading_days = (start_of_month.weekday() - first_day_index) % 7
    week_expr = func.floor((extract("day", activity_query.local_start_time_expression()) + leading_days - 1) / 7) + 1

    rows = _activity_rows(
        user_id, db, bucket=week_expr, start_date=start_of_month, end_date=end_of_month, activity_type=activity_type
    )
    by_week = _totals_by_bucket(rows)

    breakdown = [
        activity_summary_schema.WeekSummary(week_number=week_number, **_metrics(week_totals))
        for week_number, week_totals in sorted(by_week.items())
    ]

    return activity_summary_schema.MonthlySummaryRead(
        **_metrics(_totals(rows)),
        breakdown=breakdown,
        type_breakdown=_type_breakdown(rows),
    )


def get_yearly_summary(
    user_id: int,
    year: int,
    db: Session,
    *,
    activity_type: str | None = None,
) -> activity_summary_schema.YearlySummaryRead:
    """Get yearly activity summary for a user.

    Args:
        db: Database session.
        user_id: Target user ID.
        year: Target calendar year.
        activity_type: Optional activity type
            filter name.

    Returns:
        Yearly summary with monthly breakdowns.
    """
    start_of_year = date(year, 1, 1)
    end_of_year = date(year + 1, 1, 1)

    month_expr = extract("month", activity_query.local_start_time_expression())

    rows = _activity_rows(
        user_id, db, bucket=month_expr, start_date=start_of_year, end_date=end_of_year, activity_type=activity_type
    )
    by_month = _totals_by_bucket(rows)

    breakdown: list[activity_summary_schema.MonthSummary] = []
    for month_number in range(1, 13):
        month_totals = by_month.get(month_number)
        if month_totals:
            breakdown.append(activity_summary_schema.MonthSummary(month_number=month_number, **_metrics(month_totals)))
        else:
            breakdown.append(activity_summary_schema.MonthSummary(month_number=month_number))

    return activity_summary_schema.YearlySummaryRead(
        **_metrics(_totals(rows)),
        breakdown=breakdown,
        type_breakdown=_type_breakdown(rows),
    )


def get_lifetime_summary(
    user_id: int,
    db: Session,
    *,
    activity_type: str | None = None,
) -> activity_summary_schema.LifetimeSummaryRead:
    """Get lifetime activity summary for a user.

    Reads and decrypts every activity of the user, or every activity of the
    type, once per call.

    Args:
        db: Database session.
        user_id: Target user ID.
        activity_type: Optional activity type
            filter name.

    Returns:
        Lifetime summary with yearly breakdowns.
    """
    year_expr = extract("year", activity_query.local_start_time_expression())

    rows = _activity_rows(user_id, db, bucket=year_expr, activity_type=activity_type)
    by_year = _totals_by_bucket(rows)

    breakdown = [
        activity_summary_schema.YearlyPeriodSummary(year_number=year_number, **_metrics(year_totals))
        for year_number, year_totals in sorted(by_year.items(), reverse=True)
    ]

    return activity_summary_schema.LifetimeSummaryRead(
        **_metrics(_totals(rows)),
        breakdown=breakdown,
        type_breakdown=_type_breakdown(rows),
    )
