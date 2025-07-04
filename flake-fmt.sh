#!/usr/bin/env bash
# Format Nix code using the project's formatter

set -euo pipefail

needsUpdate=0
currentSystem=$(nix eval --raw --impure --expr builtins.currentSystem)
toplevel=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
buildArgs=()
fmt="$toplevel/.git/flake-fmt"

if command -v treefmt &>/dev/null; then
    exec treefmt "$@"
fi

if [[ ! -d "$toplevel/.git/flake-fmt" ]]; then
    needsUpdate=1
elif [[ -n "$toplevel" ]]; then
    buildArgs+=("-o" "$toplevel"/.git/flake-fmt)
    referenceTime=$(stat -c %Y "$toplevel"/.git/flake-fmt)
    for file in flake.nix flake.lock; do
        if [[ -f "$file" ]] && [[ "$(stat -c %Y "$file")" -gt "$referenceTime" ]]; then
            needsUpdate=1
            break
        fi
    done
fi

if [[ "$needsUpdate" == 1 ]]; then
    fmt=$(nix build --out-link "$toplevel/.git/flake-fmt" --builders '' "${buildArgs[@]}" ".#formatter.${currentSystem}" --print-out-paths)
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