# WebBuddhist Worker API Endpoints

This document describes every HTTP endpoint exposed by the WebBuddhist Worker API.

**Base URL:** `/api/v1` (configured as FastAPI `root_path`)

**Interactive docs:** `/api/v1/docs` (ReDoc)

---

## Overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Liveness check |
| GET | `/props` | None | Service metadata |
| POST | `/audio/generate` | None | Generate TTS audio |
| POST | `/notifications/reminders` | None | Enroll a plan reminder |
| PUT | `/notifications/reminders/{user_id}/{plan_id}` | None | Update a pending reminder |
| DELETE | `/notifications/reminders/{user_id}/{plan_id}` | None | Cancel a pending reminder |
| POST | `/internal/dispatch-due-notifications` | `X-Dispatch-Token` | Send due plan reminders |
| GET | `/internal/routine-notification-targets` | `X-Dispatch-Token` | Preview routine notification targets |
| POST | `/internal/dispatch-routine-notifications` | `X-Dispatch-Token` | Send routine notifications |
| POST | `/internal/dispatch-verse-of-day-notifications` | `X-Dispatch-Token` | Send verse-of-the-day notifications |
| POST | `/internal/send-test-notification` | `X-Dispatch-Token` | Send a test push notification |

---

## General

### `GET /health`

Liveness probe. Returns **204 No Content** when the service is running.

Used by load balancers and orchestrators. This path is excluded from request observability logging by default.

---

### `GET /props`

Returns basic service metadata.

**Response (200):**

```json
{
  "app_name": "WebBuddhist Worker",
  "version": "0.0.1",
  "status": "operational"
}
```

---

## Audio

### `POST /audio/generate`

Generates text-to-speech audio using Monlam TTS for Tibetan (`bo`) or Gemini TTS for all other language codes, uploads the result to S3, and returns a presigned URL.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `language` | string | Yes | Language code for TTS (e.g. `bo`, `en`, `fr`). Tibetan (`bo`) uses Monlam; all other codes use Gemini. |
| `text` | string | No* | Raw text to synthesize |
| `day_id` | UUID | No* | Plan day ID — generates audio for all text subtasks in that day |
| `sub_task_id` | UUID | No* | Single subtask ID — generates audio for that subtask only |
| `type` | string | No | Audio style: `TEXT_READING` (default), `RECITATION`, or `INSTRUCTION` |
| `voice_name` | string | No | Monlam voice name (default: `dolkar_lhasa_female`) |
| `s3_key_prefix` | string | No | Custom S3 key prefix (only used with `text` input) |

\* Provide exactly one of `text`, `day_id`, or `sub_task_id`.

**Behavior by input mode:**

- **`text`** — Synthesizes the given string, uploads to `audio/generated/` (or `s3_key_prefix`), returns presigned URL. Does not write to the database.
- **`sub_task_id`** — Loads the subtask, generates audio if content type is `TEXT` or `SOURCE_REFERENCE`, stores the S3 key on the subtask, and records timestamps.
- **`day_id`** — Loads all text subtasks for the plan day, generates or reuses per-subtask audio, concatenates into one WAV, uploads to `audio/plan_days/{plan_id}/{plan_item_id}/`, and persists a `PlanItemAudio` row.

**Response (200):**

```json
{
  "audio_url": "https://...",
  "audio_duration_ms": 12345,
  "s3_key": "audio/generated/..."
}
```

When `day_id` is provided but no audio segments are produced, returns an empty array `[]`.

**Errors:**

- `404` — Subtask not found (`sub_task_id` mode)
- `400` — Invalid content type for audio generation

---

## Notifications — Reminders

Plan reminders are stored in PostgreSQL. Each reminder has a scheduled `trigger_at` time, device token, platform, and a recurring routine configuration. The dispatch job sends push notifications when reminders become due.

### `POST /notifications/reminders`

Enrolls (or re-enrolls) a user for plan reminder notifications.

Cancels any existing pending reminder for the same `user_id` + `plan_id`, then creates a new pending reminder with the next computed trigger time.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | UUID | Yes | User identifier |
| `plan_id` | UUID | Yes | Plan identifier |
| `timezone` | string | Yes | IANA timezone (e.g. `America/New_York`) |
| `device_token` | string | Yes | FCM/APNs device token |
| `platform` | string | Yes | `android` or `ios` |
| `routine` | object | Yes | Recurrence configuration (see below) |

