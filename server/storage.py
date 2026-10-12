"""Storage helpers with pluggable backends for run artifacts."""

from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Dict, Iterable, Optional

from .storage_lock import run_storage_lock


def _resolve_data_root(path: Optional[str]) -> Path:
    base = Path(path) if path else Path.cwd() / "data"
    return base.expanduser().resolve()


@dataclass(frozen=True)
class StorageConfig:
    """Configuration for the storage subsystem."""

    backend: str
    data_root: Path
    bucket: Optional[str] = None
    bucket_prefix: str = "runs/"
    kms_key_id: Optional[str] = None
    azure_account_url: Optional[str] = None
    azure_container: Optional[str] = None
    azure_connection_string: Optional[str] = None
    azure_prefix: str = "runs/"

    @classmethod
    def from_env(cls) -> "StorageConfig":
        backend = os.getenv("SABLE_STORAGE_BACKEND", "local").lower()
        data_root = _resolve_data_root(os.getenv("SABLE_DATA_ROOT"))
        bucket = os.getenv("SABLE_STORAGE_S3_BUCKET")
        prefix = os.getenv("SABLE_STORAGE_S3_PREFIX", "runs/")
        kms_key_id = os.getenv("SABLE_STORAGE_S3_KMS_KEY_ID")
        return cls(
            backend=backend,
            data_root=data_root,
            bucket=bucket or None,
            bucket_prefix=prefix,
            kms_key_id=kms_key_id or None,
            azure_account_url=os.getenv("AZURE_STORAGE_ACCOUNT_URL") or None,
            azure_container=os.getenv("AZURE_STORAGE_CONTAINER") or None,
            azure_connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING") or None,
            azure_prefix=os.getenv("SABLE_STORAGE_AZURE_PREFIX", "runs/"),
        )


@dataclass(frozen=True)
class RunStoragePaths:
    """Container for common run directory locations."""

    base: Path
    inputs: Path
    logs: Path
    checkpoints: Path
    results: Path
    artifacts: Path

    def as_dict(self, stringify: bool = True) -> Dict[str, Path | str]:
        data: Dict[str, Path | str] = {
            "base": self.base,
            "inputs": self.inputs,
            "logs": self.logs,
            "checkpoints": self.checkpoints,
            "results": self.results,
            "artifacts": self.artifacts,
        }
        if stringify:
            return {key: str(value) for key, value in data.items()}
        return data


class StorageBackend(ABC):
    """Abstract storage backend contract."""

    def __init__(self, config: StorageConfig):
        self._config = config

    @property
    def config(self) -> StorageConfig:
        return self._config

    @abstractmethod
    def ensure_run_dirs(self, run_id: str) -> RunStoragePaths:
        """Ensure run directories exist and return their locations."""

    @abstractmethod
    def run_dir(self, run_id: str) -> Path:
        """Return the base path for a run."""

    @abstractmethod
    def checkpoint_path(self, run_id: str, filename: str, *, ensure_exists: bool = True) -> Path:
        """Resolve a checkpoint path for download or inspection."""

    @abstractmethod
    def list_checkpoints(self, run_id: str) -> Iterable[str]:
        """List checkpoint filenames for a run."""

    def results_json_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "results" / "results.json"

    def summary_txt_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "results" / "summary.txt"

    def generate_download_url(self, run_id: str, category: str, filename: str, *, expires_in: int = 300) -> Optional[str]:
        """Generate a signed download URL when supported by the backend."""
        return None

    def sync_run(self, run_id: str) -> None:
        """Persist the current local state of a run when required."""

    def delete_run(self, run_id: str) -> None:
        """Delete a run from storage."""
        base = self.run_dir(run_id)
        if base.exists():
            import shutil

            shutil.rmtree(base)


