"""DiskStore: Bounded persistent artifact storage backed by diskcache."""

from pathlib import Path
from typing import Any

from diskcache import Cache

from invariant.cacheable import is_cacheable
from invariant.store.base import ArtifactStore
from invariant.store.codec import deserialize, serialize


class DiskStore(ArtifactStore):
    """Bounded persistent artifact store backed by diskcache.

    The cache is stored on the local filesystem under `.invariant/cache/`
    by default, with metadata managed by SQLite via diskcache. Values are the
    serialized artifact bytes, so the store codec contract is unchanged.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        size_limit_bytes: int = 2**30,
        eviction_policy: str = "least-frequently-used",
        cull_limit: int = 1,
    ) -> None:
        """Initialize DiskStore.

        Args:
            cache_dir: Directory to store cache. Defaults to `.invariant/cache/`
                in the current working directory.
            size_limit_bytes: Approximate maximum on-disk size of the cache.
            eviction_policy: diskcache eviction policy. Defaults to LFU.
            cull_limit: Maximum entries diskcache will evict per cull cycle.
        """
        if cache_dir is None:
            cache_dir = Path.cwd() / ".invariant" / "cache"
        elif isinstance(cache_dir, str):
            cache_dir = Path(cache_dir)

        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.size_limit_bytes = size_limit_bytes
        self.eviction_policy = eviction_policy
        self.cull_limit = cull_limit
        self._cache = Cache(
            directory=str(self.cache_dir),
            size_limit=size_limit_bytes,
            eviction_policy=eviction_policy,
            cull_limit=cull_limit,
        )
        super().__init__()

    def _make_key(self, op_name: str, digest: str) -> str:
        """Create a stable composite cache key from operation and digest.

        Args:
            op_name: The name of the operation.
            digest: The SHA-256 hash (64 character hex string).

        Returns:
            Composite key suitable for diskcache.
        """
        if len(digest) != 64:
            raise ValueError(f"Invalid digest length: {len(digest)}, expected 64")

        return f"{op_name}:{digest}"

    def exists(self, op_name: str, digest: str) -> bool:
        """Check if an artifact exists."""
        key = self._make_key(op_name, digest)
        exists = key in self._cache
        if exists:
            self.stats.hits += 1
        else:
            self.stats.misses += 1
        return exists

    def get(self, op_name: str, digest: str) -> Any:
        """Retrieve an artifact by operation name and digest.

        Raises:
            KeyError: If artifact does not exist.
        """
        key = self._make_key(op_name, digest)

        serialized = self._cache.get(key, default=None)
        if serialized is None:
            raise KeyError(
                f"Artifact with op_name '{op_name}' and digest '{digest}' not found"
            )

        return deserialize(serialized)

    def put(self, op_name: str, digest: str, artifact: Any) -> None:
        """Store an artifact with the given operation name and digest."""
        if not is_cacheable(artifact):
            raise TypeError(
                f"Artifact is not cacheable: {type(artifact)}. "
                f"Use is_cacheable() to check values before storing."
            )

        key = self._make_key(op_name, digest)
        serialized_data = serialize(artifact)
        self._cache.set(key, serialized_data)
        self.stats.puts += 1

    def close(self) -> None:
        """Close the underlying diskcache handle."""
        self._cache.close()

    def __del__(self) -> None:
        """Best-effort cleanup for callers that don't explicitly close the store."""
        try:
            self.close()
        except Exception:
            pass
