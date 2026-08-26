"""Storage helpers with pluggable backends for run artifacts."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Optional


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


def _create_backend(config: StorageConfig) -> StorageBackend:
    if config.backend == "local":
        return LocalStorageBackend(config)
    if config.backend == "s3":
        return S3StorageBackend(config)
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
