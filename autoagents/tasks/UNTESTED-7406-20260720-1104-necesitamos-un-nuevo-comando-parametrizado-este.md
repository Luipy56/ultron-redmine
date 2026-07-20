# Self-upgrade: Necesitamos un nuevo comando parametrizado, este es el mejor ejemplo de petición

## Tracker
- **Redmine:** #7406 — https://redmine.amvara.de/issues/7406
- **Source:** Discord `/upgrade` (operator)

## Problem / goal

Necesitamos un nuevo comando parametrizado, este es el mejor ejemplo de petición "" <Ultron> List 10 tickets by order of priority on the project 93_DIP-RE"". En la petición se solicita el top 10 tickets de un proyecto en concreto dentro de redime, el resultado debería ser devolver un mensaje con el listado de tickets. El comando se llamará. "/top_tickets" y tendrá 2 parámetros, "project" y "kind_filter". El parámetro project será abierto y se usará para filtrar el proyecto, Ultron tendrá que consolidar si el proyecto existe o cual es el que más se parece por si el usuario lo ha escrito mal. El parámetro kind será cerrado, (priority|newests|oldests) como default priority, de esta manera se mostrarán los tickets con la prioridad más alta, más nuevos, o más antiguos (tendrás que buscar como funciona esto y como se llaman las prioridades en nuestra instancia de redmine). Y un tercer parámetro, el número de tickets a mostrar en la lista, como default 10. Este comando debe de poder llamarse por lenguaje natural, pero al ser más curl que procesado, habrá que priorizar las herramientas que redmine ofrece para conseguir el listado una vez creado el curl. En resumen, nuevo comando listar tickets proyecto específico

## High-level instructions for coder

- Implement the request above in the Ultron checkout (`ultron/`, `tests/`, `scripts/`, `docs/` as needed).
- Prefer a **minimal diff**; match existing Ultron style.
- English for Discord-facing strings; never commit secrets or `.env`.
- After implementation: append **Testing instructions**, rename this file to **UNTESTED-…**.
- Bump patch version in `pyproject.toml` and `ultron/__init__.py` together when shipping code changes.
- Do **not** restart Ultron yourself — the `/upgrade` orchestrator runs dump + systemd restart.

## Implementation summary (coder)

- Added **`/top_tickets`** (`project`, `kind_filter` choices priority|newests|oldests, `limit` default 10 max 50).
- Resolves project via Redmine `GET /projects.json` (exact / substring / fuzzy on identifier **and** name — e.g. `93_DIP-RE` → `dip-re`).
- Lists **open** issues via `GET /issues.json` with `sort=priority:desc` | `created_on:desc` | `created_on:asc`.
- NL router + Amvara planner/prefilter wired; help + README updated.
- Version **2.0.22**.

## Testing instructions

- [ ] `.venv/bin/pytest -q` passes (includes `tests/test_top_tickets.py`)
- [ ] Import check: `from ultron.bot import UltronBot`
- [ ] Optional live Redmine: `.venv/bin/python -c` calling `markdown_top_tickets(..., project_query="93_DIP-RE", kind_filter="priority", limit=10)` returns Urgent-first open issues for `dip-re`
- [ ] Discord (after dump/restart + slash sync): `/top_tickets project:93_DIP-RE` (and `kind_filter` / `limit` variants); @mention e.g. “list top 10 tickets by priority on project 93_DIP-RE”
- [ ] No secrets in the diff
