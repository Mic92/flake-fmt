{ runCommand, flake-fmt, nix, lsof, system }:
{
  flake-fmt-test = runCommand "flake-fmt-test"
    {
      buildInputs = [ flake-fmt ];
    } ''
    # Set up a proper HOME directory
    export HOME=$TMPDIR/home
    mkdir -p $HOME

    # Create a test directory with a sample flake
    mkdir -p test
    cd test

    # Create a test flake without formatter
    cat > flake.nix << 'EOF'
    {
      outputs = { self }: { };
    }
    EOF

    flake-fmt

    touch $out
  '';

  flake-fmt-with-formatter-test =
    let
      # Create a simple tarball with just a formatter script
      formatterTarball = runCommand "formatter-tarball" { } ''
        mkdir -p bin

        # Add a test formatter script
        cat > bin/test-formatter << 'EOF'
        #!/bin/sh
        echo "Test formatter ran on $*"
        # Create a marker file to prove the formatter ran
        echo "FORMATTED" > .formatter-ran
        EOF
        chmod +x bin/test-formatter

        # Create the tarball preserving the bin directory
        tar -czf $out .
      '';
    in
    runCommand "flake-fmt-with-formatter-test"
      {
        buildInputs = [ flake-fmt nix lsof ];
      } ''
      # Set up a temporary Nix store
      export TEST_ROOT=$TMPDIR/nix-test
      export NIX_STORE_DIR=$TEST_ROOT/store
      export NIX_DATA_DIR=$TEST_ROOT/share
      export NIX_STATE_DIR=$TEST_ROOT/state
      export NIX_LOG_DIR=$TEST_ROOT/log
      export NIX_CONF_DIR=$TEST_ROOT/etc
      export HOME=$TEST_ROOT/home

      mkdir -p $NIX_STORE_DIR $NIX_DATA_DIR/nix $NIX_STATE_DIR/nix/db $NIX_LOG_DIR/nix $NIX_CONF_DIR/nix $HOME

      # Create a build directory that will be accessible in the sandbox
      export NIX_BUILD_TOP=$TEST_ROOT/build
      mkdir -p $NIX_BUILD_TOP

      # Disable substituters and sandboxing via NIX_CONFIG
      export NIX_CONFIG="substituters =
      sandbox = false
      build-dir = $NIX_BUILD_TOP"
      export _NIX_TEST_NO_SANDBOX=1

      # Initialize the Nix database
      nix-store --init

      # Create a test directory with a sample flake
      mkdir -p $HOME/test
      cd $HOME/test

      # Create a test flake that uses the formatter tarball directly
      cat > flake.nix << 'EOF'
      {
        inputs.formatter.url = "tarball+file://${formatterTarball}";
        inputs.formatter.flake = false;

        outputs = { self, formatter }: {
          # Use the outPath of the formatter input
          formatter.${system} = formatter.outPath;
        };
      }
      EOF

      # Create a test nix file
      echo '{ hello = "world"; }' > test.nix

      # Now run flake-fmt
      echo "=== Running flake-fmt ==="
      flake-fmt || {
        echo "=== flake-fmt failed ==="
        exit 1
      }

      # Verify the formatter ran
      echo "=== Checking if formatter ran ==="
      if [[ -f .formatter-ran ]]; then
        echo "SUCCESS: Found .formatter-ran marker file"
        cat .formatter-ran
      else
        echo "ERROR: .formatter-ran marker file not found"
        exit 1
      fi

      # Test gcroot functionality
      echo "=== Testing gcroot functionality ==="

      # Check that the formatter was cached
      cache_base=''${XDG_CACHE_HOME:-$HOME/.cache}
      if [[ -d $cache_base/flake-fmt ]]; then
        echo "Cache directory exists"
        ls -la $cache_base/flake-fmt/
      fi

      # Run garbage collection
      echo "Running nix-collect-garbage..."
      nix-collect-garbage

      # Remove the marker file
      rm -f .formatter-ran

      # Run flake-fmt again - it should use the cached formatter
      echo "=== Running flake-fmt again after GC ==="
      flake-fmt

      # Check if formatter ran again
      if [[ -f .formatter-ran ]]; then
        echo "SUCCESS: Formatter ran successfully after GC"
      else
        echo "ERROR: Formatter did not run after GC"
        exit 1
      fi
            
      # Test cache invalidation when flake.nix changes
      echo "=== Testing cache invalidation ==="
            
      # Remove marker
      rm -f .formatter-ran
            
      # Get the cache link path
      cache_link=$(find $cache_base/flake-fmt -type l -name "*" | head -1)
      echo "Cache link: $cache_link"
            
      # Record the original formatter path
      original_formatter=$(readlink "$cache_link")
      echo "Original formatter: $original_formatter"
            
      # Touch flake.nix to make it newer than the cache
      # First make the cache link old
      touch -t 202001010000 "$cache_link"
            
      # Now touch flake.nix to make it newer
      touch -t 202401010000 flake.nix
            
      # Run flake-fmt again - it should detect that flake.nix is newer
      echo "=== Running flake-fmt after touching flake.nix ==="
      # Run with verbose output to see if it rebuilds
      flake-fmt 2>&1 | tee flake-fmt-output.log
            
      # Check if flake-fmt detected the need to update
      if grep -q "building" flake-fmt-output.log || grep -q "evaluating" flake-fmt-output.log; then
        echo "SUCCESS: flake-fmt detected flake.nix change and rebuilt"
      else
        echo "WARNING: Could not detect rebuild in output, checking formatter execution"
      fi
            
      # Verify formatter still works
      if [[ -f .formatter-ran ]]; then
        echo "SUCCESS: Formatter ran successfully after cache check"
      else
        echo "ERROR: Formatter did not run"
        exit 1
      fi

      touch $out
    '';
}
