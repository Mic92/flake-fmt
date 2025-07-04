#!/usr/bin/env bash
# Format Nix code using the project's formatter

set -euo pipefail

needsUpdate=0
currentSystem=$(nix --extra-experimental-features 'nix-command flakes' eval --raw --impure --expr builtins.currentSystem )
toplevel=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
buildArgs=()
# Escape the directory name for safe use in cache path
escaped_toplevel=$(printf '%s' "$toplevel" | sha256sum | cut -d' ' -f1)
cache_dir="$toplevel/.cache/flake-fmt"
fmt="$cache_dir/$escaped_toplevel"

if [[ ! -d "$fmt" ]]; then
    needsUpdate=1
    mkdir -p "$cache_dir"
elif [[ -n "$toplevel" ]]; then
    buildArgs+=("-o" "$fmt")
    referenceTime=$(stat -c %Y "$fmt")
    for file in flake.nix flake.lock; do
        if [[ -f "$file" ]] && [[ "$(stat -c %Y "$file")" -gt "$referenceTime" ]]; then
            needsUpdate=1
            break
        fi
    done
fi

if [[ "$needsUpdate" == 1 ]]; then
    fmt=$(nix --extra-experimental-features 'nix-command flakes' build --print-out-paths --out-link "$fmt" --builders '' "${buildArgs[@]}" ".#formatter.${currentSystem}" )
fi

# treefmt has multiple outputs
if [[ -x "$fmt/bin/treefmt" ]]; then
    exec "$fmt/bin/treefmt" "$@"
fi

for file in "$fmt/bin/"*; do
    # shellcheck disable=SC2068
    exec "$file" "$@"
done
echo "No formatter found in $fmt/bin"
exit 1
