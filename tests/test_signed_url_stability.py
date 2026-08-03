"""Pin: repeated signed_url calls return one URL so the browser can cache."""
import time
from foleyflow.storage.b2 import B2Backend


class _FakeClient:
    def __init__(self): self.calls = 0
    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.calls += 1
        return f"https://b2/{Params['Key']}?X-Amz-Date=call{self.calls}"


def _backend():
    b = B2Backend.__new__(B2Backend)
    b.bucket, b.prefix = "bkt", ""
    b._conditional_put = True
    b._signed_url_cache = {}
    b._client = _FakeClient()
    return b


def test_repeated_calls_return_the_same_url():
    b = _backend()
    a = b.signed_url("input/source.mp4")
    c = b.signed_url("input/source.mp4")
    assert a == c, "signature changed between calls; browser cache can never hit"
    assert b._client.calls == 1


def test_a_new_window_resigns():
    b = _backend()
    first = b.signed_url("input/source.mp4", expires_s=120)
    key = next(iter(b._signed_url_cache))
    # Age the entry into a previous window.
    b._signed_url_cache = {(key[0], key[1], key[2] - 1): first}
    second = b.signed_url("input/source.mp4", expires_s=120)
    assert second != first
    assert all(k[2] == int(time.time()) // 60 for k in b._signed_url_cache), \
        "stale windows must be evicted"


def test_distinct_keys_do_not_share():
    b = _backend()
    assert b.signed_url("a.mp4") != b.signed_url("b.mp4")
