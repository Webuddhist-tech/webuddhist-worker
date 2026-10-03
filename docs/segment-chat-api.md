# Segment AI Chat API

Ask questions about one segment of a text. The worker reads every related segment
(the root text, translations and commentaries) straight from the OpenPecha library,
gives them to the configured LLM as
numbered sources, and streams the answer back as Server-Sent Events. The answer cites
sources inline as `[1]`, `[2]`…, and the stream tells the client which source ids each
number refers to.

```
Browser ──POST /worker/segment-chat/stream──▶ web proxy (Vite / nginx)
                                              │  strips /worker
                                              ▼
                                   worker  POST /segment-chat/stream
                                              │ 1. GET /v2/segments/{id}            ┐
                                              │    GET /v2/segments/{id}/related     │ OpenPecha library
                                              │ 2. GET /v2/texts/{text_id}           ├ (OPENPECHA_LIBRARY_URL)
                                              │ 3. GET /v2/segments/{related}/content│ cached in Redis per segment
                                              │                                      ┘
                                              │ 4. stream completion from SEGMENT_CHAT_LLM_MODEL
                                              ▼
                              event: sources → event: delta … → event: done
```

---

## Endpoint

### `POST /segment-chat/stream`

The full path on the worker is `/api/v1/segment-chat/stream`. The web app calls it as
`/worker/segment-chat/stream` through its proxy (see [Calling it from the web app](#calling-it-from-the-web-app)).

**Auth:** none. Requests are rate limited per client IP (`SEGMENT_CHAT_RATE_LIMIT_PER_MINUTE`).

**Request body (JSON):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `segment_id` | string | Yes | Segment id (OpenPecha id, e.g. `39zZirIQIMP8OFRMMBKvK`). Letters, digits, `_` and `-` only. |
| `question` | string | Yes | The user's question, 1–2000 characters. |
| `history` | array | No | Earlier turns of this conversation, oldest first, at most 20: `[{ "role": "user" \| "assistant", "content": "..." }]`. Send the previous answers back so follow-up questions have context. |
| `language` | string | No | Language code to answer in (e.g. `en`, `bo`, `zh`). If omitted, the model answers in the language of the question. |

```json
{
  "segment_id": "39zZirIQIMP8OFRMMBKvK",
  "question": "What does the image of the autumn moon express here?",
  "history": [
    { "role": "user", "content": "Who is being praised in this verse?" },
    { "role": "assistant", "content": "The verse praises Tārā … [1][34]" }
  ],
  "language": "en"
}
```

**Response (200):** `Content-Type: text/event-stream`. Each event is

```
event: <name>
data: <one line of JSON>

```

Events arrive in this order:

1. **`sources`** — exactly once, before any answer text. The selected segment and every source given to the model.

   ```json
   {
     "segment": {
       "segment_id": "39zZirIQIMP8OFRMMBKvK",
       "content": "ཕྱག་འཚལ་སྟོན་ཀའི་ཟླ་བ་ཀུན་དུ། …",
       "text": { "text_id": "HyUbHGlzS9LsSrgiFQNYE", "title": "སྒྲོལ་མ་ཉེར་གཅིག་ལ་བསྟོད་པ།", "language": "bo" }
     },
     "sources": [
       {
         "ref": 1,
         "type": "translation",
         "segment_id": "v93J56NhTS1vORXrVII2V",
         "text_id": "1vN3osCzwUT1sD87cUt1U",
         "title": "The Clear Mirror: The Twenty-One Homages Together with Their Benefits",
         "language": "en",
         "source_link": null,
         "license": "public",
         "snippet": "…"
       },
       {
         "ref": 34,
         "type": "commentary",
         "segment_id": "7oUwLaCDChBZqEguY5IUw",
         "text_id": "0Mf7CNevYyHI8TTBh3RHW",
         "title": "བསྟན་དགའ་སྤྲུལ་སྐུའི་སྒྲོལ་མའི་བསྟོད་འགྲེལ།",
         "language": "bo",
         "source_link": null,
         "license": "public",
         "snippet": "…"
       }
     ]
   }
   ```

   | Field | Meaning |
   |-------|---------|
   | `ref` | The number the answer uses to cite this source, as `[ref]`. |
   | `type` | `root_text` (the text the selected segment translates or comments on), `translation`, or `commentary`. |
   | `segment_id` | The related segment the text came from. Open it with `/chapter?text_id={text_id}&segment_id={segment_id}`. |
   | `text_id` | The related text (root text, translation or commentary). |
   | `title`, `language`, `source_link`, `license` | Display details of that text. |
   | `snippet` | First ~280 characters of the source, for previews. The full text stays on the server. |

   `sources` is empty when the segment has no related texts. The model then
   says the sources don't cover the question.

2. **`delta`** — zero or more. A piece of the answer, in Markdown. Append `text` to what you have.

   ```json
   { "text": "The autumn moon stands for Tārā's radiant, cooling compassion [34]" }
   ```

3. **`done`** — once, when the answer is complete. `cited_refs` lists the source numbers that
   the answer actually cited, in first-use order. Use it to show a "Sources used" list.

   ```json
   { "cited_refs": [34, 1] }
   ```

   **or `error`** — instead of `done`, if the model fails part way. The text already
   streamed is incomplete.

   ```json
   { "message": "The answer could not be completed. Please try again." }
   ```

**Errors before the stream starts** (normal JSON responses, `{"detail": ...}`):

| Status | When |
|--------|------|
| 404 | The segment does not exist. |
| 422 | Invalid body: blank question, bad `segment_id` characters, too long, or too much history. |
| 429 | Rate limit reached for this client. Retry after a minute. |
| 502 | The OpenPecha library could not be reached for the segment or its related segments. |
| 503 | Segment chat is disabled (`SEGMENT_CHAT_ENABLED`) or no LLM is configured. |

Gathering sources takes a few seconds the first time a segment is asked about (each related
segment's content is fetched). They are then cached in Redis for
`SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS`, so follow-up questions on the same segment start
streaming almost immediately. Show a "gathering sources" state until the first event arrives.

---

## Examples

### curl

```sh
curl -N -X POST http://127.0.0.1:8001/api/v1/segment-chat/stream \
  -H "Content-Type: application/json" \
  -d '{"segment_id": "39zZirIQIMP8OFRMMBKvK", "question": "What does this verse mean?"}'
```

`-N` turns off curl's buffering so events print as they arrive.

### TypeScript (fetch + ReadableStream)

`EventSource` only supports GET, so read the POST response body directly:

```ts
const response = await fetch("/worker/segment-chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ segment_id, question, history, language }),
  signal: abortController.signal,
});
if (!response.ok) throw new Error((await response.json()).detail);

const reader = response.body!.getReader();
const decoder = new TextDecoder();
let buffer = "";
for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const events = buffer.split("\n\n");
  buffer = events.pop()!; // keep the incomplete tail
  for (const raw of events) {
    const name = raw.match(/^event: (.*)$/m)?.[1];
    const data = JSON.parse(raw.match(/^data: (.*)$/m)?.[1] ?? "{}");
    if (name === "sources") setSources(data.sources);
    else if (name === "delta") appendAnswer(data.text);
    else if (name === "done") setCitedRefs(data.cited_refs);
    else if (name === "error") showError(data.message);
  }
}
```

The web app's implementation is `src/services/worker/segmentChat.ts` in the WeBuddhist repo.

---

## Calling it from the web app

The browser never calls the worker host directly. Like `/api` (backend) and `/library`,
the web app has a `/worker` proxy that strips the prefix:

| Environment | Where | Config |
|-------------|-------|--------|
| `vite dev` / `vite preview` | `vite.config.ts` → `"/worker"` | `VITE_WORKER_URL` in `env/.env.<mode>` (default `http://127.0.0.1:8001`) |
| Docker / nginx | `nginx/pecha.conf.template` → `location /worker/` | `WORKER` build arg → `VITE_WORKER_URL` env |

`/worker/segment-chat/stream` → `${VITE_WORKER_URL}/segment-chat/stream`. The nginx block turns
off proxy buffering so events are not held back, and the worker also sends
`X-Accel-Buffering: no` for any other nginx in front of it.

---

## Configuration (worker)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SEGMENT_CHAT_ENABLED` | `true` | Turn the endpoint off without a deploy (returns 503). |
| `SEGMENT_CHAT_LLM_PROVIDER` | `gemini` | LLM provider. Only `gemini` is implemented. |
| `SEGMENT_CHAT_LLM_MODEL` | `gemini-2.5-flash` | Model id passed to the provider. |
| `SEGMENT_CHAT_LLM_API_KEY` | *(empty)* | API key. Falls back to `GEMINI_API_KEY` when empty. |
| `SEGMENT_CHAT_LLM_TEMPERATURE` | `0.3` | Sampling temperature. Low keeps answers close to the sources. |
| `SEGMENT_CHAT_LLM_MAX_OUTPUT_TOKENS` | `2048` | Maximum answer length. |
| `SEGMENT_CHAT_LLM_THINKING_BUDGET` | `0` | Gemini thinking tokens. `0` is off (fastest first token); `-1` keeps the model default. Thinking tokens count against the output limit. |
| `SEGMENT_CHAT_MAX_CONTEXT_CHARS` | `60000` | Total characters of sources sent to the model. Sources past the budget are left out (and not listed in `sources`). |
| `SEGMENT_CHAT_MAX_SOURCE_CHARS` | `8000` | Characters per source; longer ones are cut and marked as an excerpt in the prompt. |
| `SEGMENT_CHAT_RELATED_PAGE_SIZE` | `100` | Page size for the library's `/related` (library max 100). |
| `SEGMENT_CHAT_MAX_RELATED_PAGES` | `10` | Page limit, so a heavily linked segment cannot run unbounded. |
| `SEGMENT_CHAT_MAX_SOURCES` | `80` | Most related segments whose content is read (one library call each). |
| `OPENPECHA_LIBRARY_URL` | `https://library.webuddhist.com` | OpenPecha library API base URL. |
| `OPENPECHA_APP_NAME` | `webuddhist` | Sent as the `X-Application` header the library requires. |
| `OPENPECHA_API_KEY` | *(empty)* | Sent as `X-API-Key` when set. The read endpoints used here don't need it. |
| `OPENPECHA_TIMEOUT_SECONDS` | `15` | Read timeout per library call. Transport failures are retried up to 3 times. |
| `OPENPECHA_MAX_CONCURRENCY` | `16` | Library calls in flight at once while gathering one segment's sources. |
| `SEGMENT_CHAT_CONTEXT_CACHE_TTL_SECONDS` | `600` | How long a segment's gathered sources are cached in Redis. `0` disables the cache. |
| `SEGMENT_CHAT_CONTEXT_CACHE_KEY_PREFIX` | `worker:segment-chat:context:` | Redis key prefix for the cache. |
| `SEGMENT_CHAT_RATE_LIMIT_PER_MINUTE` | `20` | Questions per client IP per minute. `0` disables the limit. |
| `SEGMENT_CHAT_RATE_LIMIT_KEY_PREFIX` | `worker:segment-chat:rate:` | Redis key prefix for the rate limit. |

Also used: `CACHE_CONNECTION_STRING` (Redis). The main backend is not involved. Redis is optional for this feature: without it every request refetches sources
and the rate limit is not enforced.

---

## How sources are chosen

All of it comes from the OpenPecha library:

1. `GET /v2/segments/{id}` gives the selected segment's `text_id`; `GET /v2/segments/{id}/content`
   its text. A 404 here is the endpoint's 404.
2. `GET /v2/segments/{id}/related` is paged (`offset`/`limit`) until `has_more` is false. Each item
   carries the related segment's own `id` and its `text_id`. Items from the selected segment's own
   text are skipped.
3. `GET /v2/texts/{text_id}` for the selected text and every related text decides what each one is,
   the same way the web app's resources panel does:
   - `translation_of` set → `translation` (this includes translations of commentaries);
   - `commentary_of` set → `commentary`;
   - the text the selected one is a translation or commentary of → `root_text`;
   - anything else is left out.
4. `GET /v2/segments/{related_id}/content` for each kept segment (up to `SEGMENT_CHAT_MAX_SOURCES`),
   and `GET /v2/texts/{text_id}/editions?edition_type=critical` for each text's `source_link`.

Sources are numbered root text first, then translations, then commentaries, keeping the library's
order within each. Empty segments are dropped.
- HTML tags are stripped from content as a safety measure (v2 content is normally plain text).

## Code

| File | Role |
|------|------|
| `worker_api/segment_chat/segment_chat_views.py` | Route, error mapping, rate limit, SSE response |
| `worker_api/segment_chat/services/segment_context_service.py` | Classifies related texts, builds and caches the segment's sources |
| `worker_api/segment_chat/services/library_client.py` | OpenPecha library calls (segments, related, content, texts) with retries and a concurrency gate |
| `worker_api/segment_chat/services/chat_prompt.py` | System prompt and source formatting |
| `worker_api/segment_chat/services/llm_client.py` | Provider/model from config; streaming completion |
| `worker_api/segment_chat/services/segment_chat_service.py` | SSE events, citation extraction, rate limit |
