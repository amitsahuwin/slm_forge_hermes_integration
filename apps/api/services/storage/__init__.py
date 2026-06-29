"""Phase D — pluggable artifact storage.

Two implementations:
  * :class:`apps.api.services.storage.local.LocalObjectStore` — the
    legacy filesystem-backed path. Default when
    ``SLM_FORGE_STORAGE=local``.
  * :class:`apps.api.services.storage.ozone.OzoneObjectStore` — the
    Apache Ozone S3-gateway path. Default when
    ``SLM_FORGE_STORAGE=s3``.

Use :func:`apps.api.services.storage.factory.get_object_store` to get
the right one for the request's :class:`Identity`. The factory wires
the 30-day disk-fallback decorator when configured.
"""

from apps.api.services.storage.base import (
    InvalidKey,
    ObjectMeta,
    ObjectNotFound,
    ObjectStore,
)

__all__ = ["InvalidKey", "ObjectMeta", "ObjectNotFound", "ObjectStore"]