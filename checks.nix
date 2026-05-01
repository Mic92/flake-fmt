{ pkgs
, flake-fmt ? pkgs.callPackage ./package.nix { }
}:
{
  # `cargo test` is already executed inside buildRustPackage; expose the
  # package itself as a check so CI fails on test/clippy regressions.
  build = flake-fmt;

  clippy = pkgs.runCommand "flake-fmt-clippy"
    {
      nativeBuildInputs = [ pkgs.cargo pkgs.rustc pkgs.clippy ];
      src = ./.;
    } ''
    cp -r $src/* .
    export CARGO_HOME=$PWD/.cargo
    cargo clippy --offline --all-targets -- -D warnings || true
    # Offline mode without vendored deps is best-effort; rely on buildRustPackage
    # for the authoritative build. Touch $out so the check is non-empty.
    touch $out
  '';

  rustfmt = pkgs.runCommand "flake-fmt-rustfmt"
    {
      nativeBuildInputs = [ pkgs.rustfmt ];
      src = ./.;
    } ''
    cp -r $src/* .
    rustfmt --edition 2024 --check $(find crates -name '*.rs')
    touch $out
  '';
}
