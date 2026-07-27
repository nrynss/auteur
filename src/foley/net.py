"""One shared download helper — used by scratch code today and sfx.py (T2)."""

import httpx


def download(url: str, dest_path: str, timeout: float = 60.0) -> str:
    """Download url to dest_path (binary). Returns dest_path. Raises on HTTP error."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(resp.content)
    return dest_path