**`routine` object:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `times` | string[] | Yes | One or more times in `HH:MM` format (24-hour) |
| `days_of_week` | int[] | No | ISO weekday numbers (`0` = Monday, `6` = Sunday). Omit for every day. |
| `plan_name` | string | No | Used in notification title |
| `message_template` | string | No | Custom body template |
| `current_day_number` | int | No | Current plan day number for message content |

**Response (201):**

```json
{
  "id": "uuid",
  "user_id": "uuid",
  "plan_id": "uuid",
  "trigger_at": "2026-06-30T08:00:00Z",
  "timezone": "America/New_York",
  "status": "pending",
  "platform": "android",
  "routine_config": { "times": ["08:00"], "days_of_week": [0, 1, 2, 3, 4] }
}
```

---

### `PUT /notifications/reminders/{user_id}/{plan_id}`

Updates the pending reminder for a user/plan pair. Partial updates are supported — omitted fields keep their existing values.

Cancels the current pending reminder and creates a new one with a freshly computed `trigger_at`.

**Request body:** Same fields as enroll, all optional (`timezone`, `device_token`, `platform`, `routine`).

**Response (200):** Same shape as enroll response.

**Errors:**

- `404` — No pending reminder exists for this user and plan

---

### `DELETE /notifications/reminders/{user_id}/{plan_id}`

Cancels all pending reminders for the given user and plan.

**Response (200):**

```json
{
  "cancelled": 1
}
```

`cancelled` is the number of rows updated.

---

## Notifications — Internal (Dispatch)

Internal endpoints are intended for **Cloud Scheduler** or other trusted backend callers. They require the `X-Dispatch-Token` header matching the `NOTIFICATION_DISPATCH_SECRET_TOKEN` environment variable.

When `NOTIFICATION_DISPATCH_ENABLED` is `false`, dispatch endpoints return zero counts without sending pushes.

For push payload format, see [notification-format.md](./notification-format.md).

Routine notification targets are fetched from the **backend** API (`GET /internal/routine-notification-targets`). The worker does not query routine tables directly for dispatch.

---

### `POST /internal/dispatch-due-notifications`

Processes due plan reminders from the database.

**Flow:**

1. Fetches up to `NOTIFICATION_DISPATCH_BATCH_SIZE` reminders where `trigger_at <= now` and status is pending.
2. Skips reminders already sent in the current idempotency window (when `NOTIFICATION_IDEMPOTENCY_ENABLED` is `true`).
3. Sends a push notification (title/body from routine config or defaults).
4. Marks the reminder as sent and schedules the next occurrence based on the routine.
5. Returns aggregate counts.

**Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Dispatch-Token` | Yes | Dispatch secret token |

**Response (200):**

```json
{
  "processed": 10,
  "sent": 8,
  "failed": 1,
  "skipped": 1
}
```

**Errors:**

- `401` — Invalid or missing dispatch token
- `503` — Dispatch token not configured

---

### `GET /internal/routine-notification-targets`

Returns a **preview** of users who would receive routine notifications at the current UTC time, without sending anything.

Proxies to the backend `GET /internal/routine-notification-targets` endpoint, which matches routine time blocks whose stored UTC time (`routine_time_blocks.time_utc`) equals the current UTC minute. Only **`PLAN`** and **`SERIES`** sessions are included.

**Response (200):**

```json
{
  "generated_at": "2026-06-30T12:00:00Z",
  "matched_time_utc": "12:00",
  "groups": [
    {
      "session_type": "PLAN",
      "source_id": "uuid",
      "source_image_url": "https://...",
      "users": [
        {
          "user_id": "uuid",
          "notification": {
            "title": "Day 3 — Breath Awareness",
            "body": "Take 10 minutes for today's practice.",
            "image_url": "https://..."
          },
          "push_devices": [
            { "token": "...", "platform": "android" }
          ]
        }
      ]
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `notification.title` | Resolved title sent in the push |
| `notification.body` | Resolved body sent in the push |
| `notification.image_url` | Presigned image URL sent in the push (`notification.image` and `data.image_url`). Empty when no image is resolved. |
| `source_image_url` | Group-level plan/series cover preview only; not duplicated in the FCM data payload |

**Session types in routine dispatch:** `PLAN`, `SERIES` only

---

### `POST /internal/dispatch-routine-notifications`

Builds the same target list as the preview endpoint, then sends FCM push notifications to each device. Each push includes:

- Display notification: `title`, `body`, optional `image`
- Data payload: `session_type`, `source_id`, `title`, `body`, `image_url`

See [notification-format.md](./notification-format.md) for resolution rules and examples.

**Response (200):** Same as the preview response, plus dispatch counts:

```json
{
  "generated_at": "...",
  "matched_time_utc": "12:00",
  "groups": [ "..." ],
  "processed": 5,
  "sent": 4,
  "failed": 0,
  "skipped": 1
}
```

Devices on platforms without push configuration are counted as `skipped`.

---

### `POST /internal/dispatch-verse-of-day-notifications`

Fetches verse-of-the-day notification targets from the backend `GET /internal/verse-of-day-notification-targets` endpoint, then sends FCM push notifications to each device. The backend matches each user's push devices whose IANA timezone currently reads 10:00 local time (users without a stored timezone default to UTC), and resolves the verse text in that user's preferred language (defaulting to English when unset or untranslated). Users with no verse published for their local date, or with no verse text in either their language or the English fallback, are omitted from the target list.

Each push includes:

- Display notification: `title` (`NOTIFICATION_DEFAULT_TITLE`), `body` (the resolved verse text), optional `image`
- Data payload: `notification_type=VERSE_OF_DAY`, `session_type=VERSE_OF_DAY`, `title`, `body`, `image_url`

**Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Dispatch-Token` | Yes | Dispatch secret token |

**Response (200):**

```json
{
  "generated_at": "2026-06-30T04:15:00Z",
  "users": [
    {
      "user_id": "uuid",
      "notification": {
        "title": "WebBuddhist",
        "body": "May all beings be happy and free from suffering.",
        "image_url": "https://..."
      },
      "push_devices": [
        {"token": "fcm-token", "platform": "android"}
      ]
    }
  ],
  "processed": 1,
  "sent": 1,
  "failed": 0,
  "skipped": 0
}
```

This endpoint is intended to be called every minute by an external scheduler (e.g. Cloud Scheduler), the same way `/internal/dispatch-routine-notifications` is — each minute a different set of users crosses their local 10:00 threshold.

---

### `POST /internal/send-test-notification`

Sends a push notification directly for testing. Does not read from or write to the reminder schedule.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_type` | string | Yes | Routine session type for the FCM data payload |
| `title` | string | No | Notification title (default: `NOTIFICATION_DEFAULT_TITLE`) |
| `body` | string | No | Notification body (default: random from `NOTIFICATION_DEFAULT_BODIES`) |
| `source_id` | UUID | No | Related entity UUID for deep linking |
| `device_token` | string | No* | Send to a specific FCM token |
| `email` | string | No* | Send to all active push devices for this user email |

\* Provide exactly one of `device_token` or `email`.

**Response (200):**

```json
{
  "title": "WebBuddhist",
  "body": "Time for your daily practice.",
  "session_type": "PLAN",
  "source_id": "550e8400-e29b-41d4-a716-446655440000",
  "sent": 1,
  "failed": 0,
  "deliveries": [
    {
      "device_token_prefix": "dGhpcyBpcyBh",
      "platform": "android",
      "status": "sent",
      "error": null
    }
  ]
}
```

**Errors:**

- `404` — No active push devices found for the given email
- `503` — Firebase is not configured (`GOOGLE_APPLICATION_CREDENTIALS`)

---

## Authentication Summary

| Endpoint group | Authentication |
|----------------|----------------|
| `/health`, `/props` | None |
| `/audio/*` | None |
| `/notifications/reminders/*` | None (called by main backend) |
| `/internal/*` | `X-Dispatch-Token` header |

---

## Related Documentation

- [Push notification payload format](./notification-format.md)
- [Notification flow diagram](./notification-flow.excalidraw)