class LocalStorageBackend(StorageBackend):
    """Local filesystem backend for development and testing."""

    def __init__(self, config: StorageConfig):
        super().__init__(config)
        self._root = config.data_root
        # Make sure the base directories exist eagerly to surface permission issues early.
        (self._root / "runs").mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return (self._root / "runs" / run_id).resolve()

    def ensure_run_dirs(self, run_id: str) -> RunStoragePaths:
        base = self.run_dir(run_id)
        inputs = base / "inputs"
        logs = base / "logs"
        checkpoints = base / "checkpoints"
        results = base / "results"
        artifacts = base / "artifacts"

        for path in (inputs, logs, checkpoints, results, artifacts):
            path.mkdir(parents=True, exist_ok=True)

        return RunStoragePaths(
            base=base,
            inputs=inputs,
            logs=logs,
            checkpoints=checkpoints,
            results=results,
            artifacts=artifacts,
        )

    def checkpoint_path(self, run_id: str, filename: str, *, ensure_exists: bool = True) -> Path:
        run_base = self.run_dir(run_id)
        
        # check standard checkpoints first
        base = (run_base / "checkpoints").resolve()
        target = (base / filename).resolve()
        
        # If not found in checkpoints, check provider-specific Boltz artifacts.
        if not (target.exists() and target.is_file()):
            for directory_name in ("boltz_cifs", "boltz_platform"):
                boltz_dir = (run_base / "artifacts" / directory_name).resolve()
                boltz_target = (boltz_dir / filename).resolve()
                if boltz_target.exists() and boltz_target.is_file():
                    target = boltz_target
                    base = boltz_dir
                    break

        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError("Invalid checkpoint path") from exc

        if ensure_exists and (not target.exists() or not target.is_file()):
            raise FileNotFoundError(filename)

        return target

    def list_checkpoints(self, run_id: str) -> Iterable[str]:
        checkpoints = []
        
        # Standard checkpoints
        base = self.run_dir(run_id) / "checkpoints"
        if base.exists():
            checkpoints.extend(sorted(p.name for p in base.iterdir() if p.is_file()))
            
        # Boltz structures from self-hosted and Platform providers.
        for directory_name in ("boltz_cifs", "boltz_platform"):
            boltz_dir = self.run_dir(run_id) / "artifacts" / directory_name
            if boltz_dir.exists():
                checkpoints.extend(sorted(
                    p.name for p in boltz_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {'.cif', '.pdb'}
                ))
            
        return checkpoints


