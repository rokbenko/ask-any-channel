## What does this change?

<!-- One or two sentences. -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] Commits are signed off (DCO — `git commit -s`; see
      [CONTRIBUTING.md](https://github.com/rokbenko/ask-any-channel/blob/main/CONTRIBUTING.md))
- [ ] No transcript content or dataset bundle files are included (nothing from `datasets/` or
      `data/raw/`)
- [ ] README/CONTRIBUTING updated if behavior changed

## Registry PR?

If this PR only adds/updates an entry in `registry/channels.json`, the checklist above mostly
doesn't apply — see the README's
[Add your favorite creator](https://github.com/rokbenko/ask-any-channel/blob/main/README.md#add-your-favorite-creator)
section instead. CI will validate your entry against `registry/schema.json` automatically.
