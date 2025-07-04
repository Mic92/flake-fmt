# flake-fmt

A smart formatter wrapper for Nix flakes that automatically detects and uses your project's configured formatter.

## Features

- **Project-aware**: Automatically detects and uses the formatter defined in your flake
- **Smart caching**: Caches formatter evaluation and builds to avoid evaluation/rebuilding on every invocation
- **Automatic rebuilding**: Rebuilds the formatter only when `flake.nix` or `flake.lock` changes

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

Pass any arguments to the underlying formatter:

```bash
flake-fmt --check
flake-fmt path/to/file.nix
```

## How it works

1. First checks if `treefmt` is available globally and uses it directly if found
2. Otherwise, evaluates your flake to get the formatter for your current system
3. Builds the formatter using Nix and caches it in `.cache/flake-fmt/` with a hashed directory name
4. Executes the formatter with any arguments you passed

The formatter is only rebuilt when your `flake.nix` or `flake.lock` changes, making subsequent runs fast.

## Requirements

- Nix with flakes enabled
- Git (for detecting repository root and caching)
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
