"""Integration manifest for the read-only config screen (handoff.md task 20,
docs/api.md §3.17).

Declares each external integration this product can be wired to, the env vars
it needs, and the source class the deployment binds them from — Secret Manager
(`scripts/setup_secrets.sh` + `deploy.sh --set-secrets`) or plain env
(`env.public.yaml`). The screen reports PRESENCE only (var set / absent) and
the contract source — never a value. The service deploys
`--allow-unauthenticated` with no authentication anywhere, so a value-echoing
route would be a credential endpoint; rotation stays a Secret Manager
operation (task 16) until authentication exists.

`source` is the deployment contract, not a runtime provenance claim: the
container cannot tell where an env var came from once it is set. It says "this
product binds this var from Secret Manager" — which is exactly what a
correctly-deployed instance did — and the docs say so.

`unresolved` catches the classic misconfiguration of putting a literal Secret
Manager reference (`projects/{p}/secrets/{s}/versions/{n}`) in
`env.public.yaml`: Cloud Run then sets the var to the reference *string*
instead of resolving it. The value is matched by shape and reported by name —
never echoed.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

# (key, label, source, env var names, match rule)
# `match`: "all" = every named var must be set; "any" = at least one.
_INTEGRATIONS: List[Dict[str, object]] = [
    {
        "key": "b2",
        "label": "Backblaze B2 storage",
        "source": "secret-manager",
        "env": ["B2_APPLICATION_KEY", "B2_KEY_ID"],
        "match": "all",
    },
    {
        "key": "gemini",
        "label": "Gemini analysis (scene VLM)",
        "source": "secret-manager",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "match": "any",
    },
    {
        "key": "openai",
        "label": "OpenAI analysis (optional)",
        "source": "env",
        "env": ["OPENAI_API_KEY"],
        "match": "all",
    },
    {
        "key": "elevenlabs",
        "label": "ElevenLabs sound effects (paid tier)",
        "source": "secret-manager",
        "env": ["ELEVENLABS_API_KEY"],
        "match": "all",
    },
    {
        "key": "fal",
        "label": "fal.ai gateway (fallback / retired routes)",
        "source": "env",
        "env": ["FAL_KEY"],
        "match": "all",
    },
    {
        "key": "sfx_worker",
        "label": "Self-hosted SFX worker",
        "source": "env",
        "env": ["FOLEYFLOW_SFX_WORKER_URL"],
        "match": "all",
    },
    {
        "key": "sfx_worker_token",
        "label": "SFX worker bearer token",
        "source": "secret-manager",
        "env": ["FOLEYFLOW_SFX_WORKER_TOKEN"],
        "match": "all",
    },
]

# The shape Cloud Run substitutes when a --set-secrets env var is *not*
# resolved — i.e. someone put the reference itself in env.public.yaml.
# Version may be numeric or the literal `latest` (both are valid references).
_SECRET_REFERENCE_RE = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/(?:\d+|latest)$")


def _is_unresolved_reference(value: str) -> bool:
    return bool(value and _SECRET_REFERENCE_RE.match(value))


def integration_status(environ: "dict | None" = None) -> List[dict]:
    """Live presence for each declared integration.

    Returns one dict per manifest entry: key, label, source, configured
    (boolean), env (the variable names), missing (names that are absent), and
    unresolved (names whose value is a literal Secret Manager reference).
    No value is ever included — only names and booleans.
    """
    env = os.environ if environ is None else environ
    rows: List[dict] = []
    for spec in _INTEGRATIONS:
        names = list(spec["env"])  # type: ignore[arg-type]
        present = {
            n: (env.get(n) or "").strip() for n in names
        }
        missing = [n for n, v in present.items() if not v]
        unresolved = [
            n for n, v in present.items()
            if v and _is_unresolved_reference(v)
        ]
        if spec["match"] == "any":
            configured = any(v for v in present.values())
        else:
            configured = all(v for v in present.values())
        rows.append({
            "key": spec["key"],
            "label": spec["label"],
            "source": spec["source"],
            "configured": configured,
            "env": names,
            "missing": missing,
            "unresolved": unresolved,
        })
    return rows
