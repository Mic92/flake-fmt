#!/usr/bin/env python3
"""Tests for flake-fmt."""

import io
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from flake_fmt import main


@pytest.fixture(autouse=True)
def nix_sandbox_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Set up environment variables for nix sandbox testing."""
    # Set up a temporary test root (same as old checks.nix)
    test_root = tmp_path / "nix-test"
    test_root.mkdir(exist_ok=True)

    # Set up Nix store paths
    monkeypatch.setenv("TEST_ROOT", str(test_root))
    monkeypatch.setenv("NIX_STORE_DIR", str(test_root / "store"))
    monkeypatch.setenv("NIX_DATA_DIR", str(test_root / "share"))
    monkeypatch.setenv("NIX_STATE_DIR", str(test_root / "state"))
    monkeypatch.setenv("NIX_LOG_DIR", str(test_root / "log"))
    monkeypatch.setenv("NIX_CONF_DIR", str(test_root / "etc"))
    monkeypatch.setenv("HOME", str(test_root / "home"))

    # Create directories
    for dir_name in ["store", "share/nix", "state/nix/db", "log/nix", "etc/nix", "home"]:
        (test_root / dir_name).mkdir(parents=True, exist_ok=True)

    # Set up build directory
    build_dir = test_root / "build"
    build_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("NIX_BUILD_TOP", str(build_dir))

    # Disable substituters and sandboxing
    monkeypatch.setenv(
        "NIX_CONFIG",
        """substituters =
sandbox = false
build-dir = """
        + str(build_dir),
    )
    monkeypatch.setenv("_NIX_TEST_NO_SANDBOX", "1")

    # Set up git config
    gitconfig = test_root / "home" / ".gitconfig"
    gitconfig.write_text("""[user]
    email = test@example.com
    name = Test User
""")

    # Initialize the Nix database
    subprocess.run(["nix-store", "--init"], check=True)


@pytest.fixture
def temp_flake_dir() -> Iterator[Path]:
    """Create a temporary directory with a basic flake setup."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Get current system
        result = subprocess.run(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            capture_output=True,
            text=True,
            check=True,
        )
        current_system = result.stdout.strip()

        # Create a basic flake.nix
        flake_content = f"""
{{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {{ self, nixpkgs }}: {{
    formatter.{current_system} = nixpkgs.legacyPackages.{current_system}.writeShellScriptBin "test-formatter" ''
      echo "Formatting..."
      touch .formatter-ran
      echo "$@"
    '';
  }};
}}
"""
        (tmpdir_path / "flake.nix").write_text(flake_content)

        # Run git init to make it a valid flake
        subprocess.run(["git", "init"], check=False, cwd=tmpdir_path, capture_output=True)
        subprocess.run(["git", "add", "flake.nix"], check=False, cwd=tmpdir_path, capture_output=True)

        yield tmpdir_path


def test_basic_functionality(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that flake-fmt runs the formatter."""
    monkeypatch.chdir(temp_flake_dir)

    # Add some debug output
    print(f"Working directory: {Path.cwd()}")
    print(f"Flake exists: {(temp_flake_dir / 'flake.nix').exists()}")
    print(f"Contents: {(temp_flake_dir / 'flake.nix').read_text()[:100]}")

    # Run flake-fmt
    stderr = io.StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc_info:
        main([])
    print(f"Exit code: {exc_info.value.code}")
    print(f"Stderr: {stderr.getvalue()}")
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()


def test_formatter_arguments(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that arguments are passed to the formatter."""
    monkeypatch.chdir(temp_flake_dir)

    # Capture stdout to check formatter output

    output = io.StringIO()
    with redirect_stdout(output), pytest.raises(SystemExit) as exc_info:
        main(["--", "arg1", "arg2"])

    assert exc_info.value.code == 0
    assert "arg1 arg2" in output.getvalue()


def test_nix_arguments(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that nix arguments are passed correctly."""
    monkeypatch.chdir(temp_flake_dir)

    with pytest.raises(SystemExit) as exc_info:
        main(["--quiet"])
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()


def test_cache_directory_created(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that cache directory is created correctly."""
    monkeypatch.chdir(temp_flake_dir)

    # Set custom cache directory
    cache_dir = temp_flake_dir / "custom-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    assert (cache_dir / "flake-fmt").exists()


def test_cache_invalidation_on_flake_change(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that cache is invalidated when flake.nix changes."""
    monkeypatch.chdir(temp_flake_dir)

    # First run
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0

    # Wait a bit to ensure timestamp difference
    time.sleep(0.1)

    # Delete marker file
    (temp_flake_dir / ".formatter-ran").unlink()

    # Touch flake.nix to update its timestamp
    (temp_flake_dir / "flake.nix").touch()

    # Second run should rebuild
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()


def test_no_cache_environment_variable(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that NO_CACHE environment variable forces rebuild."""
    monkeypatch.chdir(temp_flake_dir)

    # First run to populate cache
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0

    # Delete marker file
    (temp_flake_dir / ".formatter-ran").unlink()

    # Run with NO_CACHE
    monkeypatch.setenv("NO_CACHE", "1")

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()


def test_no_formatter_defined(monkeypatch: MonkeyPatch) -> None:
    """Test behavior when no formatter is defined."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a flake without formatter
        flake_content = """
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: {};
}
"""
        (tmpdir_path / "flake.nix").write_text(flake_content)

        # Run git init
        subprocess.run(["git", "init"], check=False, cwd=tmpdir_path, capture_output=True)
        subprocess.run(["git", "add", "flake.nix"], check=False, cwd=tmpdir_path, capture_output=True)

        monkeypatch.chdir(tmpdir_path)

        # Capture stderr

        stderr = io.StringIO()
        with redirect_stderr(stderr), pytest.raises(SystemExit) as exc_info:
            main([])

        assert exc_info.value.code == 0
        assert "Warning: No formatter defined" in stderr.getvalue()


def test_find_flake_in_subdirectory(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that flake-fmt finds flake.nix in parent directory."""
    # Create subdirectory
    subdir = temp_flake_dir / "subdir"
    subdir.mkdir()

    monkeypatch.chdir(subdir)

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()


def test_no_flake_found(monkeypatch: MonkeyPatch) -> None:
    """Test error when no flake.nix is found."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.chdir(tmpdir)

        # Capture stderr

        stderr = io.StringIO()
        with redirect_stderr(stderr), pytest.raises(SystemExit) as exc_info:
            main([])

        assert exc_info.value.code == 1
        assert "No flake.nix found" in stderr.getvalue()
