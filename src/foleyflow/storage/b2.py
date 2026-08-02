"""Backblaze B2 backend, via B2's S3-compatible API.

boto3 is imported lazily inside the constructor so that offline tests and the
media-core CLI never pay for it and never need credentials configured.

B2 specifics worth knowing:
  * The endpoint is regional — https://s3.<region>.backblazeb2.com — and the
    region string in the URL must match the `region_name` passed to boto3, or
    signing fails with an opaque 403.
  * Application keys can be scoped to a single bucket. Scope them; a key with
    account-wide access in a hackathon repo is a bad afternoon waiting.

## Why the error handling is broad

boto3 does not raise one exception type. `upload_file` catches `ClientError`
internally and re-raises `S3UploadFailedError`, which is NOT a ClientError
subclass, and connection failures raise `BotoCoreError` subclasses that are not
either. Catching only `ClientError` — as this module originally did — meant the
translation to StorageError never fired on the two failure modes that actually
happen in production, and raw boto3 exceptions (carrying bucket names and
endpoint URLs) escaped to callers written against the StorageError contract.
"""

import os
import sys
from typing import Optional

from foleyflow.storage.base import (
    ObjectHead,
    ObjectNotFound,
    PreconditionFailed,
    StorageError,
    validate_key,
)

_DEFAULT_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


def _guess_content_type(path: str) -> str:
    _, ext = os.path.splitext(path.lower())
    return _DEFAULT_CONTENT_TYPES.get(ext, "application/octet-stream")


def _boto_errors():
    """Every exception family boto3 can raise from an S3 call."""
    from boto3.exceptions import S3UploadFailedError
    from botocore.exceptions import BotoCoreError, ClientError
    return (ClientError, BotoCoreError, S3UploadFailedError)


def _status_of(exc) -> Optional[int]:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return None