class AzureBlobStorageBackend(LocalStorageBackend):
    """Azure Blob storage with per-run coordination for a shared working cache.

    Downloads publish complete files without overwriting local writes. Completion
    records and recoverable staging live outside run artifacts; the next operation
    on a run cleans up any staging left by a terminated process. Completion records
    are valid only while their run directory and recorded files still exist.
    """

    def __init__(self, config: StorageConfig, container_client=None):
        if not config.azure_container:
            raise ValueError("Azure container must be configured via AZURE_STORAGE_CONTAINER")
        super().__init__(config)
        self._prefix = config.azure_prefix.strip("/")
        self._container_client = container_client or self._create_container_client(config)
        self._cache_source = sha256(json.dumps([
            config.azure_account_url, config.azure_connection_string,
            config.azure_container, self._prefix,
        ]).encode("utf-8")).hexdigest()

    @staticmethod
    def _create_container_client(config: StorageConfig):
        from azure.storage.blob import BlobServiceClient

        if config.azure_connection_string:
            service = BlobServiceClient.from_connection_string(config.azure_connection_string)
        elif config.azure_account_url:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(
                account_url=config.azure_account_url,
                credential=DefaultAzureCredential(),
            )
        else:
            raise ValueError(
                "Configure AZURE_STORAGE_ACCOUNT_URL for managed identity or "
                "AZURE_STORAGE_CONNECTION_STRING"
            )
        return service.get_container_client(config.azure_container)

    def _blob_prefix(self, run_id: str) -> str:
        parts = [part for part in (self._prefix, run_id) if part]
        return "/".join(parts) + "/"

    @staticmethod
    def _is_directory_blob(blob) -> bool:
        metadata = getattr(blob, "metadata", None) or {}
        return str(metadata.get("hdi_isfolder", "")).lower() == "true"

    def _list_run_blobs(self, prefix: str):
        return self._container_client.list_blobs(
            name_starts_with=prefix,
            include=["metadata"],
        )

    def _cache_identity(self, base: Path):
        if not base.exists():
            return None
        stat = base.stat()
        return [self._cache_source, stat.st_dev, stat.st_ino]

    @staticmethod
    def _clear_staging(state: Path) -> None:
        staging = state / "staging"
        if staging.exists():
            shutil.rmtree(staging)

    def _mark_hydrated(self, base: Path, state: Path, files: Iterable[str]) -> None:
        identity = self._cache_identity(base)
        if identity is not None:
            staging = state / "staging"
            staging.mkdir(exist_ok=True)
            marker = staging / "hydrated"
            marker.write_text(json.dumps({"identity": identity, "files": sorted(files)}),
                              encoding="utf-8")
            os.replace(marker, state / "hydrated")

    def _hydrate_run(self, run_id: str) -> None:
        base = super().run_dir(run_id)
        with run_storage_lock(base) as state:
            # A dead downloader leaves only private staging files, never artifacts.
            self._clear_staging(state)
            marker = state / "hydrated"
            try:
                cached = json.loads(marker.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                cached = None
            if (isinstance(cached, dict)
                    and cached.get("identity") is not None
                    and cached["identity"] == self._cache_identity(base)
                    and isinstance(cached.get("files"), list)
                    and all(isinstance(name, str) and (base / name).is_file()
                            for name in cached["files"])):
                return
            marker.unlink(missing_ok=True)

            staging = state / "staging"
            staging.mkdir()
            try:
                prefix = self._blob_prefix(run_id)
                files: list[str] = []
                for blob in self._list_run_blobs(prefix):
                    if self._is_directory_blob(blob):
                        continue
                    relative_name = blob.name[len(prefix):]
                    if not relative_name:
                        continue
                    target = (base / relative_name).resolve()
                    try:
                        files.append(target.relative_to(base).as_posix())
                    except ValueError as exc:
                        raise ValueError(f"Invalid blob path: {blob.name}") from exc
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    download = staging / "download"
                    with download.open("wb") as data:
                        self._container_client.download_blob(blob.name).readinto(data)
                        data.flush()
                        os.fsync(data.fileno())
                    try:
                        # link is atomic and cannot replace a concurrent worker write.
                        os.link(download, target)
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        raise OSError(
                            f"Cannot atomically publish {target}; the artifact cache requires "
                            f"same-filesystem hard-link support: {exc}"
                        ) from exc
                    download.unlink()

                self._mark_hydrated(base, state, files)
            finally:
                self._clear_staging(state)

    def run_dir(self, run_id: str) -> Path:
        self._hydrate_run(run_id)
        return super().run_dir(run_id)

    def sync_run(self, run_id: str) -> None:
        base = super().run_dir(run_id)
        with run_storage_lock(base) as state:
            self._clear_staging(state)
            (state / "hydrated").unlink(missing_ok=True)
            try:
                prefix = self._blob_prefix(run_id)
                local_blobs: set[str] = set()

                if base.exists():
                    for path in base.rglob("*"):
                        if not path.is_file():
                            continue
                        blob_name = prefix + path.relative_to(base).as_posix()
                        local_blobs.add(blob_name)
                        with path.open("rb") as data:
                            self._container_client.upload_blob(blob_name, data, overwrite=True)

                remote_blobs = {
                    blob.name
                    for blob in self._list_run_blobs(prefix)
                    if not self._is_directory_blob(blob)
                }
                for blob_name in remote_blobs - local_blobs:
                    self._container_client.delete_blob(blob_name)

                self._mark_hydrated(base, state,
                                    (name[len(prefix):] for name in local_blobs))
            finally:
                self._clear_staging(state)

    def delete_run(self, run_id: str) -> None:
        base = super().run_dir(run_id)
        with run_storage_lock(base) as state:
            self._clear_staging(state)
            (state / "hydrated").unlink(missing_ok=True)
            if base.exists():
                shutil.rmtree(base)
            prefix = self._blob_prefix(run_id)
            blobs = sorted(
                self._list_run_blobs(prefix),
                key=lambda blob: blob.name.count("/"),
                reverse=True,
            )
            for blob in blobs:
                self._container_client.delete_blob(blob.name)


class S3StorageBackend(StorageBackend):
    """Placeholder for a future S3-backed implementation."""

    def __init__(self, config: StorageConfig):
        super().__init__(config)
        if not config.bucket:
            raise ValueError("S3 bucket must be configured via SABLE_STORAGE_S3_BUCKET")
        # A boto3 client would be initialised here lazily when first needed.

    # The following methods intentionally raise NotImplementedError to
    # highlight the work required when enabling the backend.
    def run_dir(self, run_id: str) -> Path:  # type: ignore[override]
        raise NotImplementedError("S3 backend does not expose local run directories")

    def ensure_run_dirs(self, run_id: str) -> RunStoragePaths:  # type: ignore[override]
        raise NotImplementedError("S3 backend setup is not yet implemented")

    def checkpoint_path(self, run_id: str, filename: str, *, ensure_exists: bool = True) -> Path:  # type: ignore[override]
        raise NotImplementedError("S3 checkpoint resolution requires signed URL support")

    def list_checkpoints(self, run_id: str) -> Iterable[str]:  # type: ignore[override]
        raise NotImplementedError("S3 checkpoint listing is not yet implemented")

    def generate_download_url(self, run_id: str, category: str, filename: str, *, expires_in: int = 300) -> Optional[str]:
        # Future implementation will return a presigned URL with limited lifetime and scope.
        raise NotImplementedError("Presigned download URLs are not yet implemented")


class StorageService:
    """High-level facade over the configured storage backend."""

    def __init__(self, backend: StorageBackend):
        self._backend = backend

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    def ensure_run_dirs(self, run_id: str) -> RunStoragePaths:
        return self._backend.ensure_run_dirs(run_id)

    def run_dir(self, run_id: str) -> Path:
        return self._backend.run_dir(run_id)

    def results_json_path(self, run_id: str) -> Path:
        return self._backend.results_json_path(run_id)

    def summary_txt_path(self, run_id: str) -> Path:
        return self._backend.summary_txt_path(run_id)

    def checkpoint_path(self, run_id: str, filename: str, *, ensure_exists: bool = True) -> Path:
        return self._backend.checkpoint_path(run_id, filename, ensure_exists=ensure_exists)

    def list_checkpoints(self, run_id: str) -> Iterable[str]:
        return self._backend.list_checkpoints(run_id)

    def generate_download_url(self, run_id: str, category: str, filename: str, *, expires_in: int = 300) -> Optional[str]:
        return self._backend.generate_download_url(run_id, category, filename, expires_in=expires_in)

    def sync_run(self, run_id: str) -> None:
        self._backend.sync_run(run_id)

    def delete_run(self, run_id: str) -> None:
        self._backend.delete_run(run_id)


def _create_backend(config: StorageConfig) -> StorageBackend:
    if config.backend == "local":
        return LocalStorageBackend(config)
    if config.backend == "s3":
        return S3StorageBackend(config)
    if config.backend in {"azure", "azure_blob"}:
        return AzureBlobStorageBackend(config)
    raise ValueError(f"Unsupported storage backend: {config.backend}")


@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    config = StorageConfig.from_env()
    backend = _create_backend(config)
    return StorageService(backend)


def reset_storage_service_cache() -> None:
    """Clear cached storage service; useful for tests when env changes."""

    get_storage_service.cache_clear()


# Backwards-compatible helpers -------------------------------------------------


DATA_ROOT = get_storage_service().backend.config.data_root


def run_dir(run_id: str) -> Path:
    return get_storage_service().run_dir(run_id)


def ensure_run_dirs(run_id: str) -> Dict[str, str]:
    paths = get_storage_service().ensure_run_dirs(run_id)
    return paths.as_dict()


def results_json_path(run_id: str) -> Path:
    return get_storage_service().results_json_path(run_id)


def summary_txt_path(run_id: str) -> Path:
    return get_storage_service().summary_txt_path(run_id)


def checkpoint_path(run_id: str, filename: str, *, ensure_exists: bool = True) -> Path:
    return get_storage_service().checkpoint_path(run_id, filename, ensure_exists=ensure_exists)


def list_run_checkpoints(run_id: str) -> list[str]:
    return list(get_storage_service().list_checkpoints(run_id))


def sync_run(run_id: str) -> None:
    get_storage_service().sync_run(run_id)


def delete_run_data(run_id: str) -> None:
    get_storage_service().delete_run(run_id)
