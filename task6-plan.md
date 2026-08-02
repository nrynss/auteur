# Task 6 — Per-project budgets: implementation slices

Sliced so each unit lands on its own and is independently testable. Order is
hard: the API contract (U1) comes first and nothing is built against a spec
that has not changed. Commits follow the units.

Dependency chain:

```
U1 contract ──> U2 attribution ──> U4 read + gate ──> U5 dossier block
        └────> U3 definition ────────^                      │
                                              U6 CLI context ─┘ (needs U4 for the verb)
U7 finish (suite, docs, handoff DONE)
```

---

## U1 — API contract (`docs/api.md`, no code) — XS

Write the contract first, per "API-first":

- `POST /projects` body gains optional `budget_usd` (decimal > 0).
- New `GET /projects/{id}/budget`:
  `{"project_id", "budget_usd", "spent", "remaining", "estimate": true,
    "ledger_writable", "by_model": [{endpoint, spent}]}`.
- Budget gate on **analyze** and **regenerate** (routes with a known estimate):
  `remaining < estimate` → `422 precondition_failed` with
  `detail.remaining` / `detail.estimated_cost_usd` / `detail.budget_usd`.
  Free calls (estimate $0.00) are never refused. **Mix is not gated** — its
  cost is unknowable at the door (cache hits); overage is visible post-hoc.
- Dossier gains a compact `budget` block (same fields).

Acceptance: the spec is reviewable without code; nothing implements it yet.

---

## U2 — Ledger attribution (contextvar) — S

- `ledger.py`: a `ContextVar[str | None]`; `set_project_context(project_id)`
  context manager; `log_transaction` stamps `project_id` when set (field
  optional — old rows and unattributed `foleyflow render` rows stay valid).
- `ledger.py`: `project_spend(project_id) -> (total, by_endpoint)` summing
  `cost_estimate` on rows tagged with that project.
- `jobs.py`: `_execute` wraps `work(run)` in `set_project_context(run.project_id)`
  — analysis, mix, and repair runs all tag their rows with no call-site change.

Acceptance: rows carry `project_id`; `project_spend` isolates two projects;
a direct render writes untagged rows.

---

## U3 — Budget definition — S

- `providers.toml`: `[budgets] default = …`.
- `CreateProjectRequest.budget_usd` (optional, > 0); `Project.budget_usd`
  (default `None` — old records load fine); `create_project(..., budget_usd=…)`.
- `budget_usd` surfaced on `GET /projects/{id}` detail.

Acceptance: create with/without a budget; negative/zero rejected (400);
pre-existing projects report no configured budget.

---

## U4 — Budget read + gate — S

- `GET /projects/{id}/budget` composing `project_spend` + `budget_usd` +
  `ledger_writable` (fiction flag, same as `/quota`).
- `_budget_gate(store, project_id, estimate)` on the analyze and regenerate
  routes: `estimate > 0 and remaining < estimate` → 422 with the numbers.

Acceptance: two $0.30 projects report their own spent/remaining; a $0.50
budget refuses a second $0.30 call; a $0.00 estimate never refuses;
`ledger_writable: false` still returns a payload, marked fiction.

---

## U5 — Dossier budget block — XS

- `GET /projects/{id}/dossier` gains a `budget` object reusing the U4 read.

Acceptance: dossier shows configured/spent/remaining with `estimate: true`.

---

## U6 — CLI project context + `foleyflow budget` — S

- Current-project resolution, in precedence order: positional id > `--project`
  flag > `FOLEYFLOW_PROJECT` env > persisted default (file under
  `~/.foleyflow/project`, written by `foleyflow use <id>`).
- `project_arg` becomes optional (`nargs="?"`); project verbs resolve through
  one helper; a missing project anywhere → usage error naming how to set one.
- `foleyflow budget [project]` and `foleyflow use <id>` verbs.

Acceptance: `foleyflow use` then bare `foleyflow budget` reports the current
project; env and `--project` override; no default → clear usage error.

---

## U7 — Finish — XS

- Full suite green; `handoff.md` task 6 → DONE; SPEC §9.4/§10 touched where
  they describe the budget; test count refreshed; this plan deleted.
