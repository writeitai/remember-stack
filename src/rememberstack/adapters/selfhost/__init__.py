"""Self-host adapters: pg delivery shell, local-FS object store, local mounts (WP-0.4a)."""

from rememberstack.adapters.selfhost.forget import LocalFSForgetManifestStore
from rememberstack.adapters.selfhost.git import LocalGitRepository
from rememberstack.adapters.selfhost.minio import MinIOObjectStore
from rememberstack.adapters.selfhost.minio import MinIOSettings
from rememberstack.adapters.selfhost.mounts import AuditedRawReader
from rememberstack.adapters.selfhost.mounts import LocalMountPublisher
from rememberstack.adapters.selfhost.mounts import RawAccessDenied
from rememberstack.adapters.selfhost.mounts import storage_class_for
from rememberstack.adapters.selfhost.object_store import LocalFSObjectStore
from rememberstack.adapters.selfhost.object_store import ObjectAlreadyExistsError
from rememberstack.adapters.selfhost.object_store import ObjectKeyEscapesRootError
from rememberstack.adapters.selfhost.projection import SelfHostProjectionPurger
from rememberstack.adapters.selfhost.queue import SelfHostTaskQueue
from rememberstack.adapters.selfhost.queue import SelfHostWorkerLoop
from rememberstack.adapters.selfhost.queue import TokenBucket
from rememberstack.adapters.selfhost.telemetry import FanoutTelemetry
from rememberstack.adapters.selfhost.telemetry import JsonLineTelemetry
from rememberstack.adapters.selfhost.watcher import LocalDirectoryWatcher

__all__ = (
    "FanoutTelemetry",
    "LocalFSForgetManifestStore",
    "LocalGitRepository",
    "LocalDirectoryWatcher",
    "JsonLineTelemetry",
    "LocalFSObjectStore",
    "AuditedRawReader",
    "LocalMountPublisher",
    "MinIOObjectStore",
    "MinIOSettings",
    "RawAccessDenied",
    "storage_class_for",
    "ObjectAlreadyExistsError",
    "ObjectKeyEscapesRootError",
    "SelfHostTaskQueue",
    "SelfHostProjectionPurger",
    "SelfHostWorkerLoop",
    "TokenBucket",
)
