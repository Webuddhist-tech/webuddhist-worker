# Push Notification Format

This document describes the push notification payload sent by the WebBuddhist worker to mobile devices. Use it when implementing notification handling, deep linking, or routing in the frontend app.

## Overview

The worker sends notifications via **Firebase Cloud Messaging (FCM)** for both Android and iOS.

Each push includes:

1. A **display notification** (`title`, `body`, and optional `image`) shown in the system tray.
2. A **data payload** with routing metadata plus the resolved notification content (`title`, `body`, `image_url`).

## Push Payload

```json
{
  "notification": {
    "title": "Day 3 — Breath Awareness",
    "body": "Take 10 minutes for today's practice.",
    "image": "https://cdn.example.com/plans/cover.jpg"
  },
  "data": {
    "session_type": "PLAN",
    "source_id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Day 3 — Breath Awareness",
    "body": "Take 10 minutes for today's practice.",
    "image_url": "https://cdn.example.com/plans/cover.jpg"
  }
}
```

### Display notification

| Field   | Type   | Description |
|---------|--------|-------------|
| `title` | string | Notification title shown to the user |
| `body`  | string | Notification body shown to the user |
| `image` | string | Optional image URL for rich notifications (Android and supported iOS setups). Omitted when no image is resolved. |

### Data payload

All data values are **strings** (FCM requirement).

