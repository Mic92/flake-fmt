//! End-to-end test for the `flake-fmt-trace` interposer.
//!
//! Spawns the `trace-probe` helper binary with the shim preloaded, points it
//! at a controlled tempdir, and asserts that the inherited trace fd received
//! the expected paths -- and only those, i.e. paths outside the configured
//! root must be filtered out.
//!
//! No third-party deps. The test depends on `cargo test --workspace` (or
//! `buildRustPackage`'s default workspace build) having already produced
//! `target/<profile>/libflake_fmt_trace.{so,dylib}` next to the test binary.

use std::env;
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::Command;

const DYLIB_EXT: &str = if cfg!(target_os = "macos") {
    "dylib"
} else {
    "so"
};

/// Locate the cdylib next to the test binary. cargo places workspace artifacts
/// under `target/<profile>/`; the test binary lives at
/// `target/<profile>/deps/<name>-<hash>`, so two `parent()` calls reach the
/// profile dir.
fn locate_shim() -> PathBuf {
    let exe = env::current_exe().expect("test exe path");
    let profile_dir = exe
        .parent()
        .and_then(Path::parent)
        .expect("target/<profile>");
    let p = profile_dir.join(format!("libflake_fmt_trace.{DYLIB_EXT}"));
    if !p.exists() {
        // `cargo test -p flake-fmt` does not build sibling workspace cdylibs;
        // bootstrap it explicitly so the test is hermetic.
        let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
        let profile = if profile_dir.ends_with("release") {
            "--release"
        } else {
            "--profile=dev"
        };
        let status = Command::new(&cargo)
            .args(["build", "-p", "flake-fmt-trace", profile])
            .status()
            .expect("spawn cargo build");
        assert!(status.success(), "cargo build flake-fmt-trace failed");
    }
    assert!(p.exists(), "trace shim still missing at {}", p.display());
    p
}

/// Open an anonymous append-only tempfile and clear `CLOEXEC` so a child
/// inherits the fd. Mirrors the same setup the real CLI does before spawning
/// nix.
fn open_inheritable_trace() -> (fs::File, i32) {
    let path = env::temp_dir().join(format!(
        "flake-fmt-trace-test-{}.{}",
        std::process::id(),
        rand_suffix()
    ));
    let f = fs::OpenOptions::new()
        .read(true)
        .create_new(true)
        .append(true)
        .open(&path)
        .expect("open tempfile");
    let _ = fs::remove_file(&path);

    let fd = f.as_raw_fd();
    // SAFETY: fcntl on an owned fd; clear FD_CLOEXEC so children inherit.
    unsafe {
        let flags = fcntl(fd, F_GETFD, 0);
        assert!(flags >= 0, "fcntl(F_GETFD) failed");
        assert!(
            fcntl(fd, F_SETFD, flags & !FD_CLOEXEC) >= 0,
            "fcntl(F_SETFD) failed"
        );
    }
    (f, fd)
}

const F_GETFD: i32 = 1;
const F_SETFD: i32 = 2;
const FD_CLOEXEC: i32 = 1;
unsafe extern "C" {
    // Variadic so the call site emits the proper ABI on aarch64-apple-darwin
    // (Apple puts variadic args on the stack, named args in registers).
    fn fcntl(fd: i32, cmd: i32, ...) -> i32;
}

fn rand_suffix() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

#[test]
fn shim_logs_paths_under_root_only() {
    let shim = locate_shim();
    let probe = env!("CARGO_BIN_EXE_trace-probe");

    // Layout:
    //   root/a.txt           -- expected logged
    //   root/b.txt           -- expected logged
    //   root/.git/HEAD       -- expected filtered (noise component)
    //   /tmp/.../outside.txt -- expected filtered (outside root)
    let root = env::temp_dir().join(format!("flake-fmt-shim-it-{}", rand_suffix()));
    fs::create_dir_all(root.join(".git")).unwrap();
    let inside_a = root.join("a.txt");
    let inside_b = root.join("b.txt");
    let noise = root.join(".git/HEAD");
    fs::File::create(&inside_a)
        .unwrap()
        .write_all(b"a")
        .unwrap();
    fs::File::create(&inside_b)
        .unwrap()
        .write_all(b"b")
        .unwrap();
    fs::File::create(&noise).unwrap().write_all(b"x").unwrap();
    let outside = env::temp_dir().join(format!("flake-fmt-shim-outside-{}.txt", rand_suffix()));
    fs::File::create(&outside).unwrap().write_all(b"c").unwrap();

    let (mut trace_file, trace_fd) = open_inheritable_trace();

    let mut cmd = Command::new(probe);
    cmd.arg(&inside_a)
        .arg(&inside_b)
        .arg(&noise)
        .arg(&outside)
        .env("FLAKE_FMT_TRACE_ROOT", &root)
        .env("FLAKE_FMT_TRACE_FD", trace_fd.to_string());
    if cfg!(target_os = "macos") {
        cmd.env("DYLD_INSERT_LIBRARIES", &shim);
    } else {
        cmd.env("LD_PRELOAD", &shim);
    }
    let status = cmd.status().expect("spawn trace-probe");
    assert!(status.success(), "trace-probe exited {status}");

    // Rewind and read what the shim wrote through fd inheritance.
    trace_file.seek(SeekFrom::Start(0)).unwrap();
    let mut log = String::new();
    trace_file.read_to_string(&mut log).unwrap();
    let logged: std::collections::BTreeSet<&str> = log.lines().collect();

    let a = inside_a.to_str().unwrap();
    let b = inside_b.to_str().unwrap();
    let n = noise.to_str().unwrap();
    let o = outside.to_str().unwrap();

    assert!(logged.contains(a), "expected {a} in trace, got: {logged:?}");
    assert!(logged.contains(b), "expected {b} in trace, got: {logged:?}");
    assert!(
        !logged.contains(n),
        "noise path {n} (.git component) must be filtered, got: {logged:?}"
    );
    assert!(
        !logged.contains(o),
        "outside path {o} must be filtered, got: {logged:?}"
    );

    // Cleanup files we created. Tempdir is best-effort.
    let _ = fs::remove_dir_all(&root);
    let _ = fs::remove_file(&outside);
}
