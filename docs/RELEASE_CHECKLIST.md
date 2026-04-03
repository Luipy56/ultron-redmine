# Release checklist

Use this list before tagging or publishing a release. It is the project’s explicit **definition of done** for a version bump.

## 1. Version and changelog

- [ ] Bump **`__version__`** in [`ultron/__init__.py`](../ultron/__init__.py).
- [ ] Bump **`version`** in [`pyproject.toml`](../pyproject.toml) to match (same semver).
- [ ] Update **release notes** (GitHub Releases, internal changelog, or tag annotation) with user-visible changes.

## 2. Automated tests

- [ ] Run **`pytest`** from the repository root (install dev deps: `pip install -e ".[dev]"`).

```bash
python -m pytest tests/ -q
```

## 3. Optional smoke checks

- [ ] If Redmine/LLM credentials are available in `.env`, run **[`scripts/smoke_check.py`](../scripts/smoke_check.py)** (no Discord required).

```bash
python scripts/smoke_check.py
```

- [ ] If the **wizard** extra is installed, run **`ultron wizard`** once and confirm the main menu loads (`pip install -e ".[wizard]"`).

## 4. Manual sanity (when changing Discord behavior)

- [ ] Start the bot against a **test** guild or token; confirm slash commands appear (guild sync if `DISCORD_GUILD_ID` is set).
- [ ] Smoke-test critical flows: **`/help`**, whitelist **`/token`** / **`/approve`**, and one Redmine command if applicable.

## 5. Git tag (optional)

- [ ] Create an annotated tag matching the version, e.g. `v0.1.13`.
- [ ] Push commits and tags to the remote.

## Success criteria

- Tests pass.
- Version numbers are consistent across `__init__.py` and `pyproject.toml`.
- Release notes describe what changed for operators and/or users.
