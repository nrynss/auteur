# Cleanup: decouple the media core from fal.ai

**Status:** ready to execute
**Owner:** unassigned agent
**Prerequisite reading:** [SPEC.md](SPEC.md) §9 (cost-aware routing), §12 (provider strategy)

---

## 1. Goal

Remove every hardcoded reference to fal.ai — endpoint IDs, argument shapes,
duration limits, prices, and the `fal_client` import — from `src/foley` and
`src/foleyflow`. Providers and models become declarative configuration in a
TOML file. Talking to a specific vendor becomes one small adapter class behind
one interface.

When this is done, adding a provider is editing TOML plus writing one adapter.
Today it is edits in four modules across three packages.

## 2. Why this is worth doing now

1. **Genblaze has no fal connector.** We have to write one. The adapter this
   cleanup introduces is the same shape as Genblaze's `SyncProvider.generate(step)
   -> step`, so the work lands twice: it decouples the core *and* becomes the
   connector.
2. **Local models are coming.** MOSS-SoundEffect and Stable Audio Open run on
   local hardware. There is no sane way to add a non-HTTP provider while
   `execute_with_ledger` is a thin wrapper over `fal_client`.
3. **SPEC.md §12 requires listing exact provider and model IDs in the README,**
   and §9 requires showing users the cost estimate before a billable
   regeneration. Both are much easier to keep honest when the IDs and prices
   live in one declared file rather than scattered across dicts in four modules.

## 3. Current coupling — complete inventory

Everything below is fal-specific and must move.

| Location | What's hardcoded |
|---|---|
| [ledger.py:6](src/foley/ledger.py:6) | `import fal_client` at module scope |
| [ledger.py:95-107](src/foley/ledger.py:95) | `execute_with_ledger` calls `fal_client.subscribe/run/submit` directly |
| [ledger.py:11-24](src/foley/ledger.py:11) | `COST_ESTIMATES` — 12 fal endpoint IDs and prices |
| [ledger.py:35-41](src/foley/ledger.py:35) | `estimate_cost` sniffs endpoint substrings (`"ltx-2.3" in endpoint`, `"kling" in endpoint`) for duration-based pricing |
| [sfx.py:43-56](src/foley/sfx.py:43) | `_ARG_BUILDERS` — per-endpoint argument shapes as lambdas |
| [sfx.py:84-94](src/foley/sfx.py:84) | `extract_audio_url` assumes fal's response envelope |
| [routing.py:38-51](src/foley/routing.py:38) | `DURATION_LIMITS` — per-endpoint min/max |
| [analyze.py:16-18](src/foley/analyze.py:16) | `DEFAULT_MODEL`, and prose assuming fal's OpenRouter gateway |
| [analyze.py:25-29](src/foley/analyze.py:25) | `fal_client.upload_file` for CDN upload |
| [analyze.py:124,233,307](src/foley/analyze.py:233) | literal `openrouter/router/video`, `openrouter/router/vision` |
| [analyze.py:265](src/foley/analyze.py:265) | literal `fal-ai/marlin/find` |
| [direct.py:70](src/foley/direct.py:70) | literal `openrouter/router/vision` |
| [routing.yaml](routing.yaml) | endpoints already in config — the one thing already right |

## 4. Non-negotiable constraints

Read this section twice. Violating any of these silently breaks something
expensive or invisible.

### 4.1 Cache keys must not change

`cache_key(endpoint, prompt, duration_s)` hashes the **endpoint string**. It is
computed identically in two places:

- [foley/sfx.py:69](src/foley/sfx.py:69)
- [foleyflow/sfx_cache.py:41](src/foleyflow/sfx_cache.py:41)

There are ~161 cached WAVs addressed by these hashes, representing real money
already spent. **If a model's identifier changes, every one of them is
orphaned and silently regenerates at full cost.**

Therefore: in the new TOML, a model's key **is** its current endpoint string —
`"cassetteai/sound-effects-generator"`, `"fal-ai/elevenlabs/sound-effects/v2"`,
`"fal-ai/mmaudio-v2/text-to-audio"`. Do not "clean these up" into friendly
names like `cassette-sfx`. If you want friendly names, add an `alias` field
that resolves *to* the canonical ID; the ID stays in the hash.

`tests/test_sfx_cache.py::test_empty_variant_key_is_identical_to_the_media_core`
guards this. It must keep asserting the literal historical strings. Do not
parametrize it from the TOML file — a test that reads the same config as the
code under test cannot detect a config change.

### 4.2 Tests stay fully offline

The whole suite runs with no network and no API key: 128 passed, 1 skipped.
Any test that could make a billable call is a defect. Adapters must be
injectable so tests substitute fakes.

### 4.3 The ledger stays readable

`summarize_ledger` parses existing `ledger.jsonl` rows (370 of them upstream).
Keep the `cost_estimate` field name even though it now sometimes holds an
actual cost — [ledger.py:47-51](src/foley/ledger.py:47) explains why. New
fields are fine; renaming or removing existing ones is not.

### 4.4 Do not touch mix math

`assemble.py` is calibrated by ear. The `GAINS` table, `DISCRETE_BIAS_S`,
`CONTINUOUS_DUCK_DB`, `LOOP_JITTER_DB`, the seeded RNG, `trim_onset` — all of
it stays exactly as is. This cleanup does not enter that file. Same for
`mux.py` and the degradation ladder tiers.

### 4.5 Config declares *what to send*; adapters know *how to talk*

Argument field names and types are data. Response parsing, auth, retry, and
upload are code. Do not try to make `extract_audio_url` configurable — a
JSONPath-in-TOML scheme is the obvious trap here and it will be rejected in
review. Each adapter parses its own vendor's envelope in Python.

## 5. Target design

### 5.1 Module layout

```
src/foleyflow/providers/
  __init__.py       # get_adapter(), get_model_spec(), registry loading
  base.py           # Adapter protocol, ModelSpec, GenerationResult
  config.py         # TOML load + validation, one lru_cache'd read
  fal.py            # FalAdapter — the only module importing fal_client
  local.py          # stub for MOSS/Stable Audio Open; raises NotImplementedError
