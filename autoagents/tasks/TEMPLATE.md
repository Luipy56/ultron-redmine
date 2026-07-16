# FEAT-task file

## Tracker
- **Redmine:** #ISSUE_ID — https://redmine.example/issues/ISSUE_ID
- **GitHub:** (optional) https://github.com/Luipy56/ultron-redmine/issues/N

## Meta
- **Status:** `ready-for-dev`
- **Generated:** TIMESTAMP
- **Assigned agent:** feature-coder / coder

---

## 1. Issue summary
[Brief description]

## 2. Acceptance criteria
- [ ] Criterion 1
- [ ] Tests pass (`pytest`)
- [ ] No secrets in commits or task notes

## 3. Implementation scope
**IN SCOPE:** `ultron/`, `tests/`, `scripts/`, `docs/` when needed

**OUT OF SCOPE:** `.env`, host systemd, unrelated repos

## 4. Technical notes
- Python **3.11+**, package in **`pyproject.toml`**
- Run tests: `.venv/bin/pytest -q`
- Smoke: `python scripts/smoke_check.py`

## 5. Testing instructions
(To be filled by coder before UNTESTED rename)
