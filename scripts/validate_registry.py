"""CI-only: validates registry/channels.json against registry/schema.json. Not part of the
`aac` package — invoked directly by .github/workflows/registry.yml, and by
tests/test_registry_schema.py against a deliberately-broken fixture."""

import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent


def validate(
    registry_path: Path, schema_path: Path = REPO_ROOT / "registry" / "schema.json"
) -> None:
    """Raises jsonschema.ValidationError on a shape problem, ValueError on a duplicate channel
    (JSON Schema can't express per-key uniqueness across array items)."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)

    ids = [entry["yt_channel_id"] for entry in data]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate yt_channel_id entries: {', '.join(duplicates)}")


if __name__ == "__main__":
    registry_path = REPO_ROOT / "registry" / "channels.json"
    try:
        validate(registry_path)
    except jsonschema.ValidationError as exc:
        print(f"{registry_path} is invalid: {exc.message} (at {list(exc.path)})", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"{registry_path} is invalid: {exc}", file=sys.stderr)
        sys.exit(1)

    entry_count = len(json.loads(registry_path.read_text(encoding="utf-8")))
    print(f"{registry_path} is valid ({entry_count} entries).")
