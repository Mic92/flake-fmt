{
  description = "A smart formatter wrapper for Nix flakes";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages = {
          default = self.packages.${system}.flake-fmt;
          
          flake-fmt = pkgs.writeShellApplication {
            name = "flake-fmt";
            runtimeInputs = with pkgs; [ git coreutils ];
            text = builtins.readFile ./flake-fmt.sh;
          };
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            shellcheck
            git
          ];
        };
      });
}