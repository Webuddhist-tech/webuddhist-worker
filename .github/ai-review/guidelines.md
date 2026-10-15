# Project review guidelines

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PostgreSQL, Redis, boto3/SQS consumers, Google GenAI / TTS, pytest (via poetry).

Structure

- Feature packages under `worker_api/<feature>/` with `models/`, `repositories/`, `services/`, `*_views.py`, `schemas.py` and `*_sqs_client.py`.
- Views stay thin; business logic lives in services, DB access in repositories.
- Schema changes need an Alembic migration in `migrations/` with a single head and a working downgrade.
- New logic should come with pytest tests under `tests/`.

Correctness (background jobs)

- SQS consumers must be idempotent and safe to retry; delete messages only after successful processing; handle poison messages.
- Watch for blocking calls inside `async def`, missing timeouts/retries on external APIs, unbounded loops, and DB sessions not closed on error.
- Feature flags and config come from `worker_api/config.py`; check new settings have safe defaults.

Security

- No secrets, service-account JSON, tokens or keys in code, tests or logs. `secrets/` and `.env` must never be committed.
- Internal endpoints must verify the caller (internal auth), not be publicly reachable without checks.
- Don't log tokens, passwords or personal data.
