import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User,
    GitHubInstallation,
    WebhookEvent,
    Repository,
    RepositoryBranch,
    Commit,
    IndexedFile,
    CodeChunk,
    CodeSymbol,
    Dependency,
    PullRequest,
    PRAnalysis,
    Finding,
    ImpactRelationship,
    Conversation,
    Message,
    BackgroundTask,
)


@pytest.mark.asyncio
async def test_all_17_models_persistence(db_session: AsyncSession):
    # 1. User
    user = User(
        github_id=123456,
        username="octocat",
        email="octocat@github.com",
        avatar_url="https://github.com/images/error/octocat_happy.gif",
    )
    db_session.add(user)
    await db_session.flush()
    assert user.id is not None

    # 2. GitHubInstallation
    installation = GitHubInstallation(
        user_id=user.id,
        installation_id=987654,
        account_login="octocat",
        account_type="User",
        permissions={"contents": "read", "pull_requests": "read"},
    )
    db_session.add(installation)
    await db_session.flush()
    assert installation.id is not None

    # 3. WebhookEvent
    webhook = WebhookEvent(
        delivery_id="del-12345-abcde",
        event_type="pull_request.opened",
        payload={"action": "opened", "number": 1},
        status="processed",
    )
    db_session.add(webhook)
    await db_session.flush()
    assert webhook.id is not None

    # 4. Repository
    repo = Repository(
        installation_id=installation.id,
        github_repo_id=555666,
        name="hello-world",
        owner="octocat",
        full_name="octocat/hello-world",
        default_branch="main",
        is_private=False,
    )
    db_session.add(repo)
    await db_session.flush()
    assert repo.id is not None

    # 5. RepositoryBranch
    branch = RepositoryBranch(
        repository_id=repo.id,
        name="main",
        latest_commit_sha="a1b2c3d4e5",
        is_default=True,
    )
    db_session.add(branch)
    await db_session.flush()
    assert branch.id is not None

    # 6. Commit
    commit = Commit(
        repository_id=repo.id,
        sha="a1b2c3d4e5",
        message="Initial commit",
        author_name="Mona Lisa Octocat",
    )
    db_session.add(commit)
    await db_session.flush()
    assert commit.id is not None

    # 7. IndexedFile
    indexed_file = IndexedFile(
        repository_id=repo.id,
        file_path="src/main.py",
        language="python",
        size_bytes=1024,
        file_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    db_session.add(indexed_file)
    await db_session.flush()
    assert indexed_file.id is not None

    # 8. CodeChunk
    chunk = CodeChunk(
        file_id=indexed_file.id,
        repository_id=repo.id,
        symbol_name="authenticate_user",
        symbol_type="function",
        start_line=10,
        end_line=25,
        content="def authenticate_user(): pass",
        commit_sha="a1b2c3d4e5",
        embedding_key="k" * 64,
    )
    db_session.add(chunk)
    await db_session.flush()
    assert chunk.id is not None

    # 8b. ChunkEmbedding (vectors live in their own table, keyed by embedding_key)
    from app.models import ChunkEmbedding
    from app.models.indexing import EMBEDDING_DIMENSIONS

    stored = ChunkEmbedding(
        repository_id=repo.id, embedding_key="k" * 64, model="gemini-embedding-2",
        dimensions=EMBEDDING_DIMENSIONS, embedding=[0.1] * EMBEDDING_DIMENSIONS,
    )
    db_session.add(stored)
    await db_session.flush()
    assert stored.id is not None

    # 9. CodeSymbol
    symbol = CodeSymbol(
        file_id=indexed_file.id,
        name="authenticate_user",
        symbol_type="function",
        start_line=10,
        end_line=25,
        signature="def authenticate_user(username: str, token: str) -> bool",
    )
    db_session.add(symbol)
    await db_session.flush()
    assert symbol.id is not None

    # 10. Dependency
    dep = Dependency(
        repository_id=repo.id,
        source_symbol_id=symbol.id,
        source_file_id=indexed_file.id,
        source_name="authenticate_user",
        target_name="verify_password",
        relationship_type="CALLS",
    )
    db_session.add(dep)
    await db_session.flush()
    assert dep.id is not None

    # 11. PullRequest
    pr = PullRequest(
        repository_id=repo.id,
        pr_number=42,
        title="Refactor authentication middleware",
        author_username="contributor-bob",
        state="open",
        base_branch="main",
        head_branch="feature/auth-cleanup",
        base_sha="a1b2c3d4e5",
        head_sha="f6g7h8i9j0",
    )
    db_session.add(pr)
    await db_session.flush()
    assert pr.id is not None

    # 12. PRAnalysis
    analysis = PRAnalysis(
        pull_request_id=pr.id,
        head_sha="f6g7h8i9j0",
        base_sha="a1b2c3d4e5",
        status="completed",
        summary="PR introduces breaking change in authenticate_user signature.",
        total_findings=1,
    )
    db_session.add(analysis)
    await db_session.flush()
    assert analysis.id is not None

    # 13. Finding
    finding = Finding(
        pr_analysis_id=analysis.id,
        category="breaking_change",
        severity="high",
        title="Modified authenticate_user parameter order",
        description="Signature changed from (username, password) to (password, username).",
        evidence="def authenticate_user(password: str, username: str):",
        file_path="src/main.py",
        start_line=10,
        end_line=12,
        suggestion="Maintain backward-compatible keyword arguments.",
        confidence=0.95,
    )
    db_session.add(finding)
    await db_session.flush()
    assert finding.id is not None

    # 14. ImpactRelationship
    impact = ImpactRelationship(
        pr_analysis_id=analysis.id,
        source_symbol="authenticate_user",
        source_file_path="src/main.py",
        affected_symbol="login_route",
        affected_file_path="src/routes/auth.py",
        impact_type="direct",
        description="login_route calls authenticate_user using positional arguments.",
    )
    db_session.add(impact)
    await db_session.flush()
    assert impact.id is not None

    # 15. Conversation
    convo = Conversation(
        repository_id=repo.id,
        user_id=user.id,
        title="Investigating authentication flow",
    )
    db_session.add(convo)
    await db_session.flush()
    assert convo.id is not None

    # 16. Message
    msg = Message(
        conversation_id=convo.id,
        role="assistant",
        content="Authentication is implemented in `src/main.py` lines 10-25.",
        citations=[{
            "file_path": "src/main.py",
            "start_line": 10,
            "end_line": 25,
            "snippet": "def authenticate_user(): pass",
        }],
    )
    db_session.add(msg)
    await db_session.flush()
    assert msg.id is not None

    # 17. BackgroundTask
    task = BackgroundTask(
        task_id="celery-task-9988-uuid",
        task_name="app.workers.tasks.index_repository",
        status="SUCCESS",
        progress_percentage=100.0,
        result={"files_indexed": 1, "chunks_created": 1},
    )
    db_session.add(task)
    await db_session.flush()
    assert task.id is not None

    # Verify querying and relationships
    q_repo = await db_session.execute(select(Repository).where(Repository.id == repo.id))
    fetched_repo = q_repo.scalar_one()
    assert fetched_repo.full_name == "octocat/hello-world"

    q_findings = await db_session.execute(select(Finding).where(Finding.pr_analysis_id == analysis.id))
    findings_list = q_findings.scalars().all()
    assert len(findings_list) == 1
    assert findings_list[0].category == "breaking_change"