providers.toml      # repo root, next to pyproject.toml
```

`src/foley` keeps its modules but calls into the registry instead of holding
vendor knowledge. It must not import `fal_client` anywhere when this is done —
`grep -rn "fal_client" src/` should match only `src/foleyflow/providers/fal.py`.

### 5.2 The adapter interface

```python
class Adapter(Protocol):
    name: str

    def generate(self, spec: ModelSpec, arguments: dict) -> GenerationResult:
        """Call the provider and return a normalized result.

        GenerationResult carries: asset_url (or local_path), raw_response,
        and actual_cost_usd when the vendor reports one.
        """

    def upload(self, local_path: str) -> str:
        """Make a local file reachable by the provider. HTTP providers upload
        to a CDN; local adapters return a file:// URL."""
```

Deliberately shaped like Genblaze's `SyncProvider.generate(step) -> Step` so
the fal adapter can be lifted into a `genblaze_fal` connector with thin
translation rather than a rewrite.

### 5.3 `providers.toml`

Full proposed schema. Values below are the current live ones — carry them
across verbatim, including the `# unverified` caveats which are real and
should survive as TOML comments.

```toml
[meta]
version = 1

# ---------------------------------------------------------------- adapters --
[adapters.fal]
module = "foleyflow.providers.fal:FalAdapter"
api_key_env = "FAL_KEY"
method = "subscribe"          # fal_client.subscribe | run | submit

[adapters.local]
module = "foleyflow.providers.local:LocalAdapter"
enabled = false

# ------------------------------------------------------------------ models --
# NOTE: the table key is the canonical model ID and is part of the SFX cache
# key. Renaming one orphans every cached clip generated under the old name.
# See cleanup.md §4.1 before editing any key here.

[models."cassetteai/sound-effects-generator"]
adapter = "fal"
role = "sfx"
cost_per_call_usd = 0.02
# docs: integer seconds, required; prose says up to 30s.
# unverified: lower bound (guessed 1s, integer >= 1).
duration_min_s = 1.0
duration_max_s = 30.0
prompt_field = "prompt"
duration_field = "duration"
duration_type = "int"

[models."fal-ai/elevenlabs/sound-effects/v2"]
adapter = "fal"
role = "sfx"
cost_per_call_usd = 0.05
# docs: "Duration in seconds (0.5-22)" — explicit range in the Input Schema.
duration_min_s = 0.5
duration_max_s = 22.0
prompt_field = "text"
duration_field = "duration_seconds"
duration_type = "float"

[models."fal-ai/mmaudio-v2/text-to-audio"]
adapter = "fal"
role = "sfx"
cost_per_call_usd = 0.03
# docs: optional float, default 8; no min/max documented.
# unverified: conservative guess matching the other two endpoints.
duration_min_s = 1.0
duration_max_s = 30.0
prompt_field = "prompt"
duration_field = "duration"
duration_type = "float"

# Static extra arguments, if a model ever needs them:
# [models."some/model".extra_arguments]
# output_format = "wav"

# --------------------------------------------------------------- analysis --
[analysis]
video_model = "openrouter/router/video"
vision_model = "openrouter/router/vision"
localization_model = "fal-ai/marlin/find"
default_vlm = "google/gemini-2.5-flash"

[models."openrouter/router/video"]
adapter = "fal"
role = "analysis"
cost_per_call_usd = 0.01

[models."openrouter/router/vision"]
adapter = "fal"
role = "analysis"
cost_per_call_usd = 0.005

[models."fal-ai/marlin/find"]
adapter = "fal"
role = "analysis"
cost_per_call_usd = 0.01

# ---------------------------------------------------------------- defaults --
[defaults]
cost_per_call_usd = 0.01      # was ledger.py's bare `return 0.01`
duration_min_s = 1.0
duration_max_s = 22.0
```

