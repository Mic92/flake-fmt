{ lib
, writeShellApplication
, nixpkgs-fmt
, fd
, ruff
, mypy
}:

writeShellApplication {
  name = "formatter";
  runtimeInputs = [
    nixpkgs-fmt
    fd
    ruff
    mypy
  ];
  text = ''
    # Format Nix files
    fd -e nix -x nixpkgs-fmt {} \;
    
    # Format Python files
    fd -e py -x ruff format {} \;
    
    # Run ruff linter with auto-fix
    fd -e py -x ruff check --unsafe-fixes --fix {} \;

    # Type check with mypy
    mypy flake_fmt
  '';
}
