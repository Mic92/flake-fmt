#!/usr/bin/env python3
"""A smart formatter wrapper for Nix flakes with caching."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def find_flake_root(start_path: Path | None = None) -> Path | None:
    """Find the nearest flake.nix by searching up the directory tree."""
    if start_path is None:
        start_path = Path.cwd()
    current = start_path.resolve()

    while current != current.parent:
        flake_path = current / "flake.nix"
        if flake_path.exists():
            return current
        current = current.parent

    # Check root directory
    flake_path = current / "flake.nix"
    if flake_path.exists():
        return current

    return None


def parse_arguments(args: list[str]) -> tuple[list[str], list[str]]:
    """Parse command line arguments into nix args and formatter args.

    Everything before '--' goes to nix, everything after goes to the formatter.
    """
    if "--" in args:
        split_index = args.index("--")
        nix_args = args[:split_index]
        formatter_args = args[split_index + 1 :]
    else:
        nix_args = args
        formatter_args = []

    return nix_args, formatter_args


def run_nix(
    args: list[str],
    cwd: Path | None = None,
    *,
    capture_output: bool = True,
) -> str | subprocess.CompletedProcess:
    """Run a nix command with proper error handling."""
    cmd = ["nix", *args]

    if capture_output:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd, shell=False)
        if result.returncode != 0:
            sys.exit(result.returncode)
        return result.stdout.strip()
    return subprocess.run(cmd, check=False, cwd=cwd, shell=False)


def get_current_system() -> str:
    """Get the current system identifier."""
    result = run_nix(["eval", "--raw", "--impure", "--expr", "builtins.currentSystem"])
    if isinstance(result, str):
        return result
    msg = "Expected string result from nix eval"
    raise TypeError(msg)


def get_cache_path(toplevel: Path) -> tuple[Path, Path]:
    """Get cache directory and formatter cache path."""
    escaped_toplevel = hashlib.sha256(str(toplevel).encode()).hexdigest()
    cache_home = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    cache_dir = Path(cache_home) / "flake-fmt"
    fmt_cache_path = cache_dir / escaped_toplevel
    return cache_dir, fmt_cache_path


def check_cache_validity(fmt_cache_path: Path, toplevel: Path) -> tuple[bool, list[str]]:
    """Check if cache needs update and return build args."""
    needs_update = False
    build_args = []

    if not fmt_cache_path.exists() or os.environ.get("NO_CACHE"):
        needs_update = True
    else:
        build_args.extend(["-o", str(fmt_cache_path)])
        reference_time = fmt_cache_path.stat().st_mtime

        # Check if flake.nix or flake.lock are newer
        for file in ["flake.nix", "flake.lock"]:
            file_path = toplevel / file
            if file_path.exists() and file_path.stat().st_mtime > reference_time:
                needs_update = True
                break

    return needs_update, build_args


def build_formatter(
    toplevel: Path,
    current_system: str,
    fmt_cache_path: Path,
    build_args: list[str],
    nix_args: list[str],
) -> Path:
    """Build the formatter if needed."""
    # Check if formatter exists for current system
    has_formatter_check = f"(val: val ? {current_system})"
    result = run_nix(["eval", ".#formatter", "--apply", has_formatter_check, *nix_args], cwd=toplevel)
    if not isinstance(result, str):
        msg = "Expected string result from nix eval"
        raise TypeError(msg)

    if result != "true":
        print("Warning: No formatter defined", file=sys.stderr)
        sys.exit(0)

    # Build formatter
    build_cmd = [
        "build",
        "--print-out-paths",
        "--out-link",
        str(fmt_cache_path),
        "--builders",
        "",
        "--keep-failed",
        *build_args,
        *nix_args,
        f".#formatter.{current_system}",
    ]
    fmt_path = run_nix(build_cmd, cwd=toplevel)
    if not isinstance(fmt_path, str):
        msg = "Expected string result from nix build"
        raise TypeError(msg)
    return Path(fmt_path.strip())


def execute_formatter(fmt_cache_path: Path, formatter_args: list[str], toplevel: Path) -> None:
    """Execute the formatter."""
    # Check for treefmt first (it has multiple outputs)
    treefmt_path = fmt_cache_path / "bin" / "treefmt"
    if treefmt_path.exists() and os.access(treefmt_path, os.X_OK):
        result = subprocess.run([str(treefmt_path), *formatter_args], check=False, cwd=toplevel, shell=False)
        sys.exit(result.returncode)

    # Find any executable in bin directory
    bin_dir = fmt_cache_path / "bin"
    if bin_dir.exists():
        for file_path in bin_dir.iterdir():
            if file_path.is_file() and os.access(file_path, os.X_OK):
                result = subprocess.run([str(file_path), *formatter_args], check=False, cwd=toplevel, shell=False)
                sys.exit(result.returncode)

    # If no executable found, the formatter itself might be executable
    if os.access(fmt_cache_path, os.X_OK):
        result = subprocess.run([str(fmt_cache_path), *formatter_args], check=False, cwd=toplevel, shell=False)
        sys.exit(result.returncode)

    sys.exit(1)


def main(args: list[str] | None = None) -> None:
    """Run the flake formatter with caching."""
    # Parse arguments
    if args is None:
        args = sys.argv[1:]
    nix_args, formatter_args = parse_arguments(args)

    # Find flake root
    toplevel = find_flake_root()
    if toplevel is None:
        print("No flake.nix found", file=sys.stderr)
        sys.exit(1)

    # Get current system
    current_system = get_current_system()

    # Set up cache
    cache_dir, fmt_cache_path = get_cache_path(toplevel)

    # Check cache validity
    needs_update, build_args = check_cache_validity(fmt_cache_path, toplevel)

    if needs_update:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fmt_cache_path = build_formatter(toplevel, current_system, fmt_cache_path, build_args, nix_args)

    # Execute formatter
    execute_formatter(fmt_cache_path, formatter_args, toplevel)


if __name__ == "__main__":
    main()
