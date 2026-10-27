from unittest.mock import MagicMock, patch

import api_cache


def test_request_key_is_stable_and_excludes_api_secret():
    first = api_cache._request_key("streetview", "https://example.test/x?b=2&key=secret-a&a=1")
    second = api_cache._request_key("streetview", "https://example.test/x?a=1&key=secret-b&b=2")
    assert first == second
    assert "secret-a" not in first


def test_cached_urlopen_uses_cache_without_network():
    with patch.object(api_cache._CACHE, "get", return_value=b"cached"):
        with patch("api_cache.urllib.request.urlopen") as urlopen:
            result = api_cache.cached_urlopen_bytes(
                "https://example.test/data", service="test", timeout=1
            )
    assert result == b"cached"
    urlopen.assert_not_called()


def test_cached_urlopen_stores_live_response():
    response = MagicMock()
    response.read.return_value = b"live"
    response.__enter__.return_value = response
    with patch.object(api_cache._CACHE, "get", return_value=None):
        with patch.object(api_cache._CACHE, "set") as cache_set:
            with patch("api_cache.urllib.request.urlopen", return_value=response):
                result = api_cache.cached_urlopen_bytes(
                    "https://example.test/data", service="test", timeout=1
                )
    assert result == b"live"
    cache_set.assert_called_once_with("test", "https://example.test/data", b"live")


def test_cached_urlopen_bypasses_reads_and_writes_when_disabled():
    response = MagicMock()
    response.read.return_value = b"fresh"
    response.__enter__.return_value = response
    with patch.object(api_cache._CACHE, "get") as cache_get:
        with patch.object(api_cache._CACHE, "set") as cache_set:
            with patch("api_cache.urllib.request.urlopen", return_value=response) as urlopen:
                result = api_cache.cached_urlopen_bytes(
                    "https://example.test/data",
                    service="test",
                    timeout=1,
                    use_cache=False,
                )
    assert result == b"fresh"
    cache_get.assert_not_called()
    cache_set.assert_not_called()
    urlopen.assert_called_once()
