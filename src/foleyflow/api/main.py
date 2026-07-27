"""ASGI entrypoint.

    uv run uvicorn foleyflow.api.main:app --reload

Kept separate from `app.py` so importing the application factory never
constructs a storage backend or reaches for credentials — which is what lets
tests build an app against a LocalBackend.
"""

from foleyflow.api.app import create_app

app = create_app()