`prompt_field` / `duration_field` / `duration_type` replace the `_ARG_BUILDERS`
lambdas and cover all three existing shapes exactly. Verify this against
[sfx.py:43-56](src/foley/sfx.py:43) before deleting anything.

### 5.4 Duration-based pricing

`estimate_cost` currently special-cases LTX and Kling by substring match. Those
are video-generation models used only by the retired hero-clip scripts, not by
FoleyFlow. Replace the substring sniffing with an explicit optional field:

```toml
[models."fal-ai/kling-video/v3/pro/text-to-video"]
cost_per_second_usd = 0.168
```

When `cost_per_second_usd` is present, cost is `duration * rate`; otherwise
`cost_per_call_usd`. If nothing references these video models after the
refactor, drop them from the TOML entirely and note it in the PR.

### 5.5 Reading TOML

Use `tomllib` from the standard library (Python 3.11+, and this project
requires 3.14). **Do not add a TOML dependency.** Nothing writes config, so
read-only is sufficient.

## 6. Should `routing.yaml` become TOML too?

**Recommended: yes, merge it into `providers.toml` as a `[routing]` section.**

Routes reference models by ID, so keeping them in the same file lets validation
catch a route pointing at an undeclared model — a class of bug that is
currently only discoverable by triggering the route at runtime. It also drops
the `pyyaml` dependency.

Preserve exactly:
- **ordered** categories — first keyword match wins, order is significant, and
  TOML array-of-tables `[[routing.categories]]` preserves it
- the prefix-match-at-word-boundary semantics of `keywords.has_word`
- the comment at the top of [routing.yaml](routing.yaml) explaining why
  `match` was deliberately excluded from `impact_strike` — that comment
  documents a real bug that was hit and fixed

If merging turns out to fight TOML's structure, keeping `routing.yaml` as-is is
acceptable. Raise it rather than half-doing it.

