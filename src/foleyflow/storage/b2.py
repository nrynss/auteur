"""Backblaze B2 backend, via B2's S3-compatible API.

boto3 is imported lazily inside the constructor so that offline tests and the
media-core CLI never pay for it and never need credentials configured.

B2 specifics worth knowing:
  * The endpoint is regional — https://s3.<region>.backblazeb2.com — and the
    region string in the URL must match the `region_name` passed to boto3, or
    signing fails with an opaque 403.
  * Application keys can be scoped to a single bucket. Scope them; a key with
    account-wide access in a hackathon repo is a bad afternoon waiting.
"""

import os
from typing import Optional

from foleyflow.storage.base import StorageError

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
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as e:
            # 404 (absent) and 403 (absent, on a bucket-scoped key without
            # ListBucket) both mean "not there" for our purposes.
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (403, 404):
                return False
            raise StorageError(f"head_object failed for {key!r}: {e}") from e

    def get(self, key: str, dest_path: str) -> str:
        from botocore.exceptions import ClientError
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        try:
            self._client.download_file(self.bucket, self._key(key), dest_path)
        except ClientError as e:
            raise StorageError(f"download failed for {key!r}: {e}") from e
        return dest_path

    def put(self, key: str, src_path: str, content_type: Optional[str] = None) -> str:
        from botocore.exceptions import ClientError
        try:
            self._client.upload_file(
                src_path, self.bucket, self._key(key),
                ExtraArgs={"ContentType": content_type or _guess_content_type(src_path)},
            )
        except ClientError as e:
            raise StorageError(f"upload failed for {key!r}: {e}") from e
        return key

    def list_keys(self, prefix: str):
        """Paginated listing, returned as prefix-relative-free storage keys.

        The bucket prefix is stripped so callers see the same key space they
        pass to get()/put(), and never learn which bucket or root prefix the
        backend is using.
        """
        from botocore.exceptions import ClientError
        full_prefix = self._key(prefix)
        strip = len(self.prefix) + 1 if self.prefix else 0
        keys = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"][strip:])
        except ClientError as e:
            raise StorageError(f"list failed for {prefix!r}: {e}") from e
        return sorted(keys)

    def signed_url(self, key: str, expires_s: int = 3600) -> str:
        from botocore.exceptions import ClientError
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._key(key)},
                ExpiresIn=expires_s,
            )
        except ClientError as e:
            raise StorageError(f"presign failed for {key!r}: {e}") from e

    def __repr__(self) -> str:
        return f"B2Backend(bucket={self.bucket!r}, prefix={self.prefix!r})"
