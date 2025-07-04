{
  description = "A smart formatter wrapper for Nix flakes";

  inputs.nixpkgs.url = "git+https://github.com/NixOS/nixpkgs?shallow=1&ref=nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = self.packages.${system}.flake-fmt;
          flake-fmt = pkgs.callPackage ./package.nix { };
        });

      formatter = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.callPackage ./formatter.nix { }
      );

      checks = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          checkSet = pkgs.callPackage ./checks.nix {
            inherit system;
            flake-fmt = self.packages.${system}.flake-fmt;
            inherit (pkgs) nix lsof;
          };
        in
        {
          inherit (checkSet) flake-fmt-test flake-fmt-with-formatter-test;
        }
      );
    };
}
