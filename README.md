# CodeLens AI — Codebase Intelligence & Pull Request Analyzer

> **Tagline**: Understand Your Codebase. Catch Bugs Before They Ship.  
> **Type**: Full-Stack AI-Powered Developer Intelligence Platform.

---

## Overview

**CodeLens AI** is a production-grade developer platform combining repository AST parsing (Tree-sitter), hybrid retrieval (PostgreSQL full-text search + pgvector 768-dim embeddings), and multi-agent LangGraph workflows to provide:
1. **Codebase Intelligence**: Verifiable RAG chat with exact file/line citations and zero hallucinations.
2. **Automated Pull Request Analysis**: Deep multi-vector review (bugs, security, performance, breaking changes, regressions, missing tests).
3. **Cross-File Impact & Dependency Graph**: Static call/import analysis mapping ripple effects before merging.
4. **Read-Only Safety**: Strictly zero code alteration or unauthorized commits to connected repositories.
5. **Interactive Demo Mode**: Out-of-the-box preloaded scenarios demonstrating real-world bugs, SQL injection, and breaking changes.

---

## Technology Stack

- **Frontend**: React 18/19, Vite, TypeScript, Tailwind CSS, shadcn/ui design system, React Router, TanStack Query, Axios, Lucide React, React Flow.
- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async & sync), Alembic.
- **Database & Vectors**: PostgreSQL 16 with `pgvector` extension (768-dimensional embeddings for Gemini `text-embedding-004`).
- **Async Queue**: Redis & Celery background workers.
- **AI & Analysis**: Google Gemini API, LangChain, LangGraph, Tree-sitter AST parsers.
- **Containerization**: Docker & Docker Compose.

---

## Project Structure

```
codelens-ai/
|-- frontend/               # React + Vite + TypeScript web application
|   |-- src/
|   |   |-- components/     # Layout, Sidebar, Header, UI widgets
|   |   |-- pages/          # Overview, Repositories, Chat, PRs, Impact, Settings
|   |   |-- services/       # Axios API client
|   |   |-- types/          # TypeScript interface contracts
|   |-- Dockerfile
|   |-- package.json
|   |-- vite.config.ts
|
|-- backend/                # FastAPI backend & Celery workers
|   |-- app/
|   |   |-- api/v1/         # Health & REST API routers
|   |   |-- core/           # Pydantic BaseSettings config
|   |   |-- database/       # Async/Sync engines & session makers
|   |   |-- models/         # 17 SQLAlchemy models
|   |   |-- schemas/        # Pydantic request/response schemas
|   |   |-- workers/        # Celery task definitions
|   |   |-- main.py         # FastAPI application entrypoint
|   |-- alembic/            # Migrations with pgvector support
|   |-- tests/              # Pytest test suite
|   |-- Dockerfile
|   |-- requirements.txt
|
|-- docker-compose.yml      # Orchestration for DB, Redis, Backend, Celery, Frontend
|-- .env.example            # Environment configuration template
|-- .gitignore              # Git ignore rules
|-- README.md               # Documentation
```

---

## Local Development Quick Start

### Option A: Using Docker Compose (Recommended)

1. Clone repository and copy environment configuration:
   ```bash
   cp .env.example .env
   ```
2. Start all services (PostgreSQL + pgvector, Redis, FastAPI, Celery, React):
   ```bash
   docker-compose up --build
   ```
3. Access services:
   - **Frontend Dashboard**: [http://localhost:5173](http://localhost:5173)
   - **FastAPI OpenAPI Docs**: [http://localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs)
   - **Liveness Health Check**: [http://localhost:8000/health](http://localhost:8000/health)
   - **Readiness Probe**: [http://localhost:8000/ready](http://localhost:8000/ready)

---

### Option B: Running Locally on Host

#### 1. Backend Setup
```bash
# Navigate to backend
cd backend

# Create & activate Python 3.12 virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Run backend test suite
pytest tests -v

# Start FastAPI server
uvicorn app.main:app --reload --port 8000
```

#### 2. Celery Worker (in a separate terminal)
```bash
cd backend
venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info
```

#### 3. Frontend Setup (in a separate terminal)
```bash
cd frontend
npm install
npm run dev
```

#### Live PostgreSQL tests (optional)
The default test suite uses SQLite. To also run migrations, type checks and pgvector
queries against real PostgreSQL, start the database (`docker compose up -d db`), create a
**disposable** database, and point the tests at it. These tests drop and recreate every
table in that database, so never use your development database.
```bash
docker exec codelens_db createdb -U postgres codelens_test
CODELENS_TEST_POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5432/codelens_test pytest tests -v
```
(In PowerShell: `$env:CODELENS_TEST_POSTGRES_URL="postgresql+psycopg://..."` then `pytest`.)

> **Database schema is managed only by Alembic.** The API no longer creates tables at
> startup; always run `alembic upgrade head` first (Docker Compose does this for you).
> If you have an older local SQLite file that was created automatically by a previous
> version (it has tables but no `alembic_version` table), either delete it and run
> `alembic upgrade head`, or mark it as current with `alembic stamp head`.

---

## GitHub App setup

CodeLens uses a single **GitHub App** for both sign-in and repository access. Repository
access is read-only.

1. Create a GitHub App (GitHub → Settings → Developer settings → GitHub Apps → New).
2. **Repository permissions** (read-only): *Contents: Read*, *Metadata: Read*,
   *Pull requests: Read*. Do not grant any write permission.
3. **Subscribe to events**: *Installation*, *Installation repositories*, *Push*, *Pull request*.
4. **Webhook URL**: `<BACKEND_URL>/api/v1/webhooks/github`, with a random **Webhook secret**.
   For local development, expose the backend with a tunnel.
5. **Callback URL**: `<BACKEND_URL>/api/v1/auth/callback`.
6. **Setup URL** (optional, recommended): `<BACKEND_URL>/api/v1/auth/github/login`, with
   *Redirect on update* enabled. Leave *Request user authorization (OAuth) during
   installation* **unchecked**: CodeLens starts its own sign-in, which carries the
   anti-CSRF `state` parameter that GitHub's install-time authorization omits.
7. Generate a private key, then fill in `.env`: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`,
   `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` (the App's own client credentials),
   `GITHUB_WEBHOOK_SECRET`, and `GITHUB_PRIVATE_KEY_PATH` (or `GITHUB_PRIVATE_KEY`).

**Flow:** *Sign in with GitHub* → CodeLens lists the App installations you can access
(`GET /user/installations`) and links them to your account → *Connect Repository* lists
the repositories each installation was granted → connecting fetches the repository's
metadata from GitHub. The GitHub user token is used only during sign-in and is not
stored. Installations created while you are already a CodeLens user are also linked
from the `installation` webhook.

**Known limitation:** an installation belongs to the first CodeLens user who links it.
Other members of the same organization do not see it yet.

---

## Current Status

**Phase 1 (Foundation)** and **Phase 2 (GitHub integration)** are in place, including
Milestone 0 stabilization: GitHub App installation linking, server-verified repository
connection, authenticated and user-scoped repository APIs, OAuth `state` protection,
production configuration guards, and migrations reconciled with the models (guarded by
an `alembic check` test).

**Next: Phase 3 (Repository indexing engine).** Indexing, parsing, embeddings,
search, PR analysis and impact analysis are not implemented yet.
