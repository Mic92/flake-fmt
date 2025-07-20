{ pkgs
, flake-fmt
, self
,
}:
let
  pythonWithPytest = pkgs.python3.withPackages (ps: [ ps.pytest ]);
  pythonWithTools = pkgs.python3.withPackages (ps: [ ps.ruff ps.mypy ]);
in
{
  pytest = pkgs.runCommand "flake-fmt-pytest"
    {
      buildInputs = [
        pythonWithPytest
        pkgs.git
        pkgs.nix
      ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Make sure we have a proper PATH
    export PATH="${pkgs.git}/bin:${pkgs.nix}/bin:$PATH"
    
    # Run pytest
    python -m pytest test_flake_fmt.py -v
    
    touch $out
  '';

  ruff = pkgs.runCommand "flake-fmt-ruff"
    {
      buildInputs = [ pythonWithTools ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Run ruff format check
    echo "Running ruff format check..."
    python -m ruff format --check flake_fmt test_flake_fmt.py
    
    # Run ruff lint check
    echo "Running ruff lint check..."
    python -m ruff check flake_fmt test_flake_fmt.py
    
    touch $out
  '';

  mypy = pkgs.runCommand "flake-fmt-mypy"
    {
      buildInputs = [ pythonWithTools ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Run mypy
    echo "Running mypy type check..."
    python -m mypy flake_fmt
    
    touch $out
  '';
}

