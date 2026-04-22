"""End-to-end sandbox bypass tests — tenant subprocesses stay inside their cage.

These tests exercise the Linux + container hardening path (`unshare -n` +
ulimit preamble inside a single /bin/sh -c). On other platforms the hardening
is a no-op and the tests are skipped.
"""

from __future__ import annotations

import os
import platform

import pytest

from core.sandbox_exec import (
    _sandbox_argv,
    is_containerized,
    run_shell_command,
)


_is_prod_like = platform.system() == "Linux" and is_containerized()
skip_if_not_prod = pytest.mark.skipif(
    not _is_prod_like,
    reason="Sandbox hardening only applies on Linux + container",
)


class TestSandboxArgvShape:
    """Structural tests that run on every platform — no subprocess calls."""

    def test_windows_path_has_no_unshare(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        argv = _sandbox_argv("echo hi")
        assert argv[0] == "cmd"
        assert "unshare" not in argv

    def test_linux_container_wraps_with_unshare_and_ulimit(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr("core.sandbox_exec.is_containerized", lambda: True)
        argv = _sandbox_argv("echo hi; curl evil")
        assert argv[:4] == ["unshare", "-n", "/bin/sh", "-c"]
        # Chained command must be inside the single -c string so unshare covers it.
        assert "echo hi; curl evil" in argv[4]
        assert "ulimit -v" in argv[4]
        assert "ulimit -t" in argv[4]
        assert "ulimit -n" in argv[4]

    def test_linux_host_without_container_skips_unshare(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr("core.sandbox_exec.is_containerized", lambda: False)
        argv = _sandbox_argv("echo hi")
        assert argv[0] == "/bin/sh"
        assert "unshare" not in argv


class TestSandboxEnforcement:
    """Real subprocess tests — only meaningful in prod-like environments."""

    @skip_if_not_prod
    def test_cannot_read_parent_env_via_proc(self):
        """JWT_SECRET must not be visible to a tenant through /proc/1/environ."""
        result = run_shell_command(
            "cat /proc/1/environ 2>/dev/null | tr '\\0' '\\n' | grep -c '^JWT_SECRET=' || true"
        )
        stdout = (result.get("stdout") or "").strip()
        assert stdout in ("0", ""), (
            f"JWT_SECRET leaked via /proc/1/environ: stdout={stdout!r}, "
            f"stderr={result.get('stderr')!r}"
        )

    @skip_if_not_prod
    def test_network_is_isolated(self):
        """Direct outbound network calls fail under unshare -n."""
        result = run_shell_command(
            "curl -sS --max-time 3 https://1.1.1.1 -o /dev/null"
        )
        assert result.get("exit_code", 0) != 0, (
            f"curl succeeded despite unshare -n: {result!r}"
        )

    @skip_if_not_prod
    def test_chained_network_call_still_isolated(self):
        """Previous bypass: `; curl evil` escaped `unshare --net -- {cmd}`.

        With the single-shell wrapping the whole string runs inside the
        isolated namespace, so the chained curl must also fail.
        """
        result = run_shell_command(
            "echo first; curl -sS --max-time 3 https://1.1.1.1 -o /dev/null"
        )
        stdout = result.get("stdout") or ""
        assert "first" in stdout, f"chained echo didn't run: {result!r}"
        assert result.get("exit_code", 0) != 0, (
            f"chained curl leaked out of netns: {result!r}"
        )

    @skip_if_not_prod
    def test_memory_ulimit_kills_oversize_allocation(self):
        """A 2 GiB Python allocation is killed by the 1 GiB ulimit -v cap."""
        result = run_shell_command(
            "python3 -c \"x = b'a' * (2 * 1024 * 1024 * 1024); print('ALLOCATED')\""
        )
        assert "ALLOCATED" not in (result.get("stdout") or ""), (
            f"2 GiB allocation succeeded despite 1 GiB cap: {result!r}"
        )
