//! flake-fmt: cached `nix fmt`. Spawns `nix build` with the
//! `flake-fmt-trace` shim preloaded to record source files the eval reads,
//! persists their mtimes next to the out-link, and on the next run skips
//! nix entirely if every recorded mtime still matches.

use std::collections::BTreeSet;
use std::env;
use std::ffi::{CString, OsString, c_char};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::io::{AsRawFd, FromRawFd, IntoRawFd, RawFd};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

macro_rules! dbg_log {
    ($($arg:tt)*) => {
        if debug_enabled() {
            eprintln!("[DEBUG] {}", format_args!($($arg)*));
        }
    };
}

fn debug_enabled() -> bool {
    matches!(
        env::var("FLAKE_FMT_DEBUG").ok().as_deref(),
        Some("1" | "true" | "yes" | "on" | "TRUE" | "YES" | "ON")
    )
}

fn find_flake_root(start: &Path) -> Option<PathBuf> {
    let start = fs::canonicalize(start).ok()?;
    let mut cur: &Path = &start;
    loop {
        if cur.join("flake.nix").exists() {
            return Some(cur.to_path_buf());
        }
        if cur.join(".git").exists() {
            return None;
        }
        cur = cur.parent().filter(|p| *p != cur)?;
    }
}

fn parse_arguments(args: Vec<OsString>) -> (Vec<OsString>, Vec<OsString>) {
    let Some(idx) = args.iter().position(|a| a == "--") else {
        return (args, Vec::new());
    };
    let mut nix = args;
    let fmt = nix.split_off(idx).into_iter().skip(1).collect();
    (nix, fmt)
}

fn fnv1a64(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf2_9ce4_8422_2325u64, |h, &b| {
        (h ^ b as u64).wrapping_mul(0x0000_0100_0000_01b3)
    })
}

struct CachePaths {
    /// `nix build --out-link` target; doubles as a gcroot.
    out_link: PathBuf,
    /// `<mtime_ns>\t<path>` per tracked dep.
    deps: PathBuf,
}

fn cache_paths(toplevel: &Path) -> io::Result<CachePaths> {
    let cache_home = env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
        .or_else(|| {
            env::var_os("HOME")
                .filter(|h| !h.is_empty())
                .map(|h| PathBuf::from(h).join(".cache"))
        })
        .ok_or_else(|| io::Error::other("neither XDG_CACHE_HOME nor HOME is set"))?;
    let dir = cache_home.join("flake-fmt");
    let digest = format!("{:016x}", fnv1a64(toplevel.as_os_str().as_bytes()));
    Ok(CachePaths {
        out_link: dir.join(&digest),
        deps: dir.join(format!("{digest}.deps")),
    })
}

fn mtime_ns(meta: &fs::Metadata) -> i128 {
    meta.modified()
        .ok()
        .and_then(|m| m.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as i128)
        .unwrap_or(0)
}

fn cache_is_valid(cache: &CachePaths, toplevel: &Path) -> bool {
    if env::var_os("NO_CACHE").is_some_and(|v| !v.is_empty()) {
        dbg_log!("NO_CACHE set");
        return false;
    }
    if fs::symlink_metadata(&cache.out_link).is_err() {
        return false;
    }
    let Ok(file) = File::open(&cache.deps) else {
        return false;
    };

    let flake_nix = toplevel.join("flake.nix");
    let flake_lock = toplevel.join("flake.lock");
    let mut have_flake_nix = false;
    let mut have_flake_lock = false;
    let mut checked = 0usize;

    for line in BufReader::new(file).lines() {
        let Ok(line) = line else { return false };
        let Some((mtime_str, path)) = line.split_once('\t') else {
            continue;
        };
        let Ok(want) = mtime_str.parse::<i128>() else {
            return false;
        };
        let p = Path::new(path);
        let Ok(meta) = fs::metadata(p) else {
            dbg_log!("missing: {path}");
            return false;
        };
        if mtime_ns(&meta) != want {
            dbg_log!("changed: {path}");
            return false;
        }
        have_flake_nix |= p == flake_nix;
        have_flake_lock |= p == flake_lock;
        checked += 1;
    }

    // If the shim somehow missed the two files every flake eval must read,
    // refuse the fast path so a broken shim can't silently serve stale.
    if !have_flake_nix || !have_flake_lock {
        dbg_log!("flake.nix/flake.lock not in deps -- rebuild");
        return false;
    }
    dbg_log!("cache valid ({checked} files)");
    true
}

fn nix_command() -> Command {
    let mut c = Command::new("nix");
    c.args(["--extra-experimental-features", "flakes nix-command"]);
    c
}

