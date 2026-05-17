# flake-fmt

A drop-in replacement for `nix fmt` that caches the formatter build and
skips Nix entirely when nothing relevant has changed. It records every
source file the flake evaluation reads (via an `LD_PRELOAD` /
`DYLD_INSERT_LIBRARIES` shim) and only rebuilds when one of those files
changes -- not just `flake.nix` and `flake.lock`.

## Installation

```bash
nix profile install github:Mic92/flake-fmt
```

Or as a flake input:

```nix
{
  inputs.flake-fmt.url = "github:Mic92/flake-fmt";
}
```

## Usage

Run `flake-fmt` anywhere inside a flake. Arguments before `--` go to the
nix commands, arguments after `--` go to the formatter:

```bash
flake-fmt
flake-fmt -- --check
flake-fmt -- path/to/file.nix
flake-fmt --quiet -- -v
```

Set `NO_CACHE=1` to force a rebuild. Set `FLAKE_FMT_DEBUG=1` to see why
the cache was (in)validated.

## Why not `nix fmt`?

`nix fmt` re-evaluates the flake on every run, and the act of formatting
invalidates Nix's own evaluation cache because it touches files Nix
tracks. The formatter derivation also has no gcroot, so it gets swept by
garbage collection and rebuilt over and over.

`flake-fmt` keeps its own out-link under `~/.cache/flake-fmt` (which
doubles as a gcroot) and decides whether to rebuild based on the mtimes
of the files the evaluation actually read.

## How it works

It walks up from the current directory to the nearest `flake.nix`
(stopping at a `.git` boundary) and hashes that path to find a cache
entry: an out-link plus a `.deps` sidecar of `<mtime>\t<path>` lines. If
every recorded mtime still matches, it execs the cached formatter
directly. Otherwise it runs `nix build .#formatter.<system>` with the
`flake-fmt-trace` shim preloaded, which records every `open`/`stat`/
`access` under the flake root, then writes the new sidecar and runs the
result. Inside the out-link it prefers a binary named `treefmt`,
otherwise it takes whatever is in `bin/`.

## Requirements

- Nix with flakes enabled
- A flake with a `formatter` output

A typical setup with [treefmt-nix](https://github.com/numtide/treefmt-nix):

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
        programs.nixfmt.enable = true;
        programs.prettier.enable = true;
      };
    in
    {
      formatter.${system} = treefmtEval.config.build.wrapper;
    };
}
```

## License

MIT
