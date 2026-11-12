"""User database models."""

from datetime import date as date_type
from typing import TYPE_CHECKING, Annotated

import stackencrypt
from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from stackencrypt import Ignore, Text, TextEq, TextSearch

from core.database import Base

if TYPE_CHECKING:
    from modules.gears.gear.models import Gear
    from modules.gears.gear_components.models import GearComponents
    from modules.health.health_fasting.models import HealthFasting
    from modules.health.health_poop.models import HealthPoop
    from modules.health.health_sleep.models import HealthSleep
    from modules.health.health_steps.models import HealthSteps
    from modules.health.health_targets.models import HealthTargets
    from modules.health.health_water.models import HealthWater
    from modules.health.health_weight.models import HealthWeight
    from modules.notifications.models import Notification
    from modules.users.users_default_gear.models import UsersDefaultGear
    from modules.users.users_goals.models import UsersGoal
    from modules.users.users_integrations.models import UsersIntegrations
    from modules.users.users_privacy_settings.models import UsersPrivacySettings


@stackencrypt.model("users")
class Users(Base):
    """
    User account and profile information.

    ``name``, ``username``, ``email`` and ``city`` are encrypted with
    CipherStash. ``email`` is searched by equality. ``username`` is searched by
    equality and by match, and is sorted. ``name`` and ``city`` are stored
    only. Every other column is plaintext, marked ``Ignore``.

    Attributes:
        id: Primary key.
        name: User's real name (may include spaces).
        username: Unique username (letters, numbers, dots).
        email: Unique email address (max 250 characters).
        city: User's city.
        birthdate: User's birthdate.
        preferred_language: Preferred language code.
        gender: User's gender (male, female, unspecified).
        units: Measurement units (metric, imperial).
        height: User's height in centimeters.
        max_heart_rate: User maximum heart rate (bpm).
        access_type: User type (regular, admin).
        photo_path: Path to user's photo.
        active: Whether the user is active.
        first_day_of_week: First day of the week
            (sunday, monday, etc.).
        currency: Currency preference (euro, dollar, pound).
        email_verified: Whether the user's email address has
            been verified.
        pending_admin_approval: Whether the user is pending
            admin approval for activation.
        users_integrations: List of integrations.
        users_default_gear: List of default gear.
        users_privacy_settings: List of privacy settings.
        gear: List of gear owned by the user.
        gear_components: List of gear components.
        health_sleep: List of health sleep records.
        health_weight: List of health weight records.
        health_steps: List of health steps records.
        health_targets: List of health targets.
        health_fasting: List of health fasting records.
        health_water: List of health water intake records.
        health_poop: List of health poop records.
        notifications: List of notifications.
        goals: List of user goals.
    """

    __tablename__ = "users"
    # An encrypted value has new random ciphertext each time, so a unique
    # constraint on the column never finds a duplicate. These unique indexes
    # are on the equality term, which is the same for equal plaintexts. They
    # also serve the equality searches.
    __table_args__ = (
        Index("ix_users_email", text("eql_v4.eq_term(email)"), unique=True),
        Index("ix_users_username", text("eql_v4.eq_term(username)"), unique=True),
    )

    id: Mapped[Annotated[int, Ignore]] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[Annotated[str, Text]] = mapped_column(
        nullable=False,
        comment="User real name (May include spaces)",
    )
    username: Mapped[Annotated[str, TextSearch]] = mapped_column(
        nullable=False,
        comment="User username (letters, numbers, and dots allowed)",
    )
    email: Mapped[Annotated[str, TextEq]] = mapped_column(
        nullable=False,
        comment="User email (max 250 characters)",
    )
    city: Mapped[Annotated[str | None, Text]] = mapped_column(
        nullable=True,
        comment="User city",
    )
    birthdate: Mapped[Annotated[date_type | None, Ignore]] = mapped_column(
        nullable=True,
        comment="User birthdate (date)",
    )
    preferred_language: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(35),
        nullable=False,
        comment="User preferred BCP 47 language tag (en, pt-PT, zh-Hans, others)",
    )
    gender: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(20),
        default="male",
        nullable=False,
        comment="User gender (male, female, unspecified)",
    )
    units: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(20),
        default="metric",
        nullable=False,
        comment="User units (metric, imperial)",
    )
    height: Mapped[Annotated[int | None, Ignore]] = mapped_column(
        nullable=True,
        comment="User height in centimeters",
    )
    max_heart_rate: Mapped[Annotated[int | None, Ignore]] = mapped_column(
        nullable=True,
        comment="User maximum heart rate (bpm)",
    )
    access_type: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(20),
        nullable=False,
        comment="User type (regular, admin)",
    )
    photo_path: Mapped[Annotated[str | None, Ignore]] = mapped_column(
        String(250),
        nullable=True,
        comment="User photo path",
    )
    active: Mapped[Annotated[bool, Ignore]] = mapped_column(
        default=True,
        nullable=False,
        comment="Whether the user is active (true - yes, false - no)",
    )
    first_day_of_week: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(20),
        default="monday",
        nullable=False,
        comment="User first day of week (sunday, monday, etc.)",
    )
    currency: Mapped[Annotated[str, Ignore]] = mapped_column(
        String(20),
        default="euro",
        nullable=False,
        comment="User currency (euro, dollar, pound)",
    )
    timezone: Mapped[Annotated[str | None, Ignore]] = mapped_column(
        String(250),
        nullable=True,
        comment=("User IANA timezone, used as the fallback for activities with no GPS track"),
    )
    email_verified: Mapped[Annotated[bool, Ignore]] = mapped_column(
        default=False,
        nullable=False,
        comment=("Whether the user's email address has been verified (true - yes, false - no)"),
    )
    pending_admin_approval: Mapped[Annotated[bool, Ignore]] = mapped_column(
        default=False,
        nullable=False,
        comment=("Whether the user is pending admin approval for activation (true - yes, false - no)"),
    )

    # Relationships
    users_integrations: Mapped[list["UsersIntegrations"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    users_default_gear: Mapped[list["UsersDefaultGear"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    users_privacy_settings: Mapped[list["UsersPrivacySettings"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    gear: Mapped[list["Gear"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    gear_components: Mapped[list["GearComponents"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_sleep: Mapped[list["HealthSleep"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_weight: Mapped[list["HealthWeight"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_steps: Mapped[list["HealthSteps"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_targets: Mapped[list["HealthTargets"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_fasting: Mapped[list["HealthFasting"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_water: Mapped[list["HealthWater"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    health_poop: Mapped[list["HealthPoop"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
    goals: Mapped[list["UsersGoal"]] = relationship(
        back_populates="users",
        cascade="all, delete-orphan",
    )
