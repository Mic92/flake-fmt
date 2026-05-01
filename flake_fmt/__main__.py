#!/usr/bin/env python3
"""A smart formatter wrapper for Nix flakes with caching."""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from subprocess import PIPE

logger = logging.getLogger(__name__)


def find_flake_root(start_path: Path | None = None) -> Path | None:
    """Find the nearest flake.nix walking up, stopping at git boundaries."""
    start = (start_path or Path.cwd()).resolve()
    logger.debug("Searching for flake.nix starting from: %s", start)

    for current in [start, *start.parents]:
        if (current / "flake.nix").exists():
            logger.debug("Found flake.nix at: %s", current)
            return current
        if (current / ".git").exists():
            logger.debug("Stopped at git boundary: %s", current)
            return None

    logger.debug("No flake.nix found")
    return None


def parse_arguments(args: list[str]) -> tuple[list[str], list[str]]:
    """Split args on '--': before goes to nix, after goes to the formatter."""
    if "--" not in args:
        return args, []
    i = args.index("--")
    return args[:i], args[i + 1 :]


def run_nix(
    args: list[str],
    cwd: Path | None = None,
    *,
    stdout: int | None = None,
    stderr: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a nix command with experimental features enabled."""
    cmd = ["nix", "--extra-experimental-features", "flakes nix-command", *args]
    return subprocess.run(cmd, check=check, stdout=stdout, stderr=stderr, text=True, cwd=cwd, shell=False)


def get_cache_path(toplevel: Path) -> Path:
    """Cache file path for this flake (one symlink per flake root)."""
    digest = hashlib.sha256(str(toplevel).encode()).hexdigest()
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = cache_home / "flake-fmt" / digest
    logger.debug("Formatter cache path: %s (from %s)", path, toplevel)
    return path


def cache_is_stale(fmt_cache_path: Path, toplevel: Path) -> bool:
    """Return True if the cache symlink is missing, forced, or older than flake inputs."""
    if os.environ.get("NO_CACHE"):
        logger.debug("NO_CACHE set, forcing rebuild")
        return True
    if not fmt_cache_path.exists():
        logger.debug("Cache missing at %s", fmt_cache_path)
        return True

    ref_mtime = fmt_cache_path.lstat().st_mtime
    for name in ("flake.nix", "flake.lock"):
        f = toplevel / name
        if f.exists() and f.stat().st_mtime > ref_mtime:
            logger.debug("%s newer than cache, rebuild needed", name)
            return True
    logger.debug("Cache up to date")
    return False


def build_formatter(
    toplevel: Path,
    fmt_cache_path: Path,
    nix_args: list[str],
) -> Path:
    """Build .#formatter.<system> into fmt_cache_path. Returns store path."""
    current_system = run_nix(["config", "show", "system"], stdout=PIPE).stdout.strip()
    logger.debug("Building formatter for system: %s", current_system)
    build_cmd = [
        "build",
        "--print-out-paths",
        "--out-link",
        str(fmt_cache_path),
        "--builders",
        "",
        "--keep-failed",
        *nix_args,
        f".#formatter.{current_system}",
    ]
    logger.debug("Running: nix %s", " ".join(build_cmd))
    try:
        result = run_nix(build_cmd, cwd=toplevel, stdout=PIPE, stderr=PIPE)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            sys.stderr.write(e.stderr)
        if e.stderr and "does not provide attribute" in e.stderr:
            print("Warning: No formatter defined", file=sys.stderr)
            sys.exit(1)
        sys.exit(e.returncode)
    out = Path(result.stdout.strip())
    logger.debug("Formatter built at: %s", out)
    return out


def find_executable(fmt_cache_path: Path) -> Path | None:
    """Pick formatter executable: prefer treefmt, else first executable in bin/ (sorted)."""
    bin_dir = fmt_cache_path / "bin"
    if not bin_dir.is_dir():
        return None
    treefmt = bin_dir / "treefmt"
    if treefmt.exists() and os.access(treefmt, os.X_OK):
        return treefmt
    for f in sorted(bin_dir.iterdir()):
        if f.is_file() and os.access(f, os.X_OK):
            return f
    return None


def execute_formatter(fmt_cache_path: Path, formatter_args: list[str], toplevel: Path) -> None:
    """Replace current process with the formatter."""
    exe = find_executable(fmt_cache_path)
    if exe is None:
        logger.error("No executable formatter found at %s", fmt_cache_path)
        sys.exit(1)
    logger.debug("Exec %s with args: %s", exe, formatter_args)
    os.chdir(toplevel)
    os.execv(str(exe), [str(exe), *formatter_args])  # noqa: S606


def main(args: list[str] | None = None) -> None:
    """Run the flake formatter with caching."""
    if os.environ.get("FLAKE_FMT_DEBUG", "").lower() in {"1", "true", "yes", "on"}:
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
        logger.debug("Debug logging enabled via FLAKE_FMT_DEBUG")

    try:
        nix_args, formatter_args = parse_arguments(sys.argv[1:] if args is None else args)
        logger.debug("Nix args: %s | Formatter args: %s", nix_args, formatter_args)

        toplevel = find_flake_root()
        if toplevel is None:
            print("No flake.nix found", file=sys.stderr)
            sys.exit(1)
        logger.debug("Flake root: %s", toplevel)

        fmt_cache_path = get_cache_path(toplevel)

        if cache_is_stale(fmt_cache_path, toplevel):
            fmt_cache_path.parent.mkdir(parents=True, exist_ok=True)
            fmt_cache_path = build_formatter(toplevel, fmt_cache_path, nix_args)
        else:
            logger.debug("Using cached formatter")

        execute_formatter(fmt_cache_path, formatter_args, toplevel)
    except KeyboardInterrupt:
        sys.exit(130)
    except subprocess.CalledProcessError as e:
        msg = e.stderr or str(e)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(e.returncode or 1)


if __name__ == "__main__":
    main()
