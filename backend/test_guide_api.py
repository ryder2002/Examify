"""Focused security and media validation tests for the guide module."""

from __future__ import annotations

import unittest
from io import BytesIO

from fastapi import HTTPException
from PIL import Image

from guide_api import _detected_media, sanitize_guide_html


class GuideSecurityTests(unittest.TestCase):
    def test_image_content_is_detected_from_bytes(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (32, 18), "white").save(buffer, format="PNG")
        media_type, mime_type, width, height = _detected_media(
            buffer.getvalue(), "application/octet-stream"
        )
        self.assertEqual((media_type, mime_type), ("image", "image/png"))
        self.assertEqual((width, height), (32, 18))

    def test_executable_disguised_as_media_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _detected_media(b"#!/bin/sh\nrm -rf /", "image/png")
        self.assertEqual(raised.exception.status_code, 415)

    def test_html_blocks_base64_script_and_unsafe_iframe(self) -> None:
        with self.assertRaises(HTTPException):
            sanitize_guide_html('<img src="data:image/png;base64,abc">')
        cleaned = sanitize_guide_html(
            '<script>alert(1)</script><iframe src="https://evil.example/embed"></iframe>'
            '<iframe src="https://www.youtube-nocookie.com/embed/abc123"></iframe>'
        )
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("evil.example", cleaned)
        self.assertIn("youtube-nocookie.com", cleaned)


if __name__ == "__main__":
    unittest.main()
