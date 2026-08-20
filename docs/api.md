# HTTP API

`apps/api` is a thin FastAPI shell over the same `core` chat engine the Streamlit UI uses —
same retrieval, same multi-source/voice scoping, same citations. It's the "embed the bot on
your own site" path: SSE-streamed answers, no login required by default.

Not started by the default `docker compose up`. Bring it up with:

```bash
docker compose --profile api up -d
```

Published on `127.0.0.1:8000` only, same loopback-only posture as Postgres/the UI — put a
reverse proxy in front if you want it reachable beyond localhost. Interactive docs (OpenAPI/
Swagger UI) are at <http://127.0.0.1:8000/docs> once it's running.

## Auth and CORS

| Variable | Effect |
| --- | --- |
| `API_TOKEN` | Unset (default): the API is open, matching this product's no-login selfhost posture. Set it and every `/chats`/`/ask` request needs `Authorization: Bearer <token>`. |
| `CORS_ORIGINS` | Comma-separated list of origins allowed to call the API from a browser (e.g. the site embedding it). Empty (default) allows none. |

Both live in `.env` next to every other setting — see the root `.env.example`.

## Endpoints

All under `/api/v1`. `{ref}` and entries in `sources`/`voice` accept a channel's `@handle`,
bare handle, full `UC…` channel id, or the channel's UUID.

| Method & path | Does |
| --- | --- |
| `GET /healthz` | Liveness only — `{"status": "ok"}`. |
| `GET /channels` | Every ingested channel: title, handle, video/chunk counts, suggested questions, persona state (`enabled`, `family_friendly`, `has_profile`, `disclosure`). |
| `GET /channels/{ref}` | One channel, same shape. 404 if `ref` doesn't resolve. |
| `POST /chats` | Body `{"sources": [ref, ...], "voice": ref \| null}`. Creates a chat with that knowledge scope and voice. 422 if `voice` isn't one of `sources` (or its persona is disabled); 404 if a ref doesn't resolve; 201 with `{"id", "sources", "voice", "disclosure"}` on success. |
| `GET /chats/{id}` | The chat's current scope/voice/disclosure. |
| `GET /chats/{id}/messages` | Full history: `[{"id", "role", "content", "citations", "created_at"}, ...]`. |
| `POST /chats/{id}/messages` | Body `{"question": "..."}`. Streams the answer over SSE (see below) and persists both turns, same as the UI. |
| `POST /ask` | Body `{"sources": [ref, ...], "voice": ref \| null, "question": "..."}`. Stateless one-shot: no chat is created, nothing is persisted except a usage record — the endpoint for embedding a single Q&A widget with no session state of your own. |

`POST /chats`, `POST /chats/{id}/messages`, and `POST /ask` all require the bearer token when
`API_TOKEN` is set. `GET /channels` and `GET /channels/{ref}` stay open either way — they
return only public channel metadata, and an embed needs them to render its source picker.

For a public embed you therefore have to leave `API_TOKEN` unset: a bearer token shipped to a
browser is visible in the page source, so it authenticates nobody. Gate abuse at the reverse
proxy instead — see the warning under "Example: embed it on a page".

## Streaming format (SSE)

Both `POST /chats/{id}/messages` and `POST /ask` respond `Content-Type: text/event-stream`:

- Zero or more `event: token` frames, `data: {"text": "<delta>"}` — one per streamed chunk of
  the answer, in order.
- Exactly one closing frame:
  - `event: done`, `data:` a JSON object: `message` (the full answer text), `citations` (each
    with `n`, `title`, `url`, `t_start_s`, `quote`, `channel_id`, `channel_title`,
    `channel_handle`), `usage` (`model`, `tokens_in`, `tokens_out`, `est_cost_usd`), `voice`
    (the resolved voice channel id, or `null` for Neutral), `disclosure` (the voice's
    disclosure string, or `null`), and `suggested_sources` (ingested-but-unselected channels
    worth adding, when the answer ended up citing nothing).
  - or `event: error`, `data: {"detail": "..."}` if the provider call failed mid-stream.

## Example: `curl` streaming `/ask` with two sources and a voice

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/ask \
  -H 'Content-Type: application/json' \
  -d '{
    "sources": ["@AlexHormozi", "@danmartell"],
    "voice": "@AlexHormozi",
    "question": "How should I price my offer?"
  }'
```

`-N` disables curl's output buffering so tokens print as they arrive. Add
`-H "Authorization: Bearer $API_TOKEN"` if you've set one.

## Example: embed it on a page

> **⚠️ Before you expose this beyond localhost.** Every `/ask` request spends **your** API key,
> and this API has no built-in rate limit, request quota, or spend cap. A public, unauthenticated
> `/ask` is an endpoint any visitor can run in a loop against your bill. Before you put it on a
> public page:
>
> - Put rate limiting in front of it — nginx `limit_req`, Caddy `rate_limit`, or Cloudflare.
>   Also set `client_max_body_size` (or the equivalent) there: this app applies no request body
>   size limit of its own.
> - Set a hard monthly spend limit in your OpenAI/Anthropic dashboard. That is the only ceiling
>   that cannot be bypassed by a bug in front of it.
> - Set `CORS_ORIGINS` to the exact origins you embed on. It is a browser-side control only — it
>   stops other *sites* using your endpoint, not `curl` — so it is a complement to the rate
>   limit, never a replacement.
>
> `usage_events` records the cost of every answer, so `SELECT SUM(est_cost_usd) FROM
> usage_events WHERE created_at > now() - interval '1 day'` is a quick way to watch actual spend.

A minimal SSE client using `fetch` + a `ReadableStream` reader — `EventSource` can't send a
POST body, so this parses the `event:`/`data:` frames by hand.

```html
<div id="answer"></div>
<script>
async function ask(question) {
  const el = document.getElementById("answer");
  el.textContent = "";

  const resp = await fetch("http://127.0.0.1:8000/api/v1/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sources: ["@AlexHormozi", "@danmartell"],
      voice: "@AlexHormozi",
      question,
    }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const event = eventLine.slice("event: ".length);
      const data = JSON.parse(dataLine.slice("data: ".length));

      if (event === "token") el.textContent += data.text;
      if (event === "done") console.log("citations:", data.citations);
      if (event === "error") el.textContent += `\n[error: ${data.detail}]`;
    }
  }
}

ask("How should I price my offer?");
</script>
```
