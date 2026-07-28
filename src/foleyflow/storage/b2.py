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
from typing import Optional

from foleyflow.storage.base import (
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
        """Conditional create via `If-None-Match: *`.

        S3-compatible conditional writes make this a single atomic operation,
        so two writers racing to allocate the same version cannot both win.
        A backend that rejects the header fails closed — the error propagates
        rather than silently degrading to an unconditional overwrite.
        """
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=self._key(key), Body=data,
                ContentType=content_type or "application/octet-stream",
                IfNoneMatch="*")
        except _boto_errors() as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code")
            if _status_of(e) == 412 or code in ("PreconditionFailed", "412"):
                raise PreconditionFailed(f"object already exists at {key!r}") from e
            raise StorageError(f"conditional upload failed for {key!r} "
                               f"(status {_status_of(e)})") from e
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
