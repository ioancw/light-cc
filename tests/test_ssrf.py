"""Tests for SSRF protection in web tools."""

from __future__ import annotations

import pytest
from tools.web import _is_safe_url


class TestSSRF:
    def test_public_url_allowed(self):
        safe, reason = _is_safe_url("https://example.com")
        assert safe, reason

    def test_public_url_with_path(self):
        safe, reason = _is_safe_url("https://api.github.com/repos/user/repo")
        assert safe, reason

    def test_localhost_blocked(self):
        safe, reason = _is_safe_url("http://127.0.0.1:8000")
        assert not safe
        assert "private" in reason.lower() or "internal" in reason.lower()

    def test_localhost_name_blocked(self):
        safe, reason = _is_safe_url("http://localhost:8000")
        assert not safe

    def test_private_10_blocked(self):
        safe, reason = _is_safe_url("http://10.0.0.1/admin")
        assert not safe

    def test_private_172_blocked(self):
        safe, reason = _is_safe_url("http://172.16.0.1/")
        assert not safe

    def test_private_192_blocked(self):
        safe, reason = _is_safe_url("http://192.168.1.1/")
        assert not safe

    def test_metadata_endpoint_blocked(self):
        safe, reason = _is_safe_url("http://169.254.169.254/latest/meta-data/")
        assert not safe

    def test_gcp_metadata_blocked(self):
        safe, reason = _is_safe_url("http://metadata.google.internal/computeMetadata/v1/")
        assert not safe

    def test_ftp_scheme_blocked(self):
        safe, reason = _is_safe_url("ftp://example.com/file")
        assert not safe
        assert "scheme" in reason.lower()

    def test_file_scheme_blocked(self):
        safe, reason = _is_safe_url("file:///etc/passwd")
        assert not safe

    def test_no_hostname(self):
        safe, reason = _is_safe_url("http://")
        assert not safe

    def test_invalid_url(self):
        safe, reason = _is_safe_url("not a url")
        assert not safe
