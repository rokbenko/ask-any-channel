# Contributing to AskAnyChannel

Thanks for wanting to contribute. This project is small and single-maintainer, so keeping
changes reviewable and well-tested matters more than volume.

## Dev setup

```bash
git clone https://github.com/rokbenko/ask-any-channel.git && cd ask-any-channel
cp .env.example .env   # fill in OPENAI_API_KEY
docker compose up -d postgres
uv sync --group dev --extra ui   # --extra ui only needed if you're touching apps/ui/
```

## Tests and lint

Run the full battery before opening a PR — it's exactly what CI runs
(`.github/workflows/ci.yml`):

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

For `apps/ui/` changes, also run the script headless
(`streamlit.testing.v1.AppTest.from_file("apps/ui/Home.py")`) and exercise the failure path,
not just the initial render — a green render pass alone has missed real bugs here before.

## Architecture rules

These are enforced (by tests or by review), not suggestions:

- **All logic lives in `core/`.** `cli/` and `apps/ui/` are thin clients — they assemble
  arguments/render widgets and call `core`, nothing else. `tests/test_ui_isolation.py`
  AST-parses `apps/ui/**/*.py` for `openai`/`anthropic`/`psycopg` imports (there should be
  none) and `core/**/*.py` for `streamlit` imports (also none) — a PR that fails either check
  needs the logic moved into `core/`, not the check relaxed.
- **Don't bypass the seams.** `core/store/base.py` (`VectorStore`), `core/providers/base.py`
  (`LLMProvider`), and `core/credentials.py` (`CredentialsProvider`) are the only places SQL,
  vendor SDK calls, and API keys are allowed to live, respectively.
- **Dataset bundles are the interchange format, never Postgres directly.** `aac dataset build`
  writes only to a local bundle; `aac dataset load` is the only thing that writes into
  Postgres. If you touch `core/dataset/`, check `DATASET_SCHEMA_VERSION` in
  `core/constants.py` — bump it if the bundle's on-disk shape changes.
- **SQL migrations are append-only.** Never edit an already-applied file under
  `core/db/migrations/` — add the next numbered one.
- **Type hints everywhere**, modern syntax (`str | None`, not `Optional[str]`) — `ruff`'s `UP`
  rule enforces this.
- **Diagnostics go in `core/doctor.py`.** If you add a new way for a self-hoster's setup to be
  broken, add a check function there (returns a `CheckResult`, never raises) and put it in the
  right `ROLE_CHECKS` subset — `aac doctor`, the worker/UI boot hooks, and the compose
  healthchecks all read that one table.

## Sign off your commits (DCO)

Every commit must be signed off:

```bash
git commit -s -m "fix(ingest): ..."
```

This adds a `Signed-off-by:` trailer asserting you have the right to submit the contribution
under this project's license (Apache-2.0) — the [Developer Certificate of
Origin](https://developercertificate.org/), not a CLA. If you forgot on an existing commit:
`git commit --amend -s`.

## Commit messages

- **Subject**: `<type>(<scope>): <summary>` — lowercase, imperative, ≤ 50 chars. Types:
  `feat`, `fix`, `refactor`, `chore`, `docs`, `style`, `test`, `perf`, `ci`, `build`, `revert`.
  Scopes: `core`, `cli`, `db`, `ingest`, `dataset`, `search`, `store`, `providers`, `worker`,
  `ui`, `tests`, `repo` (root tooling/workspace), `docs`, `deps`.
- **Body is mandatory for anything non-trivial**, hard-wrapped at ~72 columns, written like a
  reviewer's briefing:
  1. Open with 1–3 sentences of context: what state prompted this change.
  2. Then one cluster per concern — a short lead-in line (often ending with a colon) followed
     by bullets. Bullets name concrete files, functions, and behavior, and say *why* each
     change was needed or safe, not just what moved.
  3. Explicitly record what was **deliberately left untouched** whenever a reader might expect
     it to change ("X untouched — it describes Y, not the deliverable").
  4. Close with a `Verified:` line listing the exact checks run (`pytest`, `ruff check`,
     manual CLI invocation, etc.) and their outcomes.
- Skeleton:

  ```
  fix(ingest): dedupe rolling caption cues

  One short paragraph: the situation and why the change
  was needed.

  First cluster of changes:
  - concrete change, with file/function names and the
    reason it was needed or safe
  - what was deliberately left untouched and why

  Second cluster:
  - ...

  Verified: exact checks run and their results.
  ```

- No attribution/generation footers.
- Commit **only after a logical, reviewable unit of work** — don't commit per file edit.

## Opening a PR

Use the PR template's checklist (tests, lint, DCO sign-off, no transcript content). See the
README's [Add your favorite creator](README.md#add-your-favorite-creator) section if you're
contributing a registry entry rather than code — those PRs are metadata-only and follow a
different, much shorter path (no dev setup needed).
