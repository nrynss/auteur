"""One shared download helper — used by scratch code today and sfx.py (T2)."""

from typing import Mapping, Optional

import httpx


def download(url: str, dest_path: str, timeout: float = 60.0,
             headers: Optional[Mapping[str, str]] = None) -> str:
    """Download url to dest_path (binary). Returns dest_path. Raises on HTTP error.

    `headers` exists for providers whose *asset* URL is as protected as their
    generate endpoint — the self-hosted SFX worker serves `/files/{name}` behind
    the same bearer token as `/generate`. It is the caller's job to decide
    whether a given URL should receive credentials; see
    `providers.local.LocalAdapter.download_headers`, which attaches them only to
    its own base URL so a token can never ride along to a vendor CDN.
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=dict(headers) if headers else None)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(resp.content)
    return dest_path
