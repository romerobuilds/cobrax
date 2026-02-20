from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ADD_CAMPAIGN_FKS_EMAIL_LOGS"
down_revision = "COLOQUE_AQUI_O_ID_DA_ULTIMA_MIGRATION"
branch_labels = None
depends_on = None


def upgrade():
    # Default para attempt_count
    op.alter_column(
        "email_logs",
        "attempt_count",
        server_default="0",
        existing_type=sa.Integer(),
    )

    # FK campaign_id
    op.create_foreign_key(
        "email_logs_campaign_id_fkey",
        "email_logs",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # FK campaign_run_id
    op.create_foreign_key(
        "email_logs_campaign_run_id_fkey",
        "email_logs",
        "campaign_runs",
        ["campaign_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("email_logs_campaign_id_fkey", "email_logs", type_="foreignkey")
    op.drop_constraint("email_logs_campaign_run_id_fkey", "email_logs", type_="foreignkey")

    op.alter_column(
        "email_logs",
        "attempt_count",
        server_default=None,
        existing_type=sa.Integer(),
    )