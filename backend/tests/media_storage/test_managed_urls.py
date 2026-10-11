from __future__ import annotations

import unittest

from haoai_backend.media_storage.managed_urls import normalize_managed_url


class ManagedURLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin_map = {"https://legacy.example:443/": "https://media.example"}

    def test_current_managed_url_and_query_are_returned_byte_for_byte(self) -> None:
        url = "https://MEDIA.example:443/a/%7Eimage.png?sig=A%2fb+c&empty="
        self.assertEqual(normalize_managed_url(url, self.origin_map), url)

    def test_allowlisted_legacy_origin_maps_and_preserves_path_and_query_bytes(self) -> None:
        url = "https://LEGACY.EXAMPLE/a/%7eimage.png?sig=A%2fb+c&empty="
        expected = "https://media.example/a/%7eimage.png?sig=A%2fb+c&empty="
        self.assertEqual(normalize_managed_url(url, self.origin_map), expected)

    def test_effective_port_host_case_and_trailing_dot_are_parsed_exactly(self) -> None:
        mapping = {"HTTPS://Legacy.Example:443": "https://media.example:443/"}
        url = "https://legacy.example.:443/file.png?token=one%2ftwo"
        expected = "https://media.example:443/file.png?token=one%2ftwo"
        self.assertEqual(normalize_managed_url(url, mapping), expected)

    def test_parsed_origin_tuple_map_uses_exact_effective_origins(self) -> None:
        mapping = {
            ("https", "legacy.example", 443): ("https", "media.example", 443),
        }
        url = "https://legacy.example/file.png?signature=A%2fb"
        self.assertEqual(
            normalize_managed_url(url, mapping),
            "https://media.example/file.png?signature=A%2fb",
        )

    def test_similar_host_unlisted_origin_and_cross_origin_are_unmanaged(self) -> None:
        for url in (
            "https://legacy.example.evil/file.png?x=1",
            "https://other.example/file.png?x=1",
            "http://legacy.example/file.png?x=1",
            "https://media.example.evil/file.png?x=1",
        ):
            with self.subTest(url=url):
                self.assertIsNone(normalize_managed_url(url, self.origin_map))

    def test_root_traversal_and_empty_path_segments_are_unmanaged(self) -> None:
        for url in (
            "https://legacy.example/",
            "https://legacy.example/a/../b.png",
            "https://legacy.example/a/%2e%2e/b.png",
            "https://legacy.example/a//b.png",
            "https://legacy.example/a/%2Fimage.png",
        ):
            with self.subTest(url=url):
                self.assertIsNone(normalize_managed_url(url, self.origin_map))

    def test_credentials_fragments_malformed_ports_and_other_schemes_are_unmanaged(self) -> None:
        for url in (
            "https://user:secret@legacy.example/file.png",
            "https://legacy.example/file.png#fragment",
            "https://legacy.example:bad/file.png",
            "file:///tmp/image.png",
            "data:image/png;base64,eA==",
            "https://legacy.example/a b.png",
            "https://legacy.example/a%2",
            "https://legacy.example/file.png?token=%Q1",
        ):
            with self.subTest(url=url):
                self.assertIsNone(normalize_managed_url(url, self.origin_map))

    def test_invalid_explicit_origins_are_ignored_without_mapping(self) -> None:
        invalid_maps = (
            {"https://legacy.example/path": "https://media.example"},
            {"https://legacy.example": "https://user@media.example"},
            {"https://legacy.example": "ftp://media.example"},
        )
        url = "https://legacy.example/file.png?token=1"
        for mapping in invalid_maps:
            with self.subTest(mapping=mapping):
                self.assertIsNone(normalize_managed_url(url, mapping))
        self.assertIsNone(normalize_managed_url(url, {}))
        self.assertIsNone(normalize_managed_url(url, None))


if __name__ == "__main__":
    unittest.main()
