#!/usr/bin/env bash
# Format Nix code using the project's formatter

set -euo pipefail

needsUpdate=0
currentSystem=$(nix --extra-experimental-features 'nix-command flakes' eval --raw --impure --expr builtins.currentSystem )

# Function to find the closest flake.nix
find_flake_root() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/flake.nix" ]]; then
            echo "$dir"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    # If no flake.nix found, use current directory
    echo "$PWD"
}

# Find the flake root
toplevel=$(find_flake_root)
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
    # Check if formatter exists for current system
    has_formatter_check="(val: val ? ${currentSystem})"
    
    if [[ $(nix --extra-experimental-features 'nix-command flakes' eval ".#formatter" --apply "$has_formatter_check" 2>/dev/null) != "true" ]]; then
        echo "Warning: No formatter defined for system ${currentSystem} in flake.nix" >&2
        exit 0
    fi
    
    # Formatter exists, build it
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
