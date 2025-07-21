{ lib
, python3
, nix
}:

python3.pkgs.buildPythonApplication {
  pname = "flake-fmt";
  version = "0.1.0";

  src = ./.;

  format = "pyproject";

  nativeBuildInputs = with python3.pkgs; [
    setuptools
  ];

  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    "${lib.makeBinPath [ nix ]}"
  ];

  meta = with lib; {
    description = "A smart formatter wrapper for Nix flakes with caching";
    homepage = "https://github.com/Mic92/flake-fmt";
    license = licenses.mit;
    mainProgram = "flake-fmt";
  };
}
