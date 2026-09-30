"""Preserve whether an Excel amount cell is numeric for pivot reconciliation."""

from alembic import op
import sqlalchemy as sa

revision = "20260929_excel_amount_cell_type"
down_revision = "20273f0f64ef"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("historical_records", sa.Column(
        "excel_amount_is_numeric", sa.Boolean(), nullable=False,
        server_default=sa.true()))


def downgrade():
    op.drop_column("historical_records", "excel_amount_is_numeric")
