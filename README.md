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

---

## Current Status: Phase 1 (Foundation) Complete

- [x] Initialized Vite + React + TypeScript + Tailwind frontend skeleton.
- [x] Initialized FastAPI backend with async SQLAlchemy 2.0 & Pydantic v2.
- [x] Implemented all 17 database models with pgvector 768-dim embedding column.
- [x] Configured Alembic with pgvector extension initialization.
- [x] Configured Celery worker application with Redis broker and diagnostic ping task.
- [x] Built multi-container Docker Compose file (`db`, `redis`, `backend`, `celery_worker`, `frontend`).
- [x] Implemented `/health` and `/ready` endpoints.
- [x] Built and verified pytest suite (all 6 tests passing).
- [x] Successfully compiled frontend with `npm run build`.
