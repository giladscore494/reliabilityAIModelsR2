"""add research consent and responses

Revision ID: f1c3bcc69907
Revises: d1e2f3a4b5c6
Create Date: 2026-04-03 22:37:23.320503
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f1c3bcc69907"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        return index_name in {idx.get("name") for idx in inspector.get_indexes(table_name)}
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("research_consent"):
        op.create_table(
            "research_consent",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=True),
            sa.Column("anon_id", sa.String(length=64), nullable=True),
            sa.Column("consent_type", sa.String(length=32), nullable=False),
            sa.Column("terms_version", sa.String(length=32), nullable=False),
            sa.Column("privacy_version", sa.String(length=32), nullable=False),
            sa.Column("research_notice_version", sa.String(length=32), nullable=False),
            sa.Column("accepted_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_ip", sa.String(length=64), nullable=False),
            sa.Column("accepted_user_agent", sa.String(length=512), nullable=True),
            sa.Column("accepted_lang", sa.String(length=32), nullable=True),
            sa.Column("accepted_source", sa.String(length=64), nullable=False),
            sa.Column("is_explicit", sa.Boolean(), nullable=False),
            sa.Column("is_informed", sa.Boolean(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "consent_type",
                "terms_version",
                "privacy_version",
                "research_notice_version",
                name="uq_research_consent_user_version",
            ),
            sa.UniqueConstraint(
                "anon_id",
                "consent_type",
                "terms_version",
                "privacy_version",
                "research_notice_version",
                name="uq_research_consent_anon_version",
            ),
        )

    inspector = inspect(bind)
    for index_name, columns in (
        ("ix_research_consent_created_at", ["created_at"]),
        ("ix_research_consent_user_id", ["user_id"]),
        ("ix_research_consent_anon_id", ["anon_id"]),
        ("ix_research_consent_consent_type", ["consent_type"]),
        ("ix_research_consent_accepted_at", ["accepted_at"]),
        (
            "ix_research_consent_lookup_user",
            ["user_id", "consent_type", "research_notice_version", "accepted_at"],
        ),
        (
            "ix_research_consent_lookup_anon",
            ["anon_id", "consent_type", "research_notice_version", "accepted_at"],
        ),
    ):
        if not _has_index(inspector, "research_consent", index_name):
            op.create_index(index_name, "research_consent", columns, unique=False)

    if not inspector.has_table("research_response_session"):
        op.create_table(
            "research_response_session",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=True),
            sa.Column("anon_id", sa.String(length=64), nullable=True),
            sa.Column("flow_type", sa.String(length=32), nullable=False),
            sa.Column("source_analysis_type", sa.String(length=64), nullable=False),
            sa.Column("source_record_id", sa.Integer(), nullable=True),
            sa.Column("vehicle_context_json", sa.Text(), nullable=True),
            sa.Column("consent_id", sa.Integer(), sa.ForeignKey("research_consent.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
        )

    inspector = inspect(bind)
    for index_name, columns in (
        ("ix_research_response_session_created_at", ["created_at"]),
        ("ix_research_response_session_user_id", ["user_id"]),
        ("ix_research_response_session_anon_id", ["anon_id"]),
        ("ix_research_response_session_flow_type", ["flow_type"]),
        ("ix_research_response_session_source_analysis_type", ["source_analysis_type"]),
        ("ix_research_response_session_source_record_id", ["source_record_id"]),
        ("ix_research_response_session_consent_id", ["consent_id"]),
        ("ix_research_response_session_status", ["status"]),
        (
            "ix_research_session_flow_source",
            ["flow_type", "source_analysis_type", "source_record_id"],
        ),
        (
            "ix_research_session_subject_flow_created",
            ["user_id", "anon_id", "flow_type", "created_at"],
        ),
    ):
        if not _has_index(inspector, "research_response_session", index_name):
            op.create_index(index_name, "research_response_session", columns, unique=False)

    if not inspector.has_table("research_response"):
        op.create_table(
            "research_response",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("research_response_session.id", ondelete="CASCADE"), nullable=False),
            sa.Column("question_code", sa.String(length=64), nullable=False),
            sa.Column("flow_type", sa.String(length=32), nullable=False),
            sa.Column("response_json", sa.Text(), nullable=False),
            sa.Column("answered_at", sa.DateTime(), nullable=False),
            sa.Column("is_required", sa.Boolean(), nullable=False),
            sa.Column("question_version", sa.String(length=32), nullable=False),
            sa.Column("consent_id", sa.Integer(), sa.ForeignKey("research_consent.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("session_id", "question_code", name="uq_research_response_session_question"),
        )

    inspector = inspect(bind)
    for index_name, columns in (
        ("ix_research_response_session_id", ["session_id"]),
        ("ix_research_response_question_code", ["question_code"]),
        ("ix_research_response_flow_type", ["flow_type"]),
        ("ix_research_response_answered_at", ["answered_at"]),
        ("ix_research_response_consent_id", ["consent_id"]),
        (
            "ix_research_response_flow_question_answered",
            ["flow_type", "question_code", "answered_at"],
        ),
    ):
        if not _has_index(inspector, "research_response", index_name):
            op.create_index(index_name, "research_response", columns, unique=False)


def downgrade():
    for table_name, indexes in (
        (
            "research_response",
            [
                "ix_research_response_flow_question_answered",
                "ix_research_response_consent_id",
                "ix_research_response_answered_at",
                "ix_research_response_flow_type",
                "ix_research_response_question_code",
                "ix_research_response_session_id",
            ],
        ),
        (
            "research_response_session",
            [
                "ix_research_session_subject_flow_created",
                "ix_research_session_flow_source",
                "ix_research_response_session_status",
                "ix_research_response_session_consent_id",
                "ix_research_response_session_source_record_id",
                "ix_research_response_session_source_analysis_type",
                "ix_research_response_session_flow_type",
                "ix_research_response_session_anon_id",
                "ix_research_response_session_user_id",
                "ix_research_response_session_created_at",
            ],
        ),
        (
            "research_consent",
            [
                "ix_research_consent_lookup_anon",
                "ix_research_consent_lookup_user",
                "ix_research_consent_accepted_at",
                "ix_research_consent_consent_type",
                "ix_research_consent_anon_id",
                "ix_research_consent_user_id",
                "ix_research_consent_created_at",
            ],
        ),
    ):
        bind = op.get_bind()
        inspector = inspect(bind)
        if inspector.has_table(table_name):
            for index_name in indexes:
                if _has_index(inspector, table_name, index_name):
                    op.drop_index(index_name, table_name=table_name)

    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("research_response"):
        op.drop_table("research_response")
    inspector = inspect(bind)
    if inspector.has_table("research_response_session"):
        op.drop_table("research_response_session")
    inspector = inspect(bind)
    if inspector.has_table("research_consent"):
        op.drop_table("research_consent")
