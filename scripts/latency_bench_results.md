# Latency bench — phase-2 before/after

Harness: `uv run python scripts/latency_bench.py`

Config: `--delay-ms 50`, ~60 events on primary project, 200 ledger rows, 3 projects.
Simulated B2 RTT via `DelayedBackend` wrapping `LocalBackend` (no credentials).

Raw JSON: `scripts/latency_bench_baseline.json`, `scripts/latency_bench_after.json`.

## Before (post–7.18 wave 1, pre–phase-2)

| path | cold ms | warm ms | cold ops (summary) |
|---|---:|---:|---|
| GET /projects/{id} fold | 304.5 | 304.0 | get_bytes=63 head=2 list_keys=1 |
| GET /quota | 555.8 | 0.2 | get_bytes=200 list_keys=1 put_bytes=1 |
| GET /budget | 253.5 | 253.6 | get_bytes=61 list_keys=2 |
| GET /qa | 100.3 | 100.3 | get_bytes=2 |
| dossier heads (sample) | 304.2 | 304.0 | get_bytes=61 head=4 list_keys=1 |

## After (phase-2: aggregates + event checkpoint + shared pool)

| path | cold ms | warm ms | warm ops (summary) | warm speedup |
|---|---:|---:|---|---:|
| GET /projects/{id} fold | 303.8 | 151.0 | get_bytes=3 head=2 list_keys=1 | **2.0×** |
| GET /quota | 555.8 | 0.1 | (aggregates / row cache) | ~same warm |
| GET /budget | 101.0 | 100.8 | get_bytes=1 list_keys=1 | **2.5×** |
| GET /qa | 100.4 | 100.4 | get_bytes=2 | unchanged |
| dossier heads (sample) | 151.3 | 151.3 | get_bytes=1 head=4 list_keys=1 | **2.0×** |

Notes:

- Cold `/quota` still pays one full ledger multi-get to seed aggregates; warm is near-constant.
- Cold `/budget` after a prior `/quota` in the same process benefits from the row-cache seed + event checkpoint from the project fold; pure cold spend seed is still one scan.
- Event checkpoint always re-lists keys (one RTT) so cross-process appends are visible; same-process appends bump an epoch and clear the cache immediately.
- Multi-instance: aggregates and event cache are process-local (same class of limitation as the existing ledger row cache). No Redis.
