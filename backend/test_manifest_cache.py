import threading
import time

from manifest_cache import ManifestCache


def test_manifest_cache_singleflight_builds_once_when_redis_is_unavailable():
    cache = ManifestCache()
    cache._retry_after = time.monotonic() + 30
    builds = 0
    builds_lock = threading.Lock()
    results: list[dict] = []

    def builder() -> dict:
        nonlocal builds
        with builds_lock:
            builds += 1
        time.sleep(0.02)
        return {"questions": [{"number": 1}]}

    threads = [
        threading.Thread(
            target=lambda: results.append(cache.get_or_build("same", builder))
        )
        for _ in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert builds == 1
    assert len(results) == 20
    assert all(result == results[0] for result in results)
