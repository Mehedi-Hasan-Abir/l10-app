"""SQLAlchemy 2.0 typed models for every table in PLAN section 4.

The Alembic migration `0001_initial` is the source of truth for DDL (CHECKs, composite FKs, partial unique
indexes, triggers). These models mirror it so queries are typed; `Base.metadata` never creates tables.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("admin", "team_member", "observer")
GOAL_TYPES = ("at_least", "at_most", "between", "exactly")
SECTIONS = ("scorecard", "ids", "conclude")


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','team_member','observer')", name="ck_users_role"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text)


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), primary_key=True)


class Measurable(Base):
    __tablename__ = "measurables"
    __table_args__ = (
        CheckConstraint(
            "goal_type IN ('at_least','at_most','between','exactly')", name="ck_measurables_goal_type"
        ),
        CheckConstraint(
            "(goal_type = 'between' AND goal_min IS NOT NULL AND goal_max IS NOT NULL"
            " AND goal_min <= goal_max AND goal_value IS NULL)"
            " OR (goal_type <> 'between' AND goal_value IS NOT NULL"
            " AND goal_min IS NULL AND goal_max IS NULL)",
            name="ck_measurables_goal_shape",
        ),
        UniqueConstraint("id", "team_id", name="uq_measurables_id_team"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    unit: Mapped[str] = mapped_column(Text)
    goal_type: Mapped[str] = mapped_column(Text)
    goal_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    goal_min: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    goal_max: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer)


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        PrimaryKeyConstraint("measurable_id", "week_start", name="pk_scores"),
        CheckConstraint("EXTRACT(ISODOW FROM week_start) = 1", name="ck_scores_week_is_monday"),
    )

    measurable_id: Mapped[str] = mapped_column(ForeignKey("measurables.id", ondelete="RESTRICT"))
    week_start: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)


class Rock(Base):
    __tablename__ = "rocks"
    __table_args__ = (
        CheckConstraint("quarter ~ '^[0-9]{4}-Q[1-4]$'", name="ck_rocks_quarter"),
        CheckConstraint(
            "seeded_status IN ('on_track','off_track','complete')", name="ck_rocks_seeded_status"
        ),
        UniqueConstraint("id", "team_id", name="uq_rocks_id_team"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    quarter: Mapped[str] = mapped_column(Text)
    seeded_status: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rock_id: Mapped[str] = mapped_column(ForeignKey("rocks.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(Integer)


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) BETWEEN 1 AND 200", name="ck_issues_title"),
        CheckConstraint("priority BETWEEN 1 AND 5", name="ck_issues_priority"),
        CheckConstraint("source_type IN ('measurable','rock','todo')", name="ck_issues_source_type"),
        CheckConstraint(
            "(solved_at IS NULL AND resolution_note IS NULL)"
            " OR (solved_at IS NOT NULL AND resolution_note IS NOT NULL"
            " AND length(btrim(resolution_note)) BETWEEN 1 AND 2000)",
            name="ck_issues_solved_has_note",
        ),
        CheckConstraint(
            "(source_type IS NULL AND source_measurable_id IS NULL AND source_week_start IS NULL"
            " AND source_rock_id IS NULL AND source_todo_id IS NULL)"
            " OR (source_type = 'measurable' AND source_measurable_id IS NOT NULL"
            " AND source_week_start IS NOT NULL AND source_rock_id IS NULL AND source_todo_id IS NULL)"
            " OR (source_type = 'rock' AND source_rock_id IS NOT NULL AND source_measurable_id IS NULL"
            " AND source_week_start IS NULL AND source_todo_id IS NULL)"
            " OR (source_type = 'todo' AND source_todo_id IS NOT NULL AND source_measurable_id IS NULL"
            " AND source_week_start IS NULL AND source_rock_id IS NULL)",
            name="ck_issues_source_shape",
        ),
        CheckConstraint(
            "source_week_start IS NULL OR EXTRACT(ISODOW FROM source_week_start) = 1",
            name="ck_issues_source_week_is_monday",
        ),
        ForeignKeyConstraint(
            ["source_measurable_id", "team_id"],
            ["measurables.id", "measurables.team_id"],
            name="fk_issues_source_measurable_team",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_rock_id", "team_id"],
            ["rocks.id", "rocks.team_id"],
            name="fk_issues_source_rock_team",
            ondelete="RESTRICT",
        ),
        # Circular with todos.source_issue_id: created with ALTER TABLE after both tables exist.
        ForeignKeyConstraint(
            ["source_todo_id", "team_id"],
            ["todos.id", "todos.team_id"],
            name="fk_issues_source_todo_team",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("id", "team_id", name="uq_issues_id_team"),
        # THE duplicate guard: at most one UNSOLVED issue per source.
        Index(
            "uq_issue_open_measurable",
            "source_measurable_id",
            "source_week_start",
            unique=True,
            postgresql_where=text("solved_at IS NULL AND source_type = 'measurable'"),
        ),
        Index(
            "uq_issue_open_rock",
            "source_rock_id",
            unique=True,
            postgresql_where=text("solved_at IS NULL AND source_type = 'rock'"),
        ),
        Index(
            "uq_issue_open_todo",
            "source_todo_id",
            unique=True,
            postgresql_where=text("solved_at IS NULL AND source_type = 'todo'"),
        ),
        Index("ix_issues_team_unsolved", "team_id", postgresql_where=text("solved_at IS NULL")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    solved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_measurable_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_rock_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_todo_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) BETWEEN 1 AND 200", name="ck_todos_title"),
        ForeignKeyConstraint(
            ["source_issue_id", "team_id"],
            ["issues.id", "issues.team_id"],
            name="fk_todos_source_issue_team",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "team_id", name="uq_todos_id_team"),
        Index("ix_todos_team_due", "team_id", "due_date"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    due_date: Mapped[date] = mapped_column(Date)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_issue_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("current_section IN ('scorecard','ids','conclude')", name="ck_meetings_section"),
        CheckConstraint("EXTRACT(ISODOW FROM scorecard_week) = 1", name="ck_meetings_week_is_monday"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_meetings_ended_after_started"),
        CheckConstraint("(ended_at IS NULL) = (summary IS NULL)", name="ck_meetings_summary_iff_ended"),
        Index(
            "uq_meeting_one_active_per_team",
            "team_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    facilitator_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_section: Mapped[str] = mapped_column(Text)
    section_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scorecard_week: Mapped[date] = mapped_column(Date)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="RESTRICT"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MeetingRating(Base):
    __tablename__ = "meeting_ratings"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 10", name="ck_meeting_ratings_rating"),
        ForeignKeyConstraint(
            ["meeting_id", "user_id"],
            ["meeting_attendees.meeting_id", "meeting_attendees.user_id"],
            name="fk_meeting_ratings_attendee",
            ondelete="RESTRICT",
        ),
    )

    meeting_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppClock(Base):
    __tablename__ = "app_clock"

    app_now: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    real_anchor: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only audit trail (migration `0002_audit_events`; a trigger rejects UPDATE and DELETE)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('score','issue','todo','meeting')", name="ck_audit_events_entity_type"
        ),
        Index("ix_audit_events_team_id", "team_id", text("id DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
