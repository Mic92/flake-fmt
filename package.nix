{ lib
, writeShellApplication
, coreutils
, nix
}:

writeShellApplication {
  name = "flake-fmt";
  runtimeInputs = [ coreutils nix ];
  text = builtins.readFile ./flake-fmt.sh;
}