| Field          | Type   | Description |
|----------------|--------|-------------|
| `session_type` | string | Type of routine session (see [Session types](#session-types)) |
| `source_id`    | string | UUID of the related entity (plan or series). Empty string (`""`) when there is no linked entity |
| `title`        | string | Resolved notification title (same value as the display notification) |
| `body`         | string | Resolved notification body (same value as the display notification) |
| `image_url`    | string | Presigned HTTPS URL for the notification image. Empty string (`""`) when no image is resolved |

The app can read routing fields (`session_type`, `source_id`) and content fields (`title`, `body`, `image_url`) from `message.data` in both foreground and background handlers.

## Session types

Routine notifications are sent only for **`PLAN`** and **`SERIES`** sessions.

| `session_type` | `source_id` refers to |
|----------------|------------------------|
| `PLAN`         | Plan UUID              |
| `SERIES`       | Series UUID            |
| `CHAT`         | Chat room UUID         |
| `GROUP`        | Group UUID             |
| `VERSE_OF_DAY` | None (`source_id` field not present) |

Plan reminder dispatches (enrollment API) always use `session_type: "PLAN"`.

Chat message pushes use `session_type: "CHAT"` plus dedicated routing fields
(`notification_type`, `chat_kind`, `room_id`, `message_id`, `sender_id`, `group_id`).

Verse-of-the-day pushes use `session_type: "VERSE_OF_DAY"` plus `notification_type: "VERSE_OF_DAY"`.

Group join-request pushes use `session_type: "GROUP"` with `source_id` set to the
group UUID, plus `notification_type: "JOIN_REQUEST_CREATED"` or
`"JOIN_REQUEST_DECIDED"` and the routing fields `join_request_id`, `group_id`,
and `status`.

Event pushes use `source_id` set to the event UUID, in three kinds:

| `notification_type`  | `session_type`     | Sent when |
|-----------------------|--------------------|-----------|
| `EVENT`               | `EVENT`            | The event is published |
| `EVENT_REMINDER`      | `EVENT_REMINDER`   | Before each day the event runs (extra field: `reminder_type`, `T_MINUS_10` or `T_ZERO`) |
| `EVENT_ANNOUNCEMENT`  | `EVENT`            | An organizer sends one by hand from the CMS (extra field: `announcement_id`) |

All three are suppressed for an event whose organizer has switched
notifications off, and for a user who has muted that individual event.

## Default content

When no custom content is configured:

| Field   | Default value                    |
|---------|----------------------------------|
| `title` | `WebBuddhist`                    |
| `body`  | Random message from `NOTIFICATION_DEFAULT_BODIES` |
| `image_url` | Empty string                 |

Config keys: `NOTIFICATION_DEFAULT_TITLE`, `NOTIFICATION_DEFAULT_BODIES` (randomly selected).

## How title, body, and image are resolved

Content is resolved by the **backend** when building routine notification targets. The worker forwards the resolved values into the FCM notification and data payload.

### Routine notifications (time-block based)

Triggered when a user's routine time block matches the current UTC minute (`routine_time_blocks.time_utc`).

| Session type | Title | Body | Image (`image_url`) |
|--------------|-------|------|---------------------|
| `PLAN`       | Day notification title from `day_notifications`, or plan title if no day copy exists | Day notification body from `day_notifications`, or default body | See [Plan image rules](#plan-image-rules) |
| `SERIES`     | Series metadata title, or default title | Default body | Series cover image (presigned), or empty string |

For `PLAN`, the backend calculates the user's current day from plan progress and loads that day's notification copy from `day_notifications` (linked to the plan item / day).

#### Plan image rules

| Condition | `image_url` |
|-----------|-------------|
| Day notification exists with `image_type = CUSTOM` | Presigned URL for `day_notifications.image_url` |
| Day notification exists with `image_type = PLAN`, or no day notification | Presigned plan cover image (`plans.image_url`) |
| No plan cover available | Empty string |

#### Series image rules

| Condition | `image_url` |
|-----------|-------------|
| Series has a cover image | Presigned series cover image (`series.image`) |
| No series cover available | Empty string |

### Verse-of-the-day notifications (per-user timezone + language)

Triggered when a user's push devices' IANA timezone (from `user_metadata.timezone`, defaulting to `UTC` when unset) currently reads **10:00 local time**. The backend computes this dynamically per user on every poll (not a stored UTC time), so it stays correct across DST.

| Field | Resolution |
|-------|------------|
| `title` | `NOTIFICATION_DEFAULT_TITLE` (backend config; verse-of-day has no per-verse title) |
| `body` | The day's verse text (`verse_metadata.verse`) in the user's language (`user_metadata.language`, defaulting to `en` when unset), falling back to the English (`en`) translation when the user's language isn't available |
| `image_url` | Presigned URL from the verse's `image_urls`, or empty when none |

If no verse is published for the user's local date, or neither the user's language nor the `en` fallback has translated text, that user is omitted from the target list for that poll (no notification is sent).

### Plan reminders (enrollment API)

Used when the app enrolls reminders via `POST /api/v1/notifications/reminders`.

**Title/body resolution:**

1. `routine.message_template` (if set)
2. `"Time for {plan_name}"` (if `plan_name` is set)
3. `"Day {current_day_number}: time for your practice."` (if `current_day_number` is set)
4. Default title/body

**Data payload** includes `title` and `body` but not `image_url` (empty string):

```json
{
  "session_type": "PLAN",
  "source_id": "<plan_id>",
  "title": "Time for Morning Practice",
  "body": "Day 3: time for your practice.",
  "image_url": ""
}
```

## Reading the payload in the app

### Flutter (`firebase_messaging`)

```dart
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  final title = message.notification?.title ?? message.data['title'];
  final body = message.notification?.body ?? message.data['body'];
  final imageUrl = message.notification?.android?.imageUrl
      ?? message.data['image_url'];

  final sessionType = message.data['session_type'];
  final sourceId = message.data['source_id'];

  if (sourceId != null && sourceId.isNotEmpty) {
    // navigate to content for sessionType + sourceId
  }
});

FirebaseMessaging.onMessageOpenedApp.listen((RemoteMessage message) {
  final sessionType = message.data['session_type'];
  final sourceId = message.data['source_id'];
  // Handle notification tap
});
```

### Android (Firebase SDK)

```kotlin
override fun onMessageReceived(remoteMessage: RemoteMessage) {
    val title = remoteMessage.notification?.title ?: remoteMessage.data["title"]
    val body = remoteMessage.notification?.body ?: remoteMessage.data["body"]
    val imageUrl = remoteMessage.notification?.imageUrl
        ?: remoteMessage.data["image_url"]?.takeIf { it.isNotEmpty() }

    val sessionType = remoteMessage.data["session_type"]
    val sourceId = remoteMessage.data["source_id"]
}
```

### iOS (Firebase Messaging)

When the user taps a notification, read the data keys from the FCM payload:

```swift
let sessionType = userInfo["session_type"] as? String
let sourceId = userInfo["source_id"] as? String
let title = userInfo["title"] as? String
let body = userInfo["body"] as? String
let imageUrl = (userInfo["image_url"] as? String).flatMap { $0.isEmpty ? nil : $0 }
```

## Foreground vs background

| App state              | Behavior |
|------------------------|----------|
| Background or killed   | OS shows the notification using `title`, `body`, and `image` when present |
| Foreground             | App receives the message in the FCM handler; show UI manually if needed |

The `data` payload is available in both cases. Use it to route the user when they tap the notification.

## Example payloads

### Plan session (custom day notification + plan image)

```json
{
  "notification": {
    "title": "Day 3 — Breath Awareness",
    "body": "Take 10 minutes for today's practice.",
    "image": "https://cdn.example.com/plans/cover.jpg"
  },
  "data": {
    "session_type": "PLAN",
    "source_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "title": "Day 3 — Breath Awareness",
    "body": "Take 10 minutes for today's practice.",
    "image_url": "https://cdn.example.com/plans/cover.jpg"
  }
}
```

### Plan session (custom day image)

When the day notification uses `image_type = CUSTOM`, `image_url` points to the day-specific asset instead of the plan cover.

### Series session

```json
{
  "notification": {
    "title": "Morning Teachings",
    "body": "Time for your daily practice.",
    "image": "https://cdn.example.com/series/cover.jpg"
  },
  "data": {
    "session_type": "SERIES",
    "source_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "title": "Morning Teachings",
    "body": "Time for your daily practice.",
    "image_url": "https://cdn.example.com/series/cover.jpg"
  }
}
```

### Plan reminder (no image)

```json
{
  "notification": {
    "title": "Time for Morning Practice",
    "body": "Day 3: time for your practice."
  },
  "data": {
    "session_type": "PLAN",
    "source_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "title": "Time for Morning Practice",
    "body": "Day 3: time for your practice.",
    "image_url": ""
  }
}
```

### Verse of the day

```json
{
  "notification": {
    "title": "WebBuddhist",
    "body": "May all beings be happy and free from suffering.",
    "image": "https://cdn.example.com/verse-of-day/2026-06-30.jpg"
  },
  "data": {
    "notification_type": "VERSE_OF_DAY",
    "session_type": "VERSE_OF_DAY",
    "title": "WebBuddhist",
    "body": "May all beings be happy and free from suffering.",
    "image_url": "https://cdn.example.com/verse-of-day/2026-06-30.jpg"
  }
}
```

### Chat message (direct)

```json
{
  "notification": {
    "title": "Alice Doe",
    "body": "Hey, are you free later?"
  },
  "data": {
    "notification_type": "CHAT_MESSAGE",
    "session_type": "CHAT",
    "chat_kind": "PRIVATE",
    "room_id": "c1d2e3f4-a5b6-7890-abcd-ef1234567890",
    "message_id": "d2e3f4a5-b6c7-8901-bcde-f12345678901",
    "sender_id": "e3f4a5b6-c7d8-9012-cdef-123456789012",
    "group_id": "",
    "source_id": "c1d2e3f4-a5b6-7890-abcd-ef1234567890",
    "title": "Alice Doe",
    "body": "Hey, are you free later?",
    "image_url": ""
  }
}
```

### Chat message (group)

```json
{
  "notification": {
    "title": "Morning Sangha",
    "body": "Alice Doe: Practice starts in 10 minutes"
  },
  "data": {
    "notification_type": "CHAT_MESSAGE",
    "session_type": "CHAT",
    "chat_kind": "GROUP",
    "room_id": "c1d2e3f4-a5b6-7890-abcd-ef1234567890",
    "message_id": "d2e3f4a5-b6c7-8901-bcde-f12345678901",
    "sender_id": "e3f4a5b6-c7d8-9012-cdef-123456789012",
    "group_id": "f4a5b6c7-d8e9-0123-def0-234567890123",
    "source_id": "c1d2e3f4-a5b6-7890-abcd-ef1234567890",
    "title": "Morning Sangha",
    "body": "Alice Doe: Practice starts in 10 minutes",
    "image_url": ""
  }
}
```

### Group join request (moderators notified)

```json
{
  "notification": {
    "title": "Morning Sangha",
    "body": "Tenzin Tib asked to join Morning Sangha"
  },
  "data": {
    "notification_type": "JOIN_REQUEST_CREATED",
    "session_type": "GROUP",
    "join_request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "group_id": "f4a5b6c7-d8e9-0123-def0-234567890123",
    "status": "PENDING",
    "source_id": "f4a5b6c7-d8e9-0123-def0-234567890123",
    "title": "Morning Sangha",
    "body": "Tenzin Tib asked to join Morning Sangha",
    "image_url": ""
  }
}
```

### Group join request decided (requester notified)

```json
{
  "notification": {
    "title": "Morning Sangha",
    "body": "You've joined Morning Sangha"
  },
  "data": {
    "notification_type": "JOIN_REQUEST_DECIDED",
    "session_type": "GROUP",
    "join_request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "group_id": "f4a5b6c7-d8e9-0123-def0-234567890123",
    "status": "APPROVED",
    "source_id": "f4a5b6c7-d8e9-0123-def0-234567890123",
    "title": "Morning Sangha",
    "body": "You've joined Morning Sangha",
    "image_url": ""
  }
}
```

`status` is `APPROVED` or `REJECTED` for `JOIN_REQUEST_DECIDED`, and `PENDING`
for `JOIN_REQUEST_CREATED`. `title` and `body` are composed by the backend; the
worker forwards them unchanged.

## Chat notification delivery

Chat pushes are event-driven:

1. Backend persists the chat message.
2. Backend enqueues `{ "event_type": "CHAT_MESSAGE_CREATED", "version": 1, "message_id": "..." }` to `CHAT_NOTIFICATION_SQS_QUEUE_URL`.
3. Worker consumes the event, paginates `GET /internal/chat-notification-targets/{message_id}`, and sends FCM.
4. Permanently invalid tokens are deactivated via `POST /internal/push-devices/deactivate`.

**Recipients:**

| Chat kind | Recipients |
|-----------|------------|
| `PRIVATE` | The other participant only |
| `GROUP` | All users who joined the author group (`author_group_joins`), excluding the sender |

Configure:

| Variable | Purpose |
|----------|---------|
| `CHAT_NOTIFICATION_SQS_QUEUE_URL` | Dedicated SQS queue (backend producer, worker consumer) |
| `CHAT_NOTIFICATION_SQS_POLL_ENABLED` | Worker poll kill switch (`true`/`false`) |
| `CHAT_NOTIFICATION_SEND_CONCURRENCY` | Max concurrent FCM sends per event |
| `CHAT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS` | Redis TTL for `message_id + push_device_id` dedupe |

Attach a dead-letter queue (DLQ) to the chat notification SQS queue for poison messages.

## Join request notification delivery

Group join-request pushes are event-driven and use their own SQS queue, separate
from the chat queue:

1. A user asks to join a private group; a Studio moderator approves or rejects it.
2. Backend enqueues `{ "event_type": "JOIN_REQUEST_CREATED" | "JOIN_REQUEST_DECIDED", "version": 1, "join_request_id": "..." }` to `JOIN_REQUEST_NOTIFICATION_SQS_QUEUE_URL`.
3. Worker consumes the event, paginates `GET /internal/join-request-notification-targets/{join_request_id}`, and sends FCM.
4. Permanently invalid tokens are deactivated via `POST /internal/push-devices/deactivate`.

**Recipients:**

| Event | Recipients |
|-------|------------|
| `JOIN_REQUEST_CREATED` | The group's moderators |
| `JOIN_REQUEST_DECIDED` | The requesting user only |

Recipients with no registered push device are counted in `total` but omitted from
`recipients`; the worker treats that as a no-op and deletes the event.

Configure:

| Variable | Purpose |
|----------|---------|
| `JOIN_REQUEST_NOTIFICATION_SQS_QUEUE_URL` | Dedicated SQS queue (backend producer, worker consumer) |
| `JOIN_REQUEST_NOTIFICATION_SQS_POLL_ENABLED` | Worker poll kill switch (`true`/`false`) |
| `JOIN_REQUEST_NOTIFICATION_SEND_CONCURRENCY` | Max concurrent FCM sends per event |
| `JOIN_REQUEST_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS` | Redis TTL for `join_request_id + event_type + push_device_id` dedupe |

Attach a dead-letter queue (DLQ) to the join request notification SQS queue for poison messages.

## Preview API vs push payload

The backend preview endpoint (`GET /internal/routine-notification-targets`) returns additional server-side fields that are **not** sent to the device:

| Field | In preview API | In FCM push |
|-------|----------------|-------------|
| `notification.title` | Yes | Yes (`notification.title` and `data.title`) |
| `notification.body` | Yes | Yes (`notification.body` and `data.body`) |
| `notification.image_url` | Yes | Yes (`notification.image` and `data.image_url`) |
| `source_image_url` | Yes (group-level plan/series cover) | No — use `notification.image_url` per user |
| `user_id` | Yes | No |
| Deep link URL | No | No |

## Related API (enrollment only)

The worker also exposes reminder enrollment endpoints used by the app to schedule plan reminders:

| Method   | Path                                              | Purpose              |
|----------|---------------------------------------------------|----------------------|
| `POST`   | `/api/v1/notifications/reminders`                 | Enroll a reminder    |
| `PUT`    | `/api/v1/notifications/reminders/{user_id}/{plan_id}` | Update a reminder |
| `DELETE` | `/api/v1/notifications/reminders/{user_id}/{plan_id}` | Cancel a reminder |

Routine notifications are driven by routines and time blocks in the shared database (`routines`, `routine_time_blocks`, `push_device_tokens`), not by these enrollment endpoints.

## Suggested client routing

When the user taps a notification:

1. Read `session_type` and `source_id` from `message.data`.
2. Optionally use `title`, `body`, and `image_url` from `message.data` for in-app UI.
3. If `source_id` is empty, open the app's default home or practice screen.
4. Otherwise, navigate to the screen for that session type and entity ID:

| `session_type` | Suggested route        |
|----------------|------------------------|
| `PLAN`         | Plan detail / day view |
| `SERIES`       | Series player          |
| `CHAT`         | Chat room / DM thread using `room_id` (`chat_kind` + optional `group_id`) |
| `GROUP`        | Group screen using `group_id`; for `JOIN_REQUEST_CREATED` open the group's pending-requests view |
| `VERSE_OF_DAY` | App home / verse-of-day screen (no linked entity) |
