{ pkgs
}:
{
  pytest = pkgs.runCommand "flake-fmt-pytest"
    {
      nativeBuildInputs = [
        pkgs.python3.pkgs.pytest
        pkgs.git
        pkgs.nix
      ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Run pytest
    pytest test_flake_fmt.py -v
    
    touch $out
  '';

  ruff = pkgs.runCommand "flake-fmt-ruff"
    {
      nativeBuildInputs = [ pkgs.ruff ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Run ruff format check
    echo "Running ruff format check..."
    ruff format --check flake_fmt test_flake_fmt.py
    
    # Run ruff lint check
    echo "Running ruff lint check..."
    ruff check flake_fmt test_flake_fmt.py
    
    touch $out
  '';

  mypy = pkgs.runCommand "flake-fmt-mypy"
    {
      nativeBuildInputs = [ pkgs.mypy ];
      src = ./.;
    } ''
    # Copy source files
    cp -r $src/* .
    
    # Run mypy
    echo "Running mypy type check..."
    mypy flake_fmt
    
    touch $out
  '';
}

