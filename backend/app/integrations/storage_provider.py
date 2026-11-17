import os
import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError

_SAFE_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ObjectStorageProvider(ABC):
    """Abstract private vault storage interface using server-controlled UUID object keys."""

    @abstractmethod
    def put_object(self, object_key: str, data: bytes, *, content_type: str) -> str:
        """Persist raw bytes under `object_key` and return the canonical key."""

    @abstractmethod
    def get_object(self, object_key: str) -> bytes:
        """Retrieve raw bytes for `object_key`."""

    @abstractmethod
    def delete_object(self, object_key: str) -> None:
        """Best-effort object removal (used for rollback cleanup on DB insert failure)."""

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        """Check whether `object_key` exists in storage."""


def build_vault_object_key(
    organization_id: Optional[uuid.UUID],
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> str:
    org_segment = str(organization_id) if organization_id is not None else "unrouted"
    return f"tenants/{org_segment}/documents/{document_id}/v/{version_id}"


class LocalPrivateVaultAdapter(ObjectStorageProvider):
    """
    Local private filesystem vault stored outside any web-served static root.
    Strictly prevents path traversal or relative-path escape outside `root_dir`.
    """

    def __init__(self, root_dir: Optional[Path] = None) -> None:
        if root_dir is not None:
            resolved = Path(root_dir).resolve()
        else:
            settings = get_settings()
            configured = getattr(settings, "LOCAL_STORAGE_ROOT", None)
            if configured:
                resolved = Path(str(configured)).resolve()
            else:
                resolved = (Path(__file__).resolve().parents[2] / "vault_storage").resolve()
        self._root = resolved
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root_dir(self) -> Path:
        return self._root

    def _resolve_key(self, object_key: str) -> Path:
        if not object_key or "\x00" in object_key:
            raise ValidationError("Invalid storage object key.")
        normalized = object_key.replace("\\", "/").lstrip("/")
        if normalized != object_key:
            raise PermissionDeniedError("Invalid object key format.", error_code="INVALID_OBJECT_KEY")
        parts = normalized.split("/")
        if any(p in {"", ".", ".."} or not _SAFE_KEY_SEGMENT_RE.match(p) for p in parts):
            raise PermissionDeniedError("Path traversal or unsafe object key rejected.", error_code="INVALID_OBJECT_KEY")
        target = (self._root / Path(*parts)).resolve()
        if not target.is_relative_to(self._root) or target == self._root:
            raise PermissionDeniedError("Resolved storage path escapes vault root.", error_code="INVALID_OBJECT_KEY")
        return target

    def _fs_path(self, target: Path) -> Path:
        if os.name == "nt":
            s = str(target)
            if not s.startswith("\\\\?\\") and len(s) >= 240:
                return Path("\\\\?\\" + s)
        return target

    def put_object(self, object_key: str, data: bytes, *, content_type: str) -> str:
        target = self._resolve_key(object_key)
        fs_target = self._fs_path(target)
        fs_target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._fs_path(target.with_name(f".t.{uuid.uuid4().hex[:12]}"))
        try:
            tmp_path.write_bytes(data)
            os.replace(tmp_path, fs_target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return object_key

    def get_object(self, object_key: str) -> bytes:
        target = self._fs_path(self._resolve_key(object_key))
        if not target.is_file():
            raise NotFoundError("Stored document binary not found in vault.")
        return target.read_bytes()

    def delete_object(self, object_key: str) -> None:
        target = self._fs_path(self._resolve_key(object_key))
        target.unlink(missing_ok=True)

    def exists(self, object_key: str) -> bool:
        target = self._fs_path(self._resolve_key(object_key))
        return target.is_file()
