#!/usr/bin/env python3
"""Emit the FastAPI OpenAPI schema for the UI build (unit 7.6).

The Svelte client's TypeScript types are generated from this schema, so a
change to a Pydantic response model the UI consumes becomes a type-check
failure at UI build time rather than a demo-time surprise. Writing the schema
from the app object — `create_app().openapi()` — keeps that build offline: no
server has to be running, and no network is touched.

The default backend is forced to local, matching the offline suite: schema
emission must not require B2 credentials to exist in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("ui/openapi.json"),
        help="where to write openapi.json (default: ui/openapi.json)",
    )
    args = parser.parse_args()

    # Schema content is independent of the storage backend, and `get_backend`
    # defaults to local when unset anyway — pin it so a developer's `.env`
    # (typically FOLEYFLOW_CACHE_BACKEND=b2) cannot make this step demand
    # credentials that CI and the Docker build do not have.
    os.environ["FOLEYFLOW_CACHE_BACKEND"] = "local"

    from foleyflow.api.app import create_app

    schema = create_app().openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(json.dumps(schema))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
