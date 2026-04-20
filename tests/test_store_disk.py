"""Tests for DiskStore."""

import base64
import os
import threading
import shutil
from decimal import Decimal

import pytest

from invariant.hashing import hash_value
from invariant.store.disk import DiskStore


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "test_cache"
    yield cache_dir
    # Cleanup
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


class TestDiskStore:
    """Tests for DiskStore."""

    @staticmethod
    def _payload(size: int) -> str:
        """Build a mostly incompressible string payload of roughly the given size."""
        return base64.b85encode(os.urandom(size)).decode("ascii")

    def test_creation_default(self):
        """Test DiskStore creation with default directory."""
        store = DiskStore()
        assert store.cache_dir.exists()
        assert store.cache_dir.name == "cache"
        assert store.size_limit_bytes == 2**30
        assert store.eviction_policy == "least-frequently-used"
        assert store.cull_limit == 1
        store.close()

    def test_creation_custom_dir(self, temp_cache_dir):
        """Test DiskStore creation with custom directory."""
        store = DiskStore(temp_cache_dir)
        assert store.cache_dir == temp_cache_dir
        assert store.cache_dir.exists()
        store.close()

    def test_make_key(self, temp_cache_dir):
        """Test composite key generation."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        digest = "a" * 64
        key = store._make_key(op_name, digest)
        assert key == f"{op_name}:{digest}"
        store.close()

    def test_make_key_invalid_digest(self, temp_cache_dir):
        """Test _make_key with invalid digest length."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        with pytest.raises(ValueError, match="Invalid digest length"):
            store._make_key(op_name, "short")
        store.close()

    def test_exists_false(self, temp_cache_dir):
        """Test exists returns False for non-existent artifact."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        assert not store.exists(op_name, "a" * 64)
        store.close()

    def test_put_and_get(self, temp_cache_dir):
        """Test storing and retrieving an artifact."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        artifact = "test"
        digest = hash_value(artifact)

        store.put(op_name, digest, artifact)
        assert store.exists(op_name, digest)

        retrieved = store.get(op_name, digest)
        assert isinstance(retrieved, str)
        assert retrieved == "test"
        store.close()

    def test_get_nonexistent(self, temp_cache_dir):
        """Test that getting non-existent artifact raises KeyError."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        with pytest.raises(KeyError):
            store.get(op_name, "a" * 64)
        store.close()

    def test_put_and_get_integer(self, temp_cache_dir):
        """Test storing and retrieving integer."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        artifact = 42
        digest = hash_value(artifact)

        store.put(op_name, digest, artifact)
        retrieved = store.get(op_name, digest)
        assert isinstance(retrieved, int)
        assert retrieved == 42
        store.close()

    def test_put_and_get_decimal(self, temp_cache_dir):
        """Test storing and retrieving Decimal."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        artifact = Decimal("3.14159")
        digest = hash_value(artifact)

        store.put(op_name, digest, artifact)
        retrieved = store.get(op_name, digest)
        assert isinstance(retrieved, Decimal)
        assert retrieved == artifact
        store.close()

    def test_persistence(self, temp_cache_dir):
        """Test that artifacts persist across store instances."""
        store1 = DiskStore(temp_cache_dir)
        op_name = "test:op"
        artifact = "persistent"
        digest = hash_value(artifact)

        store1.put(op_name, digest, artifact)
        store1.close()

        # Create new store instance
        store2 = DiskStore(temp_cache_dir)
        assert store2.exists(op_name, digest)
        retrieved = store2.get(op_name, digest)
        assert retrieved == "persistent"
        store2.close()

    def test_configurable_size_limit_and_eviction_policy(self, temp_cache_dir):
        """Test custom bounded-cache configuration."""
        store = DiskStore(
            temp_cache_dir,
            size_limit_bytes=12345,
            eviction_policy="least-recently-used",
            cull_limit=2,
        )
        assert store.size_limit_bytes == 12345
        assert store.eviction_policy == "least-recently-used"
        assert store.cull_limit == 2
        assert store._cache.size_limit == 12345
        store.close()

    def test_eviction_when_size_limit_exceeded(self, temp_cache_dir):
        """Test that cache culls entries when the volume exceeds the size limit."""
        store = DiskStore(temp_cache_dir, size_limit_bytes=170_000)
        op_name = "test:op"

        artifacts = []
        for _ in range(4):
            artifact = self._payload(40_000)
            digest = hash_value(artifact)
            store.put(op_name, digest, artifact)
            artifacts.append((digest, artifact))

        store._cache.cull()
        remaining = [digest for digest, _ in artifacts if store.exists(op_name, digest)]
        assert remaining
        assert len(remaining) < len(artifacts)
        assert store._cache.volume() <= store.size_limit_bytes
        store.close()

    def test_lfu_eviction_prefers_hot_entry(self, temp_cache_dir):
        """Test LFU eviction retains the more frequently accessed entry."""
        store = DiskStore(temp_cache_dir, size_limit_bytes=170_000)
        op_name = "test:op"

        hot = self._payload(40_000)
        cold = self._payload(40_000)
        newer = self._payload(40_000)
        hot_digest = hash_value(hot)
        cold_digest = hash_value(cold)
        newer_digest = hash_value(newer)

        store.put(op_name, hot_digest, hot)
        store.put(op_name, cold_digest, cold)

        store.get(op_name, hot_digest)
        store.get(op_name, hot_digest)

        store.put(op_name, newer_digest, newer)
        store._cache.cull()

        assert store.exists(op_name, hot_digest)
        assert store.exists(op_name, newer_digest)
        assert not store.exists(op_name, cold_digest)
        store.close()

    def test_concurrent_access(self, temp_cache_dir):
        """Test concurrent thread access to the same cache directory."""
        op_name = "test:op"
        digests = []
        failures = []

        def worker(index: int) -> None:
            try:
                store = DiskStore(temp_cache_dir)
                artifact = f"value-{index}" * 128
                digest = hash_value(artifact)
                store.put(op_name, digest, artifact)
                assert store.exists(op_name, digest)
                assert store.get(op_name, digest) == artifact
                digests.append(digest)
                store.close()
            except Exception as exc:  # pragma: no cover - failure path assertion
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not failures
        final_store = DiskStore(temp_cache_dir)
        for digest in digests:
            assert final_store.exists(op_name, digest)
        final_store.close()

    def test_stats_tracking(self, temp_cache_dir):
        """Test stats still track hits, misses, and puts."""
        store = DiskStore(temp_cache_dir)
        op_name = "test:op"
        artifact = "stats"
        digest = hash_value(artifact)

        assert not store.exists(op_name, digest)
        store.put(op_name, digest, artifact)
        assert store.exists(op_name, digest)
        assert store.get(op_name, digest) == artifact

        assert store.stats.misses == 1
        assert store.stats.hits == 1
        assert store.stats.puts == 1
        store.close()