fn current_system() -> io::Result<String> {
    let out = nix_command().args(["config", "show", "system"]).output()?;
    if !out.status.success() {
        io::stderr().write_all(&out.stderr).ok();
        return Err(io::Error::other("nix config show system failed"));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// 1. `FLAKE_FMT_TRACE_LIB` env (set by Nix wrapper).
/// 2. `<exe>/../lib/libflake_fmt_trace.{so,dylib}`.
fn locate_trace_lib() -> Option<PathBuf> {
    if let Some(p) = env::var_os("FLAKE_FMT_TRACE_LIB").map(PathBuf::from)
        && p.exists()
    {
        return Some(p);
    }
    let lib_dir = env::current_exe().ok()?.parent()?.parent()?.join("lib");
    let name = if cfg!(target_os = "macos") {
        "libflake_fmt_trace.dylib"
    } else {
        "libflake_fmt_trace.so"
    };
    let p = lib_dir.join(name);
    p.exists().then_some(p)
}

/// Returns `(store_path, tracked_deps)`. `tracked_deps` is empty when no
/// shim was available (degraded to flake.nix/flake.lock-only tracking).
fn build_formatter(
    toplevel: &Path,
    cache: &CachePaths,
    nix_args: &[OsString],
) -> io::Result<Option<(PathBuf, Vec<PathBuf>)>> {
    if let Some(parent) = cache.out_link.parent() {
        fs::create_dir_all(parent)?;
    }
    let target = format!(".#formatter.{}", current_system()?);
    let trace_lib = locate_trace_lib();

    // Trace channel: anonymous tempfile (created + unlinked, kept alive by
    // its fd) inherited into nix. After exit, lseek(0) + read.
    let trace_file = trace_lib
        .as_ref()
        .map(|_| open_trace_tempfile())
        .transpose()?;

    let mut cmd = nix_command();
    cmd.current_dir(toplevel)
        .args(["build", "--print-out-paths", "--out-link"])
        .arg(&cache.out_link)
        .args(["--builders", ""])
        .arg("--keep-failed")
        .args(nix_args)
        .arg(&target)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let (Some(lib), Some(tf)) = (&trace_lib, &trace_file) {
        let raw_fd = tf.as_raw_fd();
        // std sets O_CLOEXEC on opened files; clear it so nix inherits.
        clear_cloexec(raw_fd)?;
        cmd.env("FLAKE_FMT_TRACE_ROOT", toplevel)
            .env("FLAKE_FMT_TRACE_FD", raw_fd.to_string());
        if cfg!(target_os = "macos") {
            cmd.env("DYLD_INSERT_LIBRARIES", lib);
        } else {
            cmd.env("LD_PRELOAD", lib);
        }
    }

    let out = cmd.output()?;
    if !out.status.success() {
        io::stderr().write_all(&out.stderr).ok();
        if String::from_utf8_lossy(&out.stderr).contains("does not provide attribute") {
            eprintln!("Warning: No formatter defined");
            return Ok(None);
        }
        std::process::exit(out.status.code().unwrap_or(1));
    }
    let store_path = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
    let tracked = trace_file
        .map(|f| read_trace_fd(f, toplevel))
        .unwrap_or_default();
    Ok(Some((store_path, tracked)))
}

/// Append-only tempfile, unlinked immediately so contents die with the fd.
fn open_trace_tempfile() -> io::Result<File> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let path = env::temp_dir().join(format!(".flake-fmt-trace-{}.{nanos}", std::process::id()));
    let f = fs::OpenOptions::new()
        .read(true)
        .create_new(true)
        .append(true)
        .open(&path)?;
    let _ = fs::remove_file(&path);
    Ok(f)
}

fn clear_cloexec(fd: RawFd) -> io::Result<()> {
    // SAFETY: fcntl on a fd we own; the variadic decl matches the C ABI.
    let flags = unsafe { fcntl(fd, F_GETFD, 0) };
    if flags < 0 || unsafe { fcntl(fd, F_SETFD, flags & !FD_CLOEXEC) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

const F_GETFD: i32 = 1;
const F_SETFD: i32 = 2;
const FD_CLOEXEC: i32 = 1;
const SEEK_SET: i32 = 0;
unsafe extern "C" {
    // Variadic: aarch64-apple-darwin puts variadic args on the stack while
    // named args go in registers; a non-variadic decl would have libc read
    // `arg` from stack garbage instead of x2.
    fn fcntl(fd: RawFd, cmd: i32, ...) -> i32;
    fn lseek(fd: RawFd, offset: i64, whence: i32) -> i64;
    fn execv(path: *const c_char, argv: *const *const c_char) -> i32;
}

fn read_trace_fd(f: File, toplevel: &Path) -> Vec<PathBuf> {
    let raw = f.into_raw_fd();
    // SAFETY: rewinding a fd we own; ownership recovered below either way.
    if unsafe { lseek(raw, 0, SEEK_SET) } < 0 {
        let _ = unsafe { File::from_raw_fd(raw) };
        return Vec::new();
    }
    let file = unsafe { File::from_raw_fd(raw) };
    BufReader::new(file)
        .lines()
        .map_while(Result::ok)
        .map(PathBuf::from)
        .filter(|p| p.starts_with(toplevel) && !is_directory(p))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

/// Drop directory mtimes (change on unrelated writes). Component-based
/// noise filtering happens earlier in the trace shim.
fn is_directory(p: &Path) -> bool {
    matches!(fs::metadata(p), Ok(m) if m.is_dir())
}

/// Always includes `flake.nix`/`flake.lock`; see [`cache_is_valid`].
fn write_deps(cache: &CachePaths, toplevel: &Path, tracked: &[PathBuf]) -> io::Result<()> {
    let mut paths: BTreeSet<PathBuf> = tracked.iter().cloned().collect();
    paths.insert(toplevel.join("flake.nix"));
    let lock = toplevel.join("flake.lock");
    if lock.exists() {
        paths.insert(lock);
    }

    let tmp = cache.deps.with_extension("deps.tmp");
    {
        let mut w = BufWriter::new(File::create(&tmp)?);
        for p in &paths {
            let Ok(meta) = fs::metadata(p) else { continue };
            writeln!(w, "{}\t{}", mtime_ns(&meta), p.display())?;
        }
        w.flush()?;
    }
    fs::rename(&tmp, &cache.deps)?;
    dbg_log!("wrote {} deps", paths.len());
    Ok(())
}

fn find_executable(dir: &Path) -> Option<PathBuf> {
    let bin = dir.join("bin");
    let is_exec = |p: &Path| {
        fs::metadata(p)
            .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    };
    let treefmt = bin.join("treefmt");
    if is_exec(&treefmt) {
        return Some(treefmt);
    }
    let mut entries: Vec<_> = fs::read_dir(&bin).ok()?.flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    entries.into_iter().map(|e| e.path()).find(|p| is_exec(p))
}

fn execute_formatter(dir: &Path, fmt_args: &[OsString], toplevel: &Path) -> io::Result<()> {
    let exe = find_executable(dir)
        .ok_or_else(|| io::Error::other(format!("No executable in {}", dir.display())))?;
    env::set_current_dir(toplevel)?;
    let prog = CString::new(exe.as_os_str().as_bytes()).map_err(io::Error::other)?;
    let mut cargs: Vec<CString> = vec![prog.clone()];
    for a in fmt_args {
        cargs.push(CString::new(a.as_bytes()).map_err(io::Error::other)?);
    }
    let mut ptrs: Vec<*const c_char> = cargs.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(std::ptr::null());
    // SAFETY: prog and ptrs[..] are valid NUL-terminated C strings; the
    // pointer array is null-terminated. execv only returns on error.
    unsafe { execv(prog.as_ptr(), ptrs.as_ptr()) };
    Err(io::Error::last_os_error())
}

fn run(argv: Vec<OsString>) -> io::Result<ExitCode> {
    let (nix_args, fmt_args) = parse_arguments(argv);
    let toplevel = find_flake_root(&env::current_dir()?)
        .ok_or_else(|| io::Error::other("No flake.nix found"))?;
    let cache = cache_paths(&toplevel)?;

    let target_dir = if cache_is_valid(&cache, &toplevel) {
        cache.out_link.clone()
    } else {
        let Some((store, tracked)) = build_formatter(&toplevel, &cache, &nix_args)? else {
            return Ok(ExitCode::from(0));
        };
        if let Err(e) = write_deps(&cache, &toplevel, &tracked) {
            eprintln!("Warning: failed to write deps: {e}");
        }
        store
    };

    execute_formatter(&target_dir, &fmt_args, &toplevel)?;
    Ok(ExitCode::from(0))
}

fn main() -> ExitCode {
    match run(env::args_os().skip(1).collect()) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error: {e}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_args_on_dash_dash() {
        let a: Vec<OsString> = ["--quiet", "--", "-v", "f.nix"]
            .iter()
            .map(OsString::from)
            .collect();
        let (n, f) = parse_arguments(a);
        assert_eq!(n, vec![OsString::from("--quiet")]);
        assert_eq!(f, vec![OsString::from("-v"), OsString::from("f.nix")]);
    }
}
