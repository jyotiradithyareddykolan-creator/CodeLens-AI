"""Remember files excluded by content screening.

Adds indexed_files.skip_reason so unchanged excluded files (secrets, binary,
generated) are not downloaded and screened again on every run.

Revision ID: 004_indexed_file_skip_reason
Revises: 003_indexing_persistence
Create Date: 2026-11-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_indexed_file_skip_reason"
down_revision: Union[str, None] = "003_indexing_persistence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("indexed_files") as batch:
        batch.add_column(sa.Column("skip_reason", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("indexed_files") as batch:
        batch.drop_column("skip_reason")
