# flake-fmt

Alternative to the `nix fmt` command that also does build and evaluation caching.

## Features

- **Smart caching**: Caches formatter evaluation and builds to avoid evaluation/rebuilding on every invocation
- **Sound invalidation**: Records every source file the flake eval actually reads (via an `LD_PRELOAD` / `DYLD_INSERT_LIBRARIES` shim) and rebuilds when any of them changes -- not just `flake.nix`/`flake.lock`

## Installation

### Using Nix flakes

```bash
nix profile install github:Mic92/flake-fmt
```

### In your flake

```nix
{
  inputs = {
    flake-fmt.url = "github:Mic92/flake-fmt";
  };

  outputs = { self, nixpkgs, flake-fmt, ... }: {
    # Your flake outputs...
  };
}
```

## Usage

Simply run `flake-fmt` in any Nix flake directory:

```bash
flake-fmt
```

Everything before `--` is passed to Nix commands, everything after is passed to the formatter.

```bash
flake-fmt -- --check
flake-fmt -- path/to/file.nix
# Pass --quiet to nix build/eval, and -v to the formatter
flake-fmt --quiet -- -v
```

### Cache invalidation

To force a rebuild of the formatter (ignoring the cache), set the `NO_CACHE` environment variable:

```bash
NO_CACHE=1 flake-fmt
```

### Debug logging

To understand why the formatter is being rebuilt, enable debug logging by setting `FLAKE_FMT_DEBUG` to `1`, `true`, `yes`, or `on`. This will show detailed information about cache validity checks, including file modification times and the exact reason for rebuilds:

```bash
FLAKE_FMT_DEBUG=1 flake-fmt
```

## Why not `nix fmt`?

The built-in `nix fmt` command has a significant issue: whenever it reformats the tree, Nix's own evaluation cache is invalidated.
This happens because formatting modifies files that Nix tracks, causing it to re-evaluate the entire flake on subsequent commands.
Additionally, formatters invoked by `nix fmt` have no garbage collection roots, meaning they can be removed during garbage collection and need to be rebuilt.

`flake-fmt` solves these problems by:
- Caching the formatter build separately from Nix's evaluation cache
- Not interfering with Nix's source tree tracking
- Maintaining persistent formatter builds that survive garbage collection

## How it works

1. **Find flake**: Locates the nearest `flake.nix` by walking up to a `.git` boundary.
2. **Check cache**: Looks for `~/.cache/flake-fmt/<hash>` (out-link) and `<hash>.deps` (sidecar of `<mtime>\t<path>` lines).
3. **Fast path**: If every recorded file's mtime is unchanged, exec the cached formatter without invoking nix at all.
4. **Slow path**: Spawn `nix build .#formatter.<system>` with the `flake-fmt-trace` shim preloaded; the shim records every `open`/`stat`/`access` whose path sits under the flake root. After nix exits, persist the deduped path list plus current mtimes to the sidecar.
5. **Execute formatter**: Run the binary from `<out-link>/bin` (preferring `treefmt`).

The out-link doubles as a Nix gcroot, so cached formatters survive garbage collection.

## Requirements

- Nix with flakes enabled
- A flake with a `formatter` output defined

## Example flake with formatter

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    treefmt-nix.url = "github:numtide/treefmt-nix";
  };

  outputs = { self, nixpkgs, treefmt-nix, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      treefmtEval = treefmt-nix.lib.evalModule pkgs {
        projectRootFile = "flake.nix";
        programs = {
          nixpkgs-fmt.enable = true;
          prettier.enable = true;
        };
      };
    in
    {
      formatter.${system} = treefmtEval.config.build.wrapper;
    };
}
```

## License

MIT
