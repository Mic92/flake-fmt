{ lib
, stdenv
, rustPlatform
, makeWrapper
, nix
}:

rustPlatform.buildRustPackage {
  pname = "flake-fmt";
  version = "0.2.0";

  src = ./.;
  cargoLock.lockFile = ./Cargo.lock;

  nativeBuildInputs = [ makeWrapper ];

  # buildRustPackage installs binaries by default but skips cdylibs. The
  # trace shim is consumed at runtime by the flake-fmt binary, so install it
  # under $out/lib and tell the wrapper where to find it via env var.
  postInstall =
    let
      ext = if stdenv.hostPlatform.isDarwin then "dylib" else "so";
    in
    ''
      install -Dm0644 \
        target/${stdenv.hostPlatform.rust.cargoShortTarget}/release/libflake_fmt_trace.${ext} \
        $out/lib/libflake_fmt_trace.${ext}
    '';

  postFixup =
    let
      ext = if stdenv.hostPlatform.isDarwin then "dylib" else "so";
    in
    ''
      wrapProgram $out/bin/flake-fmt \
        --prefix PATH : ${lib.makeBinPath [ nix ]} \
        --set-default FLAKE_FMT_TRACE_LIB $out/lib/libflake_fmt_trace.${ext}
    '';

  meta = with lib; {
    description = "Smart formatter wrapper for Nix flakes with sound caching";
    homepage = "https://github.com/Mic92/flake-fmt";
    license = licenses.mit;
    mainProgram = "flake-fmt";
  };
}
