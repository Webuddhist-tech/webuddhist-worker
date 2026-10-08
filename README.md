# WebBuddhist Worker API

This is the worker backend API for the WebBuddhist application.

It handles background work such as TTS/audio generation and notification
dispatch. Plan/content data owned by the main backend is accessed **only via
HTTP APIs** — the worker does not share or query the backend Postgres schema
for subtasks, plan days, or related domain tables.

## Installation

Follow these steps to set up the project on your local machine:

1. Clone the repository:
    ```sh
    git clone https://github.com/yourusername/webuddhist-worker.git
    ```
2. Navigate to the project directory:
    ```sh
    cd webuddhist-worker
    ```
3. Install the dependencies:
    ```sh
    poetry install
    ```

## Configuration

Set at least:

| Variable | Purpose |
|----------|---------|
| `BACKEND_API_URL` | Base URL of the main backend API (default `http://127.0.0.1:8000/api/v1`) |
| `NOTIFICATION_DISPATCH_SECRET_TOKEN` | Shared secret sent as `X-Dispatch-Token` on internal backend calls |
| `DATABASE_URL` | Worker-owned Postgres (reminders / notification tables only) |
| `AUDIO_SQS_QUEUE_URL` | SQS queue for audio jobs produced by the backend |
| `PRAYER_TRANSLATION_SQS_QUEUE_URL` | SQS queue for prayer request translation jobs produced by the backend |
| `GEMINI_API_KEY` / `GEMINI_PRAYER_TRANSLATION_MODEL` | Gemini for prayer translations (also used by TTS and segment chat) |
| `CHAT_NOTIFICATION_SQS_QUEUE_URL` | SQS queue for chat message notification events produced by the backend |
| `PRAYER_NOTIFICATION_SQS_QUEUE_URL` | SQS queue for prayer (`PRAYER_RECEIVED`) notification events produced by the backend |
| `CACHE_CONNECTION_STRING` | Redis URL used for chat notification per-device idempotency, and the segment chat source cache and rate limit |
| `SEGMENT_CHAT_LLM_MODEL` / `SEGMENT_CHAT_LLM_API_KEY` | LLM for segment AI chat (Gemini; the key falls back to `GEMINI_API_KEY`). All options: [docs/segment-chat-api.md](docs/segment-chat-api.md) |
| `OPENPECHA_LIBRARY_URL` | OpenPecha library API that segment AI chat reads segments and related texts from (default `https://library.webuddhist.com`) |

The worker talks to the backend for audio job status, generation payloads
(day/subtask content), persisting generation results, and prayer translation
payload/result exchange. Do **not** point
`DATABASE_URL` at the backend database for plan/subtask data.

## Database Setup (worker-owned DB only)

Start local infra from the backend repo if needed (Postgres/Mongo/Redis/etc.),
then apply **worker** migrations against the worker database:

```sh
poetry run alembic upgrade head
```

These migrations cover worker tables (e.g. upcoming reminders), not backend
plan tables.

## Running the Application

1. Ensure the main backend is running (default `http://127.0.0.1:8000`).
2. Start the worker:
    ```sh
    poetry run uvicorn worker_api.app:api --port 8001 --reload
    ```

The application will be available at `http://127.0.0.1:8001/`.

## API Documentation

You can access the Swagger UI for the API documentation at `http://127.0.0.1:8001/docs`.

## Running Tests

To run tests, execute the following command:
```sh
poetry run pytest
```

To check the coverage:
```sh
poetry run pytest --cov=worker_api
```
```sh
poetry run coverage html
```

Open the coverage report:
```sh
open htmlcov/index.html
```

## Alembic Commands

Alembic is used for handling **worker-owned** database migrations:

1. Create a new migration:
    ```sh
    poetry run alembic revision --autogenerate -m "description of migration"
    ```

2. Apply the latest migrations:
    ```sh
    poetry run alembic upgrade head
    ```

3. Downgrade to a previous migration:
    ```sh
    poetry run alembic downgrade -1
    ```

4. View the current migration history:
    ```sh
    poetry run alembic history
    ```

5. Show the current migration state:
    ```sh
    poetry run alembic current
    ```

## Shared Infrastructure

This worker can reuse the same local Docker services as `WeBuddhist-Backend`
(Postgres, MongoDB, Redis, Elasticsearch), but:

- Backend plan/content data is fetched and updated through backend internal APIs
- Worker Postgres should only hold worker-owned tables

Typical local ports:

- PostgreSQL: 5434
- MongoDB: 27017
- Redis/Dragonfly: 6379
- Elasticsearch: 9200

Both apps can run simultaneously:

- `WeBuddhist-Backend`: http://127.0.0.1:8000
- `webuddhist-worker`: http://127.0.0.1:8001
