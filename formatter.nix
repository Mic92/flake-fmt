{
  writeShellApplication,
  nixfmt-rs,
  fd,
  rustfmt,
}:

writeShellApplication {
  name = "formatter";
  runtimeInputs = [
    nixfmt-rs
    fd
    rustfmt
  ];
  text = ''
    # Format Nix files
    fd -e nix -x nixfmt {} \;

    # Format Rust files
    fd -e rs -x rustfmt --edition 2024 {} \;
  '';
}
