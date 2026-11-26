"""Initial schema (PLAN section 4).

Every table, CHECK, composite FK, partial unique index and the `assert_not_observer()` trigger function.
Hand-written SQL on purpose: the constraints are the point, and autogenerate would not produce them.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dropped in this order on downgrade (children before parents).
TABLES_CHILD_FIRST = (
    "app_clock",
    "meeting_ratings",
    "meeting_attendees",
    "meetings",
    "todos",
    "issues",
    "milestones",
    "rocks",
    "scores",
    "measurables",
    "memberships",
    "users",
    "teams",
    "companies",
)

# (table, column holding the user that must not be an observer)
OBSERVER_GUARDS = (
    ("measurables", "owner_id"),
    ("rocks", "owner_id"),
    ("issues", "owner_id"),
    ("todos", "owner_id"),
    ("meetings", "facilitator_id"),
    ("meeting_ratings", "user_id"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE companies (
            id text PRIMARY KEY,
            name text NOT NULL,
            timezone text NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teams (
            id text PRIMARY KEY,
            company_id text NOT NULL REFERENCES companies (id) ON DELETE RESTRICT,
            name text NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE users (
            id text PRIMARY KEY,
            company_id text NOT NULL REFERENCES companies (id) ON DELETE RESTRICT,
            name text NOT NULL,
            email text NOT NULL UNIQUE,
            role text NOT NULL,
            CONSTRAINT ck_users_role CHECK (role IN ('admin', 'team_member', 'observer'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE memberships (
            user_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            PRIMARY KEY (user_id, team_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_memberships_team ON memberships (team_id)")

    op.execute(
        """
        CREATE TABLE measurables (
            id text PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            name text NOT NULL,
            owner_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            unit text NOT NULL,
            goal_type text NOT NULL,
            goal_value numeric,
            goal_min numeric,
            goal_max numeric,
            sort_order integer NOT NULL,
            CONSTRAINT ck_measurables_goal_type CHECK (
                goal_type IN ('at_least', 'at_most', 'between', 'exactly')
            ),
            CONSTRAINT ck_measurables_goal_shape CHECK (
                (goal_type = 'between' AND goal_min IS NOT NULL AND goal_max IS NOT NULL
                    AND goal_min <= goal_max AND goal_value IS NULL)
                OR (goal_type <> 'between' AND goal_value IS NOT NULL
                    AND goal_min IS NULL AND goal_max IS NULL)
            ),
            CONSTRAINT uq_measurables_id_team UNIQUE (id, team_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_measurables_team ON measurables (team_id, sort_order)")

    op.execute(
        """
        CREATE TABLE scores (
            measurable_id text NOT NULL REFERENCES measurables (id) ON DELETE RESTRICT,
            week_start date NOT NULL,
            value numeric NOT NULL,
            version integer NOT NULL DEFAULT 1,
            updated_at timestamptz NOT NULL,
            updated_by text REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT pk_scores PRIMARY KEY (measurable_id, week_start),
            CONSTRAINT ck_scores_week_is_monday CHECK (EXTRACT(ISODOW FROM week_start) = 1)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE rocks (
            id text PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            title text NOT NULL,
            owner_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            quarter text NOT NULL,
            seeded_status text NOT NULL,
            sort_order integer NOT NULL,
            CONSTRAINT ck_rocks_quarter CHECK (quarter ~ '^[0-9]{4}-Q[1-4]$'),
            CONSTRAINT ck_rocks_seeded_status CHECK (seeded_status IN ('on_track', 'off_track', 'complete')),
            CONSTRAINT uq_rocks_id_team UNIQUE (id, team_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE milestones (
            id text PRIMARY KEY,
            rock_id text NOT NULL REFERENCES rocks (id) ON DELETE RESTRICT,
            title text NOT NULL,
            due_date date NOT NULL,
            done boolean NOT NULL DEFAULT false,
            sort_order integer NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_milestones_rock ON milestones (rock_id, sort_order)")

    # issues.source_todo_id -> todos is added below, after todos exists (circular reference).
    op.execute(
        """
        CREATE TABLE issues (
            id text PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            title text NOT NULL,
            owner_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            priority smallint,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL,
            solved_at timestamptz,
            resolution_note text,
            source_type text,
            source_measurable_id text,
            source_week_start date,
            source_rock_id text,
            source_todo_id text,
            CONSTRAINT ck_issues_title CHECK (length(btrim(title)) BETWEEN 1 AND 200),
            CONSTRAINT ck_issues_priority CHECK (priority BETWEEN 1 AND 5),
            CONSTRAINT ck_issues_source_type CHECK (source_type IN ('measurable', 'rock', 'todo')),
            CONSTRAINT ck_issues_solved_has_note CHECK (
                (solved_at IS NULL AND resolution_note IS NULL)
                OR (solved_at IS NOT NULL AND resolution_note IS NOT NULL
                    AND length(btrim(resolution_note)) BETWEEN 1 AND 2000)
            ),
            CONSTRAINT ck_issues_source_shape CHECK (
                (source_type IS NULL AND source_measurable_id IS NULL AND source_week_start IS NULL
                    AND source_rock_id IS NULL AND source_todo_id IS NULL)
                OR (source_type = 'measurable' AND source_measurable_id IS NOT NULL
                    AND source_week_start IS NOT NULL AND source_rock_id IS NULL AND source_todo_id IS NULL)
                OR (source_type = 'rock' AND source_rock_id IS NOT NULL AND source_measurable_id IS NULL
                    AND source_week_start IS NULL AND source_todo_id IS NULL)
                OR (source_type = 'todo' AND source_todo_id IS NOT NULL AND source_measurable_id IS NULL
                    AND source_week_start IS NULL AND source_rock_id IS NULL)
            ),
            CONSTRAINT ck_issues_source_week_is_monday CHECK (
                source_week_start IS NULL OR EXTRACT(ISODOW FROM source_week_start) = 1
            ),
            CONSTRAINT fk_issues_source_measurable_team FOREIGN KEY (source_measurable_id, team_id)
                REFERENCES measurables (id, team_id) ON DELETE RESTRICT,
            CONSTRAINT fk_issues_source_rock_team FOREIGN KEY (source_rock_id, team_id)
                REFERENCES rocks (id, team_id) ON DELETE RESTRICT,
            CONSTRAINT uq_issues_id_team UNIQUE (id, team_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_issues_team_unsolved ON issues (team_id) WHERE solved_at IS NULL")
    # THE duplicate guard (brief 2.6 "No duplicate Issues"): at most one UNSOLVED issue per source.
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_open_measurable ON issues (source_measurable_id, source_week_start) "
        "WHERE solved_at IS NULL AND source_type = 'measurable'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_open_rock ON issues (source_rock_id) "
        "WHERE solved_at IS NULL AND source_type = 'rock'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_open_todo ON issues (source_todo_id) "
        "WHERE solved_at IS NULL AND source_type = 'todo'"
    )

    op.execute(
        """
        CREATE TABLE todos (
            id text PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            title text NOT NULL,
            owner_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            due_date date NOT NULL,
            done_at timestamptz,
            source_issue_id text,
            created_at timestamptz NOT NULL,
            CONSTRAINT ck_todos_title CHECK (length(btrim(title)) BETWEEN 1 AND 200),
            CONSTRAINT fk_todos_source_issue_team FOREIGN KEY (source_issue_id, team_id)
                REFERENCES issues (id, team_id) ON DELETE RESTRICT,
            CONSTRAINT uq_todos_id_team UNIQUE (id, team_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_todos_team_due ON todos (team_id, due_date)")
    op.execute(
        """
        ALTER TABLE issues
            ADD CONSTRAINT fk_issues_source_todo_team FOREIGN KEY (source_todo_id, team_id)
            REFERENCES todos (id, team_id) ON DELETE RESTRICT
        """
    )

    op.execute(
        """
        CREATE TABLE meetings (
            id text PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            facilitator_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            started_at timestamptz NOT NULL,
            ended_at timestamptz,
            current_section text NOT NULL,
            section_started_at timestamptz NOT NULL,
            scorecard_week date NOT NULL,
            summary jsonb,
            CONSTRAINT ck_meetings_section CHECK (current_section IN ('scorecard', 'ids', 'conclude')),
            CONSTRAINT ck_meetings_week_is_monday CHECK (EXTRACT(ISODOW FROM scorecard_week) = 1),
            CONSTRAINT ck_meetings_ended_after_started CHECK (ended_at IS NULL OR ended_at >= started_at),
            CONSTRAINT ck_meetings_summary_iff_ended CHECK ((ended_at IS NULL) = (summary IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_meeting_one_active_per_team ON meetings (team_id) WHERE ended_at IS NULL"
    )
    op.execute("CREATE INDEX ix_meetings_team_started ON meetings (team_id, started_at DESC)")
    op.execute(
        """
        CREATE TABLE meeting_attendees (
            meeting_id text NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
            user_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            joined_at timestamptz NOT NULL,
            PRIMARY KEY (meeting_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE meeting_ratings (
            meeting_id text NOT NULL,
            user_id text NOT NULL,
            rating smallint NOT NULL,
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (meeting_id, user_id),
            CONSTRAINT ck_meeting_ratings_rating CHECK (rating BETWEEN 1 AND 10),
            CONSTRAINT fk_meeting_ratings_attendee FOREIGN KEY (meeting_id, user_id)
                REFERENCES meeting_attendees (meeting_id, user_id) ON DELETE RESTRICT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE app_clock (
            app_now timestamptz PRIMARY KEY,
            real_anchor timestamptz NOT NULL
        )
        """
    )

    # DB-level "Observers cannot own, facilitate or rate". The checked column is the trigger argument.
    op.execute(
        """
        CREATE FUNCTION assert_not_observer() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        DECLARE
            col_name text := TG_ARGV[0];
            uid text;
            user_role text;
        BEGIN
            uid := to_jsonb(NEW) ->> col_name;
            IF uid IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT role INTO user_role FROM users WHERE id = uid;
            IF user_role = 'observer' THEN
                RAISE EXCEPTION 'observer % cannot be set as %.%', uid, TG_TABLE_NAME, col_name
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    for table, column in OBSERVER_GUARDS:
        op.execute(
            f"CREATE TRIGGER trg_{table}_{column}_not_observer "
            f"BEFORE INSERT OR UPDATE OF {column} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION assert_not_observer('{column}')"
        )


def downgrade() -> None:
    for table, column in OBSERVER_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_{column}_not_observer ON {table}")
    op.execute("DROP FUNCTION IF EXISTS assert_not_observer()")
    op.execute("ALTER TABLE issues DROP CONSTRAINT IF EXISTS fk_issues_source_todo_team")
    for table in TABLES_CHILD_FIRST:
        op.execute(f"DROP TABLE IF EXISTS {table}")
