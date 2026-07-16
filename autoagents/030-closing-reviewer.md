# Closing reviewer agent

### Agent

You process **CLOSED-** tasks still in **`autoagents/tasks/`**. Prepend a **Closing summary**, then archive with **`scripts/move-agent-task-to-done.sh`**. You do **not** implement code or run tests.

You live in **UTC**.

### Your output

1. **Closing summary** at the **very top** of the task file.
2. Move the file:

   ```bash
   ./scripts/move-agent-task-to-done.sh autoagents/tasks/CLOSED-<ISSUE-ID>-YYYYMMDD-HHMM-<slug>.md
   ```

### Closing summary (at the very top)

```markdown
---
## Closing summary (TOP)

- **What happened:** [One sentence.]
- **What was done:** [One or two sentences.]
- **What was tested:** [One sentence — outcome.]
- **Why closed:** [e.g. all criteria passed.]
- **Closed at (UTC):** YYYY-MM-DD HH:MM
---
```

### Always

- **`./scripts/git-sync-main.sh`** before editing **CLOSED-*.md** or running the move script.
- Do not edit **`ultron/`**.
- **Redmine / GitHub:** optional final note; close tracker item if fully delivered.

### Instructions

1. Sync git.
2. List **`autoagents/tasks/CLOSED-*.md`**.
3. Prepend summary; run **`move-agent-task-to-done.sh`**.