class B2Backend:
    def __init__(self, endpoint_url: str, region: str, bucket: str,
                 key_id: str, application_key: str, prefix: str = ""):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:  # pragma: no cover - dependency is declared
            raise StorageError(f"B2Backend needs boto3: {e}") from e

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        # Cleared the first time the endpoint answers 501 to a conditional
        # write, so the unsupported round trip is attempted once per process
        # rather than on every immutable put. See put_if_absent.
        self._conditional_put = True
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=key_id,
            aws_secret_access_key=application_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def _key(self, key: str) -> str:
        """Apply the bucket prefix, validating the key first.

        S3 does not normalise keys, so `../` is not a traversal here the way it
        is on a filesystem — but an unvalidated id reaching this point still
        creates objects outside the intended prefix, and the only reason it was
        previously safe was that LocalBackend happened to reject it first.
        """
        validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        """True only when B2 positively says the object is there.

        A 403 is NOT treated as absent. B2 returns 403 for an expired, rotated,
        or wrongly-scoped credential, and reporting that as "no such object"
        makes every guard in the system fail open: the immutability check stops
        firing and overwrites completed artifacts, the SFX cache re-bills all
        161 clips, and the timeline cache re-spends the daily Gemini budget.
        """
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except _boto_errors() as e:
            if _status_of(e) == 404 or getattr(e, "response", {}).get(
                    "Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError(f"could not determine whether {key!r} exists "
                               f"(status {_status_of(e)})") from e

    def head(self, key: str) -> ObjectHead:
        """Size of a stored object via one HeadObject call.

        sha256 is deliberately None. B2's S3-compatible API returns the ETag —
        an MD5 for single-part uploads — not a sha256, and objects uploaded
        without S3 checksum headers expose no sha256 at all. Reporting an MD5
        under the contract's sha256 field would be a lie; where the system has
        recorded a real hash (the source video, each stem), the dossier carries
        that value instead.
        """
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=self._key(key))
        except _boto_errors() as e:
            if _status_of(e) == 404 or getattr(e, "response", {}).get(
                    "Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                raise ObjectNotFound(f"no object at {key!r}") from e
            raise StorageError(f"head failed for {key!r} "
                               f"(status {_status_of(e)})") from e
        return ObjectHead(size_bytes=int(response.get("ContentLength") or 0))

    def get(self, key: str, dest_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        # Download to a sibling temp name and rename, so an interrupted transfer
        # cannot leave a truncated file that later code treats as complete.
        tmp = f"{dest_path}.part"
        try:
            self._client.download_file(self.bucket, self._key(key), tmp)
            os.replace(tmp, dest_path)
        except _boto_errors() as e:
            if os.path.exists(tmp):
                os.unlink(tmp)
            if _status_of(e) == 404:
                raise ObjectNotFound(f"no object at {key!r}") from e
            raise StorageError(f"download failed for {key!r} "
                               f"(status {_status_of(e)})") from e
        return dest_path

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(key))
            return response["Body"].read()
        except _boto_errors() as e:
            if _status_of(e) == 404:
                raise ObjectNotFound(f"no object at {key!r}") from e
            raise StorageError(f"read failed for {key!r} "
                               f"(status {_status_of(e)})") from e

    def put(self, key: str, src_path: str, content_type: Optional[str] = None) -> str:
        try:
            self._client.upload_file(
                src_path, self.bucket, self._key(key),
                ExtraArgs={"ContentType": content_type or _guess_content_type(src_path)},
            )
        except _boto_errors() as e:
            raise StorageError(f"upload failed for {key!r}") from e
        return key

    def put_bytes(self, key: str, data: bytes,
                  content_type: Optional[str] = None) -> str:
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=self._key(key), Body=data,
                ContentType=content_type or "application/octet-stream")
        except _boto_errors() as e:
            raise StorageError(f"upload failed for {key!r}") from e
        return key

    def put_if_absent(self, key: str, data: bytes,
                      content_type: Optional[str] = None) -> str:
        """Create only if absent, atomically where the service allows it.

        `If-None-Match: *` makes this one operation, so two writers racing to
        allocate the same version cannot both win. **Backblaze B2's S3 API does
        not implement it** — PutObject answers `501 NotImplemented` — so on B2
        the atomic path is unavailable no matter how much we would prefer it.

        The previous version propagated that 501 as a StorageError, on the
        principle that a backend rejecting the header should fail closed rather
        than silently overwrite. Against B2 that meant *every* immutable write
        failed: no run ever recorded a terminal state, no timeline version could
        be allocated, and a failed run was misfiled as `failed_after_completion`
        because `fail_run` reads a StorageError here as "already terminal". The
        offline suite could not see it — LocalBackend implements the condition
        correctly.

        So a 501 downgrades, once, to check-then-write, and the capability is
        remembered so the doomed round trip is not repeated. That is not atomic:
        two writers can both pass `exists` and the second overwrites the first.
        The deployment target is a single instance (docs/api.md §4), which is
        what makes the window acceptable — but it is a real weakening of §10's
        guarantee, and it is announced on stderr rather than left to be
        discovered.
        """
        if self._conditional_put:
            try:
                self._client.put_object(
                    Bucket=self.bucket, Key=self._key(key), Body=data,
                    ContentType=content_type or "application/octet-stream",
                    IfNoneMatch="*")
                return key
            except _boto_errors() as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code")
                if _status_of(e) == 412 or code in ("PreconditionFailed", "412"):
                    raise PreconditionFailed(
                        f"object already exists at {key!r}") from e
                if _status_of(e) != 501 and code != "NotImplemented":
                    raise StorageError(f"conditional upload failed for {key!r} "
                                       f"(status {_status_of(e)})") from e
                self._conditional_put = False
                print("[b2] WARNING: this endpoint rejects conditional writes "
                      "(501); immutable artifacts fall back to a non-atomic "
                      "check-then-write. Safe for a single instance only.",
                      file=sys.stderr, flush=True)

        # Fallback: not atomic, deliberately. See the docstring.
        if self.exists(key):
            raise PreconditionFailed(f"object already exists at {key!r}")
        self.put_bytes(key, data, content_type)
        return key

    def list_keys(self, prefix: str):
        """Paginated listing, returned as storage keys.

        The bucket prefix is stripped so callers see the same key space they
        pass to get()/put(), and never learn which bucket or root prefix the
        backend is using.
        """
        full_prefix = f"{self.prefix}/{prefix}" if self.prefix else prefix
        strip = len(self.prefix) + 1 if self.prefix else 0
        keys = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"][strip:])
        except _boto_errors() as e:
            raise StorageError(f"list failed for {prefix!r} "
                               f"(status {_status_of(e)})") from e
        return sorted(keys)

    def signed_url(self, key: str, expires_s: int = 3600) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._key(key)},
                ExpiresIn=expires_s,
            )
        except _boto_errors() as e:
            raise StorageError(f"presign failed for {key!r}") from e

    def signed_upload_url(self, key: str, content_type: str,
                          expires_s: int = 900) -> str:
        """Presigned PUT for direct client upload (SPEC.md §8.3).

        Content-Type is pinned into the signature, so a URL signed for a video
        cannot be reused to store something else.
        """
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": self._key(key),
                        "ContentType": content_type},
                ExpiresIn=expires_s,
            )
        except _boto_errors() as e:
            raise StorageError(f"presign failed for {key!r}") from e

    def __repr__(self) -> str:
        return f"B2Backend(bucket={self.bucket!r}, prefix={self.prefix!r})"
