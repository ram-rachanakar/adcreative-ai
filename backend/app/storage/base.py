"""Storage abstraction. Local filesystem fallback + S3/MinIO adapter. Keys are always prefixed by org id.
Access is via short-lived HMAC-signed URLs (local) or presigned URLs (S3)."""
import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def signed_url(self, key: str, ttl: int = 900) -> str: ...


def _sign(key: str, exp: int) -> str:
    return hmac.new(get_settings().secret_key.encode(), f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()


def verify_signature(key: str, exp: int, sig: str) -> bool:
    return exp > time.time() and hmac.compare_digest(_sign(key, exp), sig)


class LocalStorage(Storage):
    def __init__(self, root: str = "./storage_data", public_base: str = "http://localhost:8000"):
        self.root, self.public_base = Path(root).resolve(), public_base
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # path traversal guard
            raise ValueError("Invalid storage key")
        return p

    def put(self, key, data, content_type):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key):
        return self._path(key).read_bytes()

    def signed_url(self, key, ttl=900):
        exp = int(time.time()) + ttl
        return f"{self.public_base}/files/{key}?exp={exp}&sig={_sign(key, exp)}"


class S3Storage(Storage):  # NOTE: written but not exercised in this environment (MinIO image unavailable)
    def __init__(self):
        import boto3
        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint, aws_access_key_id=s.s3_access_key,
                                   aws_secret_access_key=s.s3_secret_key)

    def put(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def signed_url(self, key, ttl=900):
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl)


def get_storage() -> Storage:
    s = get_settings()
    return S3Storage() if s.storage_backend == "s3" else LocalStorage(public_base=s.public_base_url)
