---
## Closing summary (TOP)

- **What happened:** Operators needed a Discord `/new_ticket` command (plus NL @mention) to create Redmine issues with a real project, open title/description, and a reply link.
- **What was done:** Implemented `/new_ticket` with project resolve via `list_projects`, `create_issue`, NL aliases, help/README; shipped as Ultron 2.0.26.
- **What was tested:** Pytest targeted suite (20 passed), bot import, secret scan, `/help`, live create [#7758] and unknown-project error — all PASS; Discord slash/NL UI left to operator after restart.
- **Why closed:** All required acceptance criteria passed with live Redmine evidence.
- **Closed at (UTC):** 2026-07-22 18:40
---
# Self-upgrade: Necesitamos un nuevo comando al que además se le pueda llamar por @mention NL (m

## Tracker
- **Redmine:** #7406 — https://redmine.amvara.de/issues/7406
- **Source:** Discord `/upgrade` (operator)

## Problem / goal

Necesitamos un nuevo comando al que además se le pueda llamar por @mention NL (muy bien hecho). El comando sería /new_ticket el cual crearía un nuevo ticket en redmine. El proyecto relativo al ticket debe de ser elegido y no puede ser inventado, debe de ser uno de los disponibles en nuestra instancia de redmine. También hay que elegir titulo, lo normal es que sea algo así: "[FOO] Bar". Este título es abierto, y se puede ponerlo que sea. También se recogerá la descripción del ticket. También abierto, puede ser lo que sea. No hay más requisitos, lo demás se deja default. En el mensaje de respuesta de Ultron tiene que haber un enlace al ticket.

Testealo tú mismo con un ticket de prueba.

Reincia a ultron al terminar

## High-level instructions for coder

- Implement the request above in the Ultron checkout (`ultron/`, `tests/`, `scripts/`, `docs/` as needed).
- Prefer a **minimal diff**; match existing Ultron style.
- English for Discord-facing strings; never commit secrets or `.env`.
- After implementation: append **Testing instructions**, rename this file to **UNTESTED-…**.
- Bump patch version in `pyproject.toml` and `ultron/__init__.py` together when shipping code changes.
- Do **not** restart Ultron yourself — the `/upgrade` orchestrator runs dump + systemd restart.

## Implementation notes (coder)

- Added **`/new_ticket`** `project` `title` `description` (whitelisted; no LLM).
- Project resolved via existing **`resolve_redmine_project`** against **`list_projects`** (will not invent projects).
- **`RedmineClient.create_issue`** POSTs `/issues.json` with subject + description; other fields Redmine defaults.
- NL @mention: **`new_ticket`** in `NL_ALLOWED_COMMANDS` (+ aliases `create_ticket` / `create_issue` / `new_issue`).
- Version **2.0.26**.
- Live smoke: created [#7757](https://redmine.amvara.de/issues/7757) in **10_AMVARA** (`amvara-general`).

## Testing instructions

- [x] `.venv/bin/pytest -q tests/test_new_ticket.py tests/test_nl_router.py` passes
- [x] Import check: `from ultron.bot import UltronBot`
- [x] No secrets in the diff
- [x] Optional live: create via API/`create_new_ticket` or Discord `/new_ticket` against a real project; reply must include an issue link
- [ ] Discord (after dump/restart + slash sync):
  1. `/new_ticket` with a real project identifier/name, title like `[ULTRON] test`, and a short description → reply with `[#N](url)`
  2. Same with a nonsense project name → clear “No Redmine project matching …” error
  3. With NL routing on: `@Ultron create a ticket in 10_AMVARA titled [ULTRON] nl test: short description here`
- [x] `/help` lists `/new_ticket`

## Test report

- **When:** 2026-07-22 18:39:57–18:40:21 UTC
- **Env:** branch `main`, `.venv` Python 3.13, ultron **2.0.26**; Redmine smoke OK
- **What was tested:** `tests/test_new_ticket.py` + `tests/test_nl_router.py`; import `UltronBot`; diff secret scan; `_HELP_TEXT` contains `/new_ticket`; live `create_new_ticket` (unknown project + real create in `10_AMVARA`)
- **Results:**
  - Pytest targeted suite — **PASS** (20 passed)
  - Import `UltronBot` — **PASS**
  - No secrets in product/test diff — **PASS** (only documented env var names in README)
  - Live create + link — **PASS** ([#7758](https://redmine.amvara.de/issues/7758); body includes `[#7758](…/issues/7758)`)
  - Live unknown project — **PASS** (`No Redmine project matching …`)
  - `/help` lists `/new_ticket` — **PASS**
  - Discord slash / NL @mention in-channel — **not exercised** (no Discord session); same `create_new_ticket` path used by slash handler; NL parse/aliases covered by unit tests
- **Overall:** **PASS**
- **Operator feedback:** `/new_ticket` creates issues against real projects only and returns a markdown issue link. Discord slash/NL UI still worth a quick smoke after dump/restart + slash sync; API and unit coverage already look solid. Test ticket [#7758](https://redmine.amvara.de/issues/7758) can be closed.
