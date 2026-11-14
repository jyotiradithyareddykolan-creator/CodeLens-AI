"""Initial schema with 17 core entities and pgvector support

Revision ID: 001_initial
Revises: 
Create Date: 2026-11-13 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False

# Matches app.models.base.GUID: native UUID on PostgreSQL, CHAR(36) elsewhere.
# Defined locally so the migration does not depend on application code.
UUID_TYPE = sa.CHAR(36).with_variant(postgresql.UUID(as_uuid=True), "postgresql")

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure vector extension exists in PostgreSQL
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. users
    op.create_table(
        'users',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('github_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('avatar_url', sa.Text(), nullable=True),
        sa.Column('encrypted_access_token', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('is_superuser', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_users_github_id', 'users', ['github_id'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    # 2. github_installations
    op.create_table(
        'github_installations',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('user_id', UUID_TYPE, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('installation_id', sa.BigInteger(), nullable=False),
        sa.Column('account_login', sa.String(255), nullable=False),
        sa.Column('account_type', sa.String(50), default='User', nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_github_installations_user_id', 'github_installations', ['user_id'])
    op.create_index('ix_github_installations_inst_id', 'github_installations', ['installation_id'], unique=True)

    # 3. webhook_events
    op.create_table(
        'webhook_events',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('delivery_id', sa.String(255), nullable=False),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(50), default='received', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_webhook_events_delivery_id', 'webhook_events', ['delivery_id'], unique=True)
    op.create_index('ix_webhook_events_event_type', 'webhook_events', ['event_type'])

    # 4. repositories
    op.create_table(
        'repositories',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('installation_id', UUID_TYPE, sa.ForeignKey('github_installations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('github_repo_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('owner', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(512), nullable=False),
        sa.Column('default_branch', sa.String(255), default='main', nullable=False),
        sa.Column('is_private', sa.Boolean(), default=False, nullable=False),
        sa.Column('html_url', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_indexed', sa.Boolean(), default=False, nullable=False),
        sa.Column('indexing_status', sa.String(50), default='unindexed', nullable=False),
        sa.Column('last_indexed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_indexed_commit', sa.String(100), nullable=True),
        sa.Column('total_files', sa.Integer(), default=0, nullable=False),
        sa.Column('total_chunks', sa.Integer(), default=0, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_repositories_github_repo_id', 'repositories', ['github_repo_id'], unique=True)
    op.create_index('ix_repositories_full_name', 'repositories', ['full_name'], unique=True)

    # 5. repository_branches
    op.create_table(
        'repository_branches',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('latest_commit_sha', sa.String(100), nullable=True),
        sa.Column('is_default', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_repository_branches_repo_id', 'repository_branches', ['repository_id'])

    # 6. commits
    op.create_table(
        'commits',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sha', sa.String(100), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('author_name', sa.String(255), nullable=True),
        sa.Column('author_email', sa.String(255), nullable=True),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_commits_repo_sha', 'commits', ['repository_id', 'sha'])

    # 7. indexed_files
    op.create_table(
        'indexed_files',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_path', sa.String(1024), nullable=False),
        sa.Column('language', sa.String(50), nullable=False),
        sa.Column('size_bytes', sa.Integer(), default=0, nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_indexed_files_repo_path', 'indexed_files', ['repository_id', 'file_path'], unique=True)

    # 8. code_chunks
    embedding_col = Vector(768) if (conn.dialect.name == "postgresql" and HAS_PGVECTOR) else sa.JSON()
    op.create_table(
        'code_chunks',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('file_id', UUID_TYPE, sa.ForeignKey('indexed_files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol_name', sa.String(255), nullable=True),
        sa.Column('symbol_type', sa.String(50), default='chunk', nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('commit_sha', sa.String(100), nullable=False),
        sa.Column('embedding', embedding_col, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_code_chunks_repo_symbol', 'code_chunks', ['repository_id', 'symbol_name'])

    # 9. code_symbols
    op.create_table(
        'code_symbols',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('file_id', UUID_TYPE, sa.ForeignKey('indexed_files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('symbol_type', sa.String(50), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('docstring', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_code_symbols_file_name', 'code_symbols', ['file_id', 'name'])

    # 10. dependencies
    op.create_table(
        'dependencies',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_symbol_id', UUID_TYPE, sa.ForeignKey('code_symbols.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_symbol_id', UUID_TYPE, sa.ForeignKey('code_symbols.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_file_id', UUID_TYPE, sa.ForeignKey('indexed_files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target_file_id', UUID_TYPE, sa.ForeignKey('indexed_files.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_name', sa.String(255), nullable=False),
        sa.Column('target_name', sa.String(255), nullable=False),
        sa.Column('relationship_type', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_dependencies_repo', 'dependencies', ['repository_id'])

    # 11. pull_requests
    op.create_table(
        'pull_requests',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pr_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('author_username', sa.String(255), nullable=False),
        sa.Column('author_avatar_url', sa.Text(), nullable=True),
        sa.Column('state', sa.String(50), default='open', nullable=False),
        sa.Column('base_branch', sa.String(255), nullable=False),
        sa.Column('head_branch', sa.String(255), nullable=False),
        sa.Column('base_sha', sa.String(100), nullable=False),
        sa.Column('head_sha', sa.String(100), nullable=False),
        sa.Column('github_pr_url', sa.Text(), nullable=True),
        sa.Column('diff_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_pull_requests_repo_number', 'pull_requests', ['repository_id', 'pr_number'], unique=True)

    # 12. pr_analyses
    op.create_table(
        'pr_analyses',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('pull_request_id', UUID_TYPE, sa.ForeignKey('pull_requests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('head_sha', sa.String(100), nullable=False),
        sa.Column('base_sha', sa.String(100), nullable=False),
        sa.Column('status', sa.String(50), default='pending', nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('total_findings', sa.Integer(), default=0, nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_pr_analyses_pr_id', 'pr_analyses', ['pull_request_id'])

    # 13. findings
    op.create_table(
        'findings',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('pr_analysis_id', UUID_TYPE, sa.ForeignKey('pr_analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('severity', sa.String(50), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('file_path', sa.String(1024), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('suggestion', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), default=0.0, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_findings_analysis_id', 'findings', ['pr_analysis_id'])

    # 14. impact_relationships
    op.create_table(
        'impact_relationships',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('pr_analysis_id', UUID_TYPE, sa.ForeignKey('pr_analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_symbol', sa.String(255), nullable=False),
        sa.Column('source_file_path', sa.String(1024), nullable=False),
        sa.Column('affected_symbol', sa.String(255), nullable=False),
        sa.Column('affected_file_path', sa.String(1024), nullable=False),
        sa.Column('impact_type', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_impact_relationships_analysis', 'impact_relationships', ['pr_analysis_id'])

    # 15. conversations
    op.create_table(
        'conversations',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('repository_id', UUID_TYPE, sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID_TYPE, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(255), default='New Conversation', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_conversations_repo_user', 'conversations', ['repository_id', 'user_id'])

    # 16. messages
    op.create_table(
        'messages',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('conversation_id', UUID_TYPE, sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('citations', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])

    # 17. background_tasks
    op.create_table(
        'background_tasks',
        sa.Column('id', UUID_TYPE, primary_key=True),
        sa.Column('task_id', sa.String(255), nullable=False),
        sa.Column('task_name', sa.String(255), nullable=False),
        sa.Column('status', sa.String(50), default='PENDING', nullable=False),
        sa.Column('progress_percentage', sa.Float(), default=0.0, nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_background_tasks_task_id', 'background_tasks', ['task_id'], unique=True)


def downgrade() -> None:
    op.drop_table('background_tasks')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('impact_relationships')
    op.drop_table('findings')
    op.drop_table('pr_analyses')
    op.drop_table('pull_requests')
    op.drop_table('dependencies')
    op.drop_table('code_symbols')
    op.drop_table('code_chunks')
    op.drop_table('indexed_files')
    op.drop_table('commits')
    op.drop_table('repository_branches')
    op.drop_table('repositories')
    op.drop_table('webhook_events')
    op.drop_table('github_installations')
    op.drop_table('users')
