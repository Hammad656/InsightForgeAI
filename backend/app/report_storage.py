"""Private report storage. Keys are derived entirely from authorized IDs."""
import os
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class StorageError(OSError):
    pass


class ReportStorage(Protocol):
    def put(self, org: UUID, report: UUID, content: bytes) -> None: ...
    def get(self, org: UUID, report: UUID) -> bytes: ...
    def delete(self, org: UUID, report: UUID) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, org: UUID, report: UUID) -> Path:
        path = self.root / org.hex / f"{report.hex}.pdf"
        if path.resolve() != path:
            raise StorageError("Invalid local report artifact")
        return path

    def put(self, org: UUID, report: UUID, content: bytes) -> None:
        path = self.path(org, report)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temp_path = Path(temporary.name)
            try:
                temporary.write(content)
            except OSError:
                temporary.close()
                temp_path.unlink(missing_ok=True)
                raise
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def get(self, org: UUID, report: UUID) -> bytes:
        with self.path(org, report).open("rb") as artifact:
            return artifact.read(MAX_ARTIFACT_BYTES + 1)

    def delete(self, org: UUID, report: UUID) -> None:
        self.path(org, report).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, bucket: str, endpoint: str | None) -> None:
        if not bucket or (endpoint is not None and not endpoint.startswith("https://")):
            raise StorageError("S3 report storage requires a private bucket and HTTPS")
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, config=Config(
            connect_timeout=3, read_timeout=8, retries={"total_max_attempts": 2},
        ))

    def key(self, org: UUID, report: UUID) -> dict[str, str]:
        return {"Bucket": self.bucket, "Key": f"reports/{org.hex}/{report.hex}.pdf"}

    def put(self, org: UUID, report: UUID, content: bytes) -> None:
        try:
            self.client.put_object(**self.key(org, report), Body=content, ContentType="application/pdf")
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Report storage write failed") from exc

    def get(self, org: UUID, report: UUID) -> bytes:
        try:
            with self.client.get_object(**self.key(org, report))["Body"] as stream:
                return bytes(stream.read(MAX_ARTIFACT_BYTES + 1))
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Report storage read failed") from exc

    def delete(self, org: UUID, report: UUID) -> None:
        try:
            self.client.delete_object(**self.key(org, report))
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Report storage cleanup failed") from exc


@lru_cache
def get_storage() -> ReportStorage:
    settings = get_settings()
    if settings.report_storage == "s3":
        try:
            return S3Storage(settings.report_s3_bucket, settings.report_s3_endpoint)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Report storage is unavailable") from exc
    return LocalStorage(settings.report_directory)
