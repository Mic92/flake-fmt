#!/usr/bin/env python3
"""A smart formatter wrapper for Nix flakes with caching."""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

# Create module-specific logger
logger = logging.getLogger(__name__)


class NixCommandError(Exception):
    """Exception raised when a nix command fails."""

    def __init__(self, cmd: list[str], returncode: int, stdout: str, stderr: str) -> None:
        """Initialize NixCommandError with command details and output.

        Args:
            cmd: The nix command that was executed
            returncode: The exit code returned by the command
            stdout: The standard output from the command
            stderr: The standard error output from the command

        """
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Nix command failed with exit code {returncode}: {' '.join(cmd)}\nstderr: {stderr}")


def find_flake_root(start_path: Path | None = None) -> Path | None:
    """Find the nearest flake.nix by searching up the directory tree.

    Stops at git repository boundaries (.git directory).
    """
    if start_path is None:
        start_path = Path.cwd()
    current = start_path.resolve()
    logger.debug("Searching for flake.nix starting from: %s", current)

    while current != current.parent:
        flake_path = current / "flake.nix"
        if flake_path.exists():
            logger.debug("Found flake.nix at: %s", current)
            return current

        # Stop at git repository boundaries
        if (current / ".git").exists():
            logger.debug("Stopped at git boundary: %s", current)
            return None

        current = current.parent

    # Check root directory
    flake_path = current / "flake.nix"
    if flake_path.exists():
        logger.debug("Found flake.nix at root: %s", current)
        return current

    logger.debug("No flake.nix found")
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
    cmd = ["nix", "--extra-experimental-features", "flakes nix-command", *args]

    if capture_output:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd, shell=False)
        if result.returncode != 0:
            raise NixCommandError(cmd, result.returncode, result.stdout, result.stderr)
        return result.stdout.strip()
    return subprocess.run(cmd, check=False, cwd=cwd, shell=False)


def get_current_system() -> str:
    """Get the current system identifier."""
    result = run_nix(["config", "show", "system"])
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
    logger.debug("Cache directory: %s", cache_dir)
    logger.debug("Formatter cache path: %s", fmt_cache_path)
    logger.debug("Cache path hash (from %s): %s", toplevel, escaped_toplevel)
    return cache_dir, fmt_cache_path


def check_cache_validity(fmt_cache_path: Path, toplevel: Path) -> tuple[bool, list[str]]:
    """Check if cache needs update and return build args."""
    needs_update = False
    build_args = []

    if not fmt_cache_path.exists():
        logger.debug("Cache does not exist at %s, needs rebuild", fmt_cache_path)
        needs_update = True
    elif os.environ.get("NO_CACHE"):
        logger.debug("NO_CACHE environment variable set, forcing rebuild")
        needs_update = True
    else:
        build_args.extend(["-o", str(fmt_cache_path)])
        reference_time = fmt_cache_path.stat().st_mtime
        logger.debug("Cache exists, last modified: %s", reference_time)

        # Check if flake.nix or flake.lock are newer
        for file in ["flake.nix", "flake.lock"]:
            file_path = toplevel / file
            if file_path.exists():
                file_mtime = file_path.stat().st_mtime
                logger.debug("%s last modified: %s", file, file_mtime)
                if file_mtime > reference_time:
                    logger.debug("%s is newer than cache (%s > %s), needs rebuild", file, file_mtime, reference_time)
                    needs_update = True
                    break
            else:
                logger.debug("%s does not exist", file)

    logger.debug("Cache validity check result: needs_update=%s", needs_update)
    return needs_update, build_args