Note: [routing.py:115](src/foley/routing.py:115) does
`next(c for c in routes["categories"] if c["name"] == "ambience")`, which raises
an uncaught `StopIteration` if no category is named `ambience`. Config
validation should catch this at load time with a clear message. Fix it while
you're here.

## 7. Work plan

Land these as separate commits. Run the suite after each — it should stay green
throughout, not just at the end.

### Stage 1 — config layer, nothing wired
Add `providers.toml`, `providers/config.py`, `providers/base.py`. Load,
validate, and expose `get_model_spec(model_id)`. Validation errors name the
offending key and fail loud. Tests: valid load, unknown model, route referencing
an undeclared model, missing required field, duration bounds inverted.

**Exit:** config loads and validates; no production code calls it yet.

### Stage 2 — fal adapter
`providers/fal.py` with `FalAdapter`. Move `fal_client` calls and
`extract_audio_url` into it. `execute_with_ledger` takes an adapter parameter
defaulting to the configured one, and keeps its ledger-writing behavior
unchanged.

**Exit:** `grep -rn "fal_client" src/` matches only `providers/fal.py`.

### Stage 3 — routing and argument shapes
`DURATION_LIMITS` and `_ARG_BUILDERS` read from config. Delete the dicts.

**Exit:** `test_routing.py` and `test_sfx.py` pass unchanged except for
imports. If a routing assertion needs its *expected value* edited, stop —
that means behavior changed and the config is wrong.

### Stage 4 — cost model
`COST_ESTIMATES` and `estimate_cost` read from config. Remove substring
sniffing.

**Exit:** ledger rows are byte-identical in shape to existing ones;
`summarize_ledger` still parses the historical file.

### Stage 5 — analysis endpoints
`analyze.py` and `direct.py` take their model IDs from `[analysis]`. Move
`fal_client.upload_file` behind `Adapter.upload`.

**Exit:** no vendor string literals remain in `src/foley`.

### Stage 6 — local adapter stub
`providers/local.py` raising `NotImplementedError` with a message pointing at
the model shortlist. Proves the interface admits a non-HTTP provider without
committing to the inference work.

**Exit:** `[adapters.local] enabled = true` produces a clear error, not a crash.

## 8. Out of scope

- Implementing local inference (MOSS-SoundEffect, Stable Audio Open). Stage 6
  is a stub only.
- Writing the Genblaze connector. This cleanup makes it cheap; it does not do it.
- Any change to `assemble.py`, `mux.py`, or mix calibration.
- The B2 storage layer (`src/foleyflow/storage/`) — already vendor-neutral.
- Reconciling routing policy against SPEC.md §9.1, which currently disagrees
  about which provider gets hero discrete Foley. That is a product decision,
  not a refactor. Leave routing behavior byte-identical and flag the conflict.

## 9. Verification

```bash
uv run pytest -q
```

Required end state:

- 128 passed, 1 skipped, or better. No test deleted to make this true.
- `grep -rn "fal_client" src/` → only `src/foleyflow/providers/fal.py`
- `grep -rniE "fal-ai/|cassetteai/|openrouter/" src/` → no matches
  (all such strings live in `providers.toml` and in tests that deliberately
  assert historical cache-key inputs)
- Cache-key compatibility test passes **unmodified**
- A render from a saved timeline against a warm cache still costs $0 and
  produces a byte-identical mix

Last one is the real acceptance test. If you have a populated cache and a
sample clip:

```bash
uv run foley render <clip> --from-timeline samples/timelines/clip_1.json
```

Same bytes out, no new ledger rows. If either changes, something in the key
path moved.

## 10. Raise before proceeding

- Merging `routing.yaml` into `providers.toml` fights TOML's structure (§6)
- Any change that would alter a cache key (§4.1)
- The `prompt_field`/`duration_field`/`duration_type` shape failing to express
  an existing argument builder (§5.3)
- Discovering an argument builder or response shape not covered by this
  inventory (§3) — that means the coupling is deeper than surveyed, and the
  plan needs revisiting rather than patching
