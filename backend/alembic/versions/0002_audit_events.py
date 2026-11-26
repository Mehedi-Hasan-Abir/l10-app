"""Audit trail (HARDENING H3): append-only `audit_events`.

One row per business mutation, written by the service in the same transaction as the change. The table is
append-only at the database level: a trigger rejects every UPDATE and DELETE. TRUNCATE is deliberately not
blocked, so `python -m app.seed --reset` (and the test fixtures) keep working.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_events (
            id bigserial PRIMARY KEY,
            team_id text NOT NULL REFERENCES teams (id) ON DELETE RESTRICT,
            actor_id text NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
            action text NOT NULL,
            entity_type text NOT NULL,
            entity_id text NOT NULL,
            at timestamptz NOT NULL,
            data jsonb NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT ck_audit_events_entity_type CHECK (
                entity_type IN ('score', 'issue', 'todo', 'meeting')
            )
        )
        """
    )
    # Newest first per team: the only access path (`GET /teams/{id}/audit`, keyset paginated on id).
    op.execute("CREATE INDEX ix_audit_events_team_id ON audit_events (team_id, id DESC)")

    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_events_append_only "
        "BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.execute("DROP TABLE IF EXISTS audit_events")
