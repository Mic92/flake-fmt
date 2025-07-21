#!/usr/bin/env python3
"""Tests for flake-fmt."""

import io
import subprocess
import tarfile
import tempfile
import time
from contextlib import redirect_stderr
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
    monkeypatch.delenv("NIX_REMOTE", raising=False)

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
def temp_flake_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with a basic flake setup."""
    tmpdir_path = Path(tmp_path)

    # Get current system
    result = subprocess.run(
        ["nix", "--extra-experimental-features", "flakes nix-command", "config", "show", "system"],
        capture_output=True,
        text=True,
        check=True,
    )
    current_system = result.stdout.strip()

    # Create a test formatter tarball
    formatter_build = tmpdir_path / "formatter-build"
    formatter_build.mkdir()
    bin_dir = formatter_build / "bin"
    bin_dir.mkdir()

    # Create the test formatter script
    formatter_script = bin_dir / "test-formatter"
    formatter_script.write_text("""#!/bin/sh
echo "Formatting..."
echo "$@" > .formatter-ran
""")
    formatter_script.chmod(0o755)

    # Create tarball with a parent directory
    tarball_path = tmpdir_path / "formatter.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        # Add with parent directory to match Nix's expectations
        tar.add(formatter_build, arcname="formatter", recursive=True)

    # Create a basic flake.nix that uses the tarball as an input
    flake_content = f"""
{{
  inputs.formatter.url = "tarball+file://{tarball_path}";
  inputs.formatter.flake = false;

  outputs = {{ self, formatter }}: {{
    formatter.{current_system} = formatter.outPath;
  }};
}}
"""
    (tmpdir_path / "flake.nix").write_text(flake_content)

    # Run git init to make it a valid flake
    subprocess.run(["git", "init"], check=False, cwd=tmpdir_path, capture_output=True)
    subprocess.run(["git", "add", "flake.nix"], check=False, cwd=tmpdir_path, capture_output=True)

    return tmpdir_path


def test_basic_functionality(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that flake-fmt runs the formatter."""
    monkeypatch.chdir(temp_flake_dir)

    # Run flake-fmt
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    assert (temp_flake_dir / ".formatter-ran").exists()
    # When no args are passed, the file should be empty
    assert (temp_flake_dir / ".formatter-ran").read_text().strip() == ""


def test_formatter_arguments(temp_flake_dir: Path, monkeypatch: MonkeyPatch) -> None:
    """Test that arguments are passed to the formatter."""
    monkeypatch.chdir(temp_flake_dir)

    # Run with arguments
    with pytest.raises(SystemExit) as exc_info:
        main(["--", "arg1", "arg2"])

    assert exc_info.value.code == 0
    # Check that arguments were passed to the formatter
    assert (temp_flake_dir / ".formatter-ran").exists()
    assert (temp_flake_dir / ".formatter-ran").read_text().strip() == "arg1 arg2"


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
  outputs = { self }: {};
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

        print(stderr.getvalue())
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


def test_no_flake_found(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test error when no flake.nix is found."""
    # Create a git repository to prevent find_flake_root from searching parent directories
    test_dir = tmp_path / "test_no_flake"
    test_dir.mkdir()

    # Initialize git repo to create a boundary
    subprocess.run(["git", "init"], check=True, cwd=test_dir, capture_output=True)

    monkeypatch.chdir(test_dir)

    # Capture stderr
    stderr = io.StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc_info:
        main([])

    print(stderr.getvalue())
    assert "No flake.nix found" in stderr.getvalue()
    assert exc_info.value.code == 1
