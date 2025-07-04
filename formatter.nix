{ lib
, writeShellApplication
, nixpkgs-fmt
, shfmt
, shellcheck
, fd
}:

writeShellApplication {
  name = "formatter";
  runtimeInputs = [ nixpkgs-fmt shfmt shellcheck fd ];
  text = ''
    # Format Nix files
    fd -e nix -x nixpkgs-fmt {} \;

    # Format shell scripts
    fd -e sh -x shfmt -w {} \;

    # Check shell scripts
    fd -e sh -x shellcheck {} \;
  '';
}
