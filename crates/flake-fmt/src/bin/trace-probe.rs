//! Test-only fixture binary used by integration tests to exercise the
//! `flake-fmt-trace` shim. Each argv entry is read and stat'd so we get a
//! deterministic set of `open`/`stat` syscalls under the controlled paths.

use std::env;
use std::fs;

fn main() {
    for arg in env::args().skip(1) {
        let _ = fs::metadata(&arg);
        let _ = fs::read(&arg);
    }
}