def build_formatter(
    toplevel: Path,
    current_system: str,
    fmt_cache_path: Path,
    build_args: list[str],
    nix_args: list[str],
) -> Path:
    """Build the formatter if needed."""
    logger.debug("Building formatter for system: %s", current_system)

    # Check if formatter exists for current system
    has_formatter_check = f"(val: val ? {current_system})"
    try:
        logger.debug("Checking if formatter exists for %s", current_system)
        result = run_nix(["eval", ".#formatter", "--apply", has_formatter_check, *nix_args], cwd=toplevel)
        if not isinstance(result, str):
            msg = "Expected string result from nix eval"
            raise TypeError(msg)

        if result != "true":
            logger.debug("No formatter defined for current system")
            print("Warning: No formatter defined", file=sys.stderr)
            sys.exit(0)
    except NixCommandError as e:
        # If the formatter attribute doesn't exist at all, nix will error
        if "does not provide attribute" in e.stderr:
            logger.debug("Formatter attribute does not exist")
            print("Warning: No formatter defined", file=sys.stderr)
            sys.exit(0)
        raise

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
    logger.debug("Running build command: nix %s", " ".join(build_cmd))
    fmt_path = run_nix(build_cmd, cwd=toplevel)
    if not isinstance(fmt_path, str):
        msg = "Expected string result from nix build"
        raise TypeError(msg)
    result_path = Path(fmt_path.strip())
    logger.debug("Formatter built successfully at: %s", result_path)
    return result_path


def execute_formatter(fmt_cache_path: Path, formatter_args: list[str], toplevel: Path) -> None:
    """Execute the formatter."""
    # Check for treefmt first (it has multiple outputs)
    treefmt_path = fmt_cache_path / "bin" / "treefmt"
    if treefmt_path.exists() and os.access(treefmt_path, os.X_OK):
        logger.debug("Executing treefmt: %s with args: %s", treefmt_path, formatter_args)
        result = subprocess.run([str(treefmt_path), *formatter_args], check=False, cwd=toplevel, shell=False)
        sys.exit(result.returncode)

    # Find any executable in bin directory
    bin_dir = fmt_cache_path / "bin"
    if bin_dir.exists():
        logger.debug("Checking for executables in: %s", bin_dir)
        for file_path in bin_dir.iterdir():
            if file_path.is_file() and os.access(file_path, os.X_OK):
                logger.debug("Executing formatter: %s with args: %s", file_path, formatter_args)
                result = subprocess.run([str(file_path), *formatter_args], check=False, cwd=toplevel, shell=False)
                sys.exit(result.returncode)

    # If no executable found, the formatter itself might be executable
    if os.access(fmt_cache_path, os.X_OK):
        logger.debug("Executing formatter directly: %s with args: %s", fmt_cache_path, formatter_args)
        result = subprocess.run([str(fmt_cache_path), *formatter_args], check=False, cwd=toplevel, shell=False)
        sys.exit(result.returncode)

    logger.error("No executable formatter found at %s", fmt_cache_path)
    sys.exit(1)


def main(args: list[str] | None = None) -> None:
    """Run the flake formatter with caching."""
    # Set up logging
    log_level = os.environ.get("FLAKE_FMT_DEBUG", "").lower()
    if log_level in ["1", "true", "yes", "on"]:
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
        logger.debug("Debug logging enabled via FLAKE_FMT_DEBUG environment variable")

    try:
        # Parse arguments
        if args is None:
            args = sys.argv[1:]
        nix_args, formatter_args = parse_arguments(args)
        logger.debug("Nix args: %s", nix_args)
        logger.debug("Formatter args: %s", formatter_args)

        # Find flake root
        toplevel = find_flake_root()
        if toplevel is None:
            print("No flake.nix found", file=sys.stderr)
            sys.exit(1)
        logger.debug("Flake root: %s", toplevel)

        # Get current system
        current_system = get_current_system()
        logger.debug("Current system: %s", current_system)

        # Set up cache
        cache_dir, fmt_cache_path = get_cache_path(toplevel)

        # Check cache validity
        needs_update, build_args = check_cache_validity(fmt_cache_path, toplevel)

        if needs_update:
            logger.debug("Triggering formatter rebuild")
            cache_dir.mkdir(parents=True, exist_ok=True)
            fmt_cache_path = build_formatter(toplevel, current_system, fmt_cache_path, build_args, nix_args)
        else:
            logger.debug("Using cached formatter")

        # Execute formatter
        logger.debug("Executing formatter from: %s", fmt_cache_path)
        execute_formatter(fmt_cache_path, formatter_args, toplevel)
    except NixCommandError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
