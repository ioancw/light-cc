"""Tests for core.plugin_manager -- manifest validation + install/uninstall lifecycle.

These cover the pure-function layer. The REST layer (routes/plugins.py) has its
own tests in test_plugins_api.py; here we hit plugin_manager directly so failure
modes aren't only exercised through HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import plugin_manager as pm
from core.plugin_manager import PluginError


def _write_manifest(plugin_dir: Path, data: dict) -> Path:
    """Helper: write a valid directory layout with the given manifest data."""
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / "plugin.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def plugins_root(tmp_path, monkeypatch):
    """Redirect the plugin manager at a fresh tmp dir and skip dep installs."""
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(pm, "_resolve_plugins_dir", lambda: root)
    monkeypatch.setattr(pm, "_install_dependencies", AsyncMock(return_value=None))
    return root


@pytest.fixture
def source_plugin(tmp_path):
    """A well-formed plugin directory in a non-plugins location, ready to install from."""
    src = tmp_path / "src" / "demo-plugin"
    src.mkdir(parents=True)
    _write_manifest(src, {
        "name": "demo-plugin",
        "version": "1.2.3",
        "description": "demo",
    })
    return src


# ── Manifest validation ─────────────────────────────────────────────────


class TestManifestValidation:
    def test_valid_manifest_returns_dict(self, tmp_path):
        path = _write_manifest(tmp_path / "p", {"name": "good", "version": "1.0.0"})
        got = pm._validate_manifest(path)
        assert got["name"] == "good"
        assert got["version"] == "1.0.0"

    def test_missing_name_raises(self, tmp_path):
        path = _write_manifest(tmp_path / "p", {"version": "1.0.0"})
        with pytest.raises(PluginError, match="missing 'name'"):
            pm._validate_manifest(path)

    def test_missing_version_raises(self, tmp_path):
        path = _write_manifest(tmp_path / "p", {"name": "good"})
        with pytest.raises(PluginError, match="missing 'version'"):
            pm._validate_manifest(path)

    @pytest.mark.parametrize("bad_name", [
        "UPPER",          # uppercase letters
        "-leading-dash",  # starts with hyphen
        "has space",      # whitespace
        "under_score",    # underscore not in charset
        "dot.name",       # dot not in charset
    ])
    def test_invalid_name_pattern_raises(self, tmp_path, bad_name):
        path = _write_manifest(tmp_path / "p", {"name": bad_name, "version": "1.0.0"})
        with pytest.raises(PluginError, match="Invalid plugin name"):
            pm._validate_manifest(path)

    def test_malformed_json_raises(self, tmp_path):
        manifest_dir = tmp_path / ".claude-plugin"
        manifest_dir.mkdir()
        path = manifest_dir / "plugin.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(PluginError, match="Failed to parse"):
            pm._validate_manifest(path)


# ── install_from_path ───────────────────────────────────────────────────


class TestInstallFromPath:
    @pytest.mark.asyncio
    async def test_happy_path_copies_and_returns_manifest(
        self, plugins_root, source_plugin,
    ):
        manifest = await pm.install_from_path(source_plugin)
        assert manifest["name"] == "demo-plugin"
        assert manifest["version"] == "1.2.3"

        installed = plugins_root / "demo-plugin"
        assert installed.exists()
        assert (installed / ".claude-plugin" / "plugin.json").exists()

    @pytest.mark.asyncio
    async def test_missing_source_raises(self, plugins_root, tmp_path):
        with pytest.raises(PluginError, match="is not an existing directory"):
            await pm.install_from_path(tmp_path / "nope")

    @pytest.mark.asyncio
    async def test_missing_manifest_raises(self, plugins_root, tmp_path):
        src = tmp_path / "no-manifest"
        src.mkdir()
        with pytest.raises(PluginError, match="No plugin manifest"):
            await pm.install_from_path(src)

    @pytest.mark.asyncio
    async def test_already_installed_raises(self, plugins_root, source_plugin):
        await pm.install_from_path(source_plugin)
        with pytest.raises(PluginError, match="already installed"):
            await pm.install_from_path(source_plugin)

    @pytest.mark.asyncio
    async def test_invalid_manifest_raises_before_copy(
        self, plugins_root, tmp_path,
    ):
        src = tmp_path / "bad"
        src.mkdir()
        _write_manifest(src, {"name": "Bad Name", "version": "1.0.0"})
        with pytest.raises(PluginError, match="Invalid plugin name"):
            await pm.install_from_path(src)
        assert not (plugins_root / "bad").exists()
        assert not (plugins_root / "Bad Name").exists()

    @pytest.mark.asyncio
    async def test_dependencies_installed_after_copy(
        self, plugins_root, source_plugin, monkeypatch,
    ):
        mock_deps = AsyncMock()
        monkeypatch.setattr(pm, "_install_dependencies", mock_deps)
        await pm.install_from_path(source_plugin)
        assert mock_deps.await_count == 1
        passed_manifest, passed_dir = mock_deps.await_args.args
        assert passed_manifest["name"] == "demo-plugin"
        assert passed_dir == plugins_root / "demo-plugin"


# ── install dispatcher ──────────────────────────────────────────────────


class TestInstallDispatch:
    @pytest.mark.asyncio
    async def test_local_path_routes_to_install_from_path(
        self, plugins_root, source_plugin,
    ):
        with patch.object(pm, "install_from_path", AsyncMock(return_value={"ok": True})) as p, \
             patch.object(pm, "install_from_git", AsyncMock()) as g:
            await pm.install(str(source_plugin))
        p.assert_awaited_once()
        g.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_git_url_routes_to_install_from_git(self, plugins_root):
        with patch.object(pm, "install_from_path", AsyncMock()) as p, \
             patch.object(pm, "install_from_git", AsyncMock(return_value={"ok": True})) as g:
            await pm.install("https://github.com/foo/bar.git")
        p.assert_not_awaited()
        g.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ssh_git_url_routes_to_git(self, plugins_root):
        with patch.object(pm, "install_from_git", AsyncMock(return_value={"ok": True})) as g:
            await pm.install("git@github.com:foo/bar.git")
        g.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bogus_source_raises(self, plugins_root):
        with pytest.raises(PluginError, match="not a valid path or git URL"):
            await pm.install("not-a-thing")


# ── install_from_git ────────────────────────────────────────────────────


def _mock_git_clone_success(clone_dest: Path, manifest_data: dict):
    """Return a `subprocess.run` side-effect that simulates a successful clone
    by materializing the plugin directory at the requested destination."""
    def _run(cmd, *args, **kwargs):
        if cmd[0] == "git" and cmd[1] == "clone":
            dest = Path(cmd[-1])
            dest.mkdir(parents=True, exist_ok=True)
            _write_manifest(dest, manifest_data)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")
    return _run


class TestInstallFromGit:
    @pytest.mark.asyncio
    async def test_happy_path(self, plugins_root):
        with patch.object(pm.subprocess, "run", side_effect=_mock_git_clone_success(
            plugins_root / "_installing",
            {"name": "cloned", "version": "0.1.0"},
        )):
            manifest = await pm.install_from_git("https://example.com/repo.git")
        assert manifest["name"] == "cloned"
        assert (plugins_root / "cloned" / ".claude-plugin" / "plugin.json").exists()
        assert not (plugins_root / "_installing").exists()

    @pytest.mark.asyncio
    async def test_clone_failure_cleans_up_and_raises(self, plugins_root):
        with patch.object(pm.subprocess, "run",
                          return_value=MagicMock(returncode=1, stderr="auth failed")):
            with pytest.raises(PluginError, match="git clone failed"):
                await pm.install_from_git("https://example.com/repo.git")
        assert not (plugins_root / "_installing").exists()

    @pytest.mark.asyncio
    async def test_cloned_repo_without_manifest_rejects(self, plugins_root):
        def _run(cmd, *args, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0)

        with patch.object(pm.subprocess, "run", side_effect=_run):
            with pytest.raises(PluginError, match="no .claude-plugin"):
                await pm.install_from_git("https://example.com/repo.git")
        assert not (plugins_root / "_installing").exists()


# ── uninstall ───────────────────────────────────────────────────────────


class TestUninstall:
    @pytest.mark.asyncio
    async def test_removes_installed_directory(self, plugins_root, source_plugin):
        await pm.install_from_path(source_plugin)
        assert (plugins_root / "demo-plugin").exists()
        await pm.uninstall("demo-plugin")
        assert not (plugins_root / "demo-plugin").exists()

    @pytest.mark.asyncio
    async def test_uninstall_missing_raises(self, plugins_root):
        with pytest.raises(PluginError, match="not found"):
            await pm.uninstall("never-installed")


# ── list_installed ──────────────────────────────────────────────────────


class TestListInstalled:
    def test_empty_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pm, "_resolve_plugins_dir", lambda: tmp_path / "does-not-exist")
        assert pm.list_installed() == []

    def test_returns_each_valid_manifest(self, plugins_root):
        for name in ("alpha", "beta"):
            d = plugins_root / name
            d.mkdir()
            _write_manifest(d, {"name": name, "version": "1.0.0"})
        got = {m["name"] for m in pm.list_installed()}
        assert got == {"alpha", "beta"}

    def test_skips_entries_without_manifest(self, plugins_root):
        (plugins_root / "no-manifest").mkdir()
        d = plugins_root / "has-manifest"
        d.mkdir()
        _write_manifest(d, {"name": "has-manifest", "version": "1.0.0"})
        got = [m["name"] for m in pm.list_installed()]
        assert got == ["has-manifest"]

    def test_skips_invalid_manifest_json_without_raising(self, plugins_root, caplog):
        d = plugins_root / "broken"
        d.mkdir()
        (d / ".claude-plugin").mkdir()
        (d / ".claude-plugin" / "plugin.json").write_text("{ not json")
        assert pm.list_installed() == []
