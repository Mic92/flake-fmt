//! libc interposer that logs file paths under a configured root to an
//! inherited fd. Loaded via `LD_PRELOAD` (Linux) or `DYLD_INSERT_LIBRARIES`
//! + `__DATA,__interpose` tuples (macOS).
//!
//! Env contract:
//! * `FLAKE_FMT_TRACE_ROOT` -- absolute prefix; only matching paths are logged.
//! * `FLAKE_FMT_TRACE_FD`   -- writable fd opened `O_APPEND` by the parent.
//!
//! On lib load (constructor) the shim **unsets** `LD_PRELOAD` /
//! `DYLD_INSERT_LIBRARIES` from its own environment and sets `FD_CLOEXEC`
//! on the trace fd. Effect: the shim is active in the nix client itself
//! (where eval reads source files) but does *not* propagate to its forked
//! helpers (substituters, tar, ssh, ...). Less noise, less overhead, no
//! cross-process write contention to worry about.
//!
//! The shim never opens or stats anything itself, so it cannot recurse into
//! its own hooks.

#![allow(clippy::missing_safety_doc)]

use std::ffi::{CStr, c_char, c_int, c_void};
use std::os::unix::ffi::OsStringExt;
use std::sync::OnceLock;

unsafe extern "C" {
    fn write(fd: c_int, buf: *const c_void, count: usize) -> isize;
    fn unsetenv(name: *const c_char) -> c_int;
    // Variadic: see flake-fmt's main.rs for the aarch64-apple-darwin ABI rationale.
    fn fcntl(fd: c_int, cmd: c_int, ...) -> c_int;
}

const F_SETFD: c_int = 2;
const FD_CLOEXEC: c_int = 1;

// Runs at dlopen, before `main`. Cuts the shim out of every descendant of
// the host: descendants don't load the shim (preload env unset), and
// don't inherit the trace fd (FD_CLOEXEC). Single-threaded at lib-load,
// so direct env mutation is safe.
extern "C" fn flake_fmt_trace_init() {
    // SAFETY: NUL-terminated C string literals; no concurrent env access.
    unsafe {
        unsetenv(c"LD_PRELOAD".as_ptr());
        unsetenv(c"DYLD_INSERT_LIBRARIES".as_ptr());
        unsetenv(c"DYLD_FORCE_FLAT_NAMESPACE".as_ptr());
    }
    if let Some(fd) = log_fd() {
        // SAFETY: fcntl on a parent-inherited fd; variadic decl matches C ABI.
        unsafe { fcntl(fd, F_SETFD, FD_CLOEXEC) };
    }
}

#[used]
#[cfg_attr(target_os = "linux", unsafe(link_section = ".init_array"))]
#[cfg_attr(target_os = "macos", unsafe(link_section = "__DATA,__mod_init_func"))]
static INIT: extern "C" fn() = flake_fmt_trace_init;

fn root_prefix() -> Option<&'static [u8]> {
    static ROOT: OnceLock<Option<Vec<u8>>> = OnceLock::new();
    ROOT.get_or_init(|| {
        let v = std::env::var_os("FLAKE_FMT_TRACE_ROOT")?;
        let mut bytes = v.into_vec();
        while bytes.last() == Some(&b'/') && bytes.len() > 1 {
            bytes.pop();
        }
        Some(bytes)
    })
    .as_deref()
}

fn log_fd() -> Option<c_int> {
    static FD: OnceLock<Option<c_int>> = OnceLock::new();
    *FD.get_or_init(|| {
        let v = std::env::var_os("FLAKE_FMT_TRACE_FD")?;
        v.to_str()?
            .trim()
            .parse::<c_int>()
            .ok()
            .filter(|fd| *fd >= 0)
    })
}

// Path components that never affect flake evaluation. Filtering here
// instead of in the CLI keeps the trace log small.
const NOISE: &[&[u8]] = &[
    b".git",
    b".direnv",
    b".cache",
    b".envrc",
    b"target",
    b"node_modules",
    b"result",
    b".pytest_cache",
    b".mypy_cache",
    b".ruff_cache",
    b"__pycache__",
];

fn has_noise_component(rel: &[u8]) -> bool {
    for comp in rel.split(|&b| b == b'/') {
        if comp.is_empty() {
            continue;
        }
        if comp.starts_with(b"result-") || NOISE.iter().any(|n| *n == comp) {
            return true;
        }
    }
    false
}

unsafe fn maybe_log(path: *const c_char) {
    if path.is_null() {
        return;
    }
    let Some(root) = root_prefix() else { return };
    let Some(fd) = log_fd() else { return };

    // SAFETY: null was checked; libc passes valid C strings to its hooks.
    let bytes = unsafe { CStr::from_ptr(path) }.to_bytes();
    if !bytes.starts_with(b"/") || !bytes.starts_with(root) {
        return;
    }
    if bytes.len() != root.len() && bytes.get(root.len()) != Some(&b'/') {
        return;
    }
    if has_noise_component(&bytes[root.len()..]) {
        return;
    }
    let mut buf: Vec<u8> = Vec::with_capacity(bytes.len() + 1);
    buf.extend_from_slice(bytes);
    buf.push(b'\n');
    // SAFETY: writing to a parent-supplied fd; ptr/len cover a valid slice.
    let _ = unsafe { write(fd, buf.as_ptr().cast(), buf.len()) };
}

// Linux resolves the real symbol via `dlsym(RTLD_NEXT)`. macOS uses a
// plain extern -- dyld's `__interpose` table redirects only external
// callers, so forwarding does not recurse.

unsafe fn do_open(path: *const c_char, flags: c_int, mode: c_int) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_open()(path, flags, mode)
    }
}
unsafe fn do_openat(dirfd: c_int, path: *const c_char, flags: c_int, mode: c_int) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_openat()(dirfd, path, flags, mode)
    }
}
unsafe fn do_stat(path: *const c_char, buf: *mut c_void) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_stat()(path, buf)
    }
}
unsafe fn do_lstat(path: *const c_char, buf: *mut c_void) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_lstat()(path, buf)
    }
}
unsafe fn do_fstatat(dirfd: c_int, path: *const c_char, buf: *mut c_void, flags: c_int) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_fstatat()(dirfd, path, buf, flags)
    }
}
unsafe fn do_access(path: *const c_char, mode: c_int) -> c_int {
    unsafe {
        maybe_log(path);
        platform::real_access()(path, mode)
    }
}

type OpenFn = unsafe extern "C" fn(*const c_char, c_int, c_int) -> c_int;
type OpenatFn = unsafe extern "C" fn(c_int, *const c_char, c_int, c_int) -> c_int;
type StatFn = unsafe extern "C" fn(*const c_char, *mut c_void) -> c_int;
type FstatatFn = unsafe extern "C" fn(c_int, *const c_char, *mut c_void, c_int) -> c_int;
type AccessFn = unsafe extern "C" fn(*const c_char, c_int) -> c_int;

#[cfg(target_os = "linux")]
mod platform {
    use super::*;

    #[link(name = "dl")]
    unsafe extern "C" {
        fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    }
    const RTLD_NEXT: *mut c_void = -1isize as *mut c_void;

    macro_rules! resolve {
        ($cell:ident, $ty:ty, $name:expr) => {{
            static $cell: OnceLock<usize> = OnceLock::new();
            let addr = *$cell.get_or_init(|| {
                let n: &CStr = $name;
                // SAFETY: dlsym with valid C string + RTLD_NEXT pseudo-handle.
                unsafe { dlsym(RTLD_NEXT, n.as_ptr()) as usize }
            });
            // SAFETY: address is the libc symbol whose signature is `$ty`.
            unsafe { core::mem::transmute::<usize, $ty>(addr) }
        }};
    }

    pub(super) fn real_open() -> OpenFn {
        resolve!(R_OPEN, OpenFn, c"open")
    }
    pub(super) fn real_openat() -> OpenatFn {
        resolve!(R_OPENAT, OpenatFn, c"openat")
    }
    pub(super) fn real_stat() -> StatFn {
        resolve!(R_STAT, StatFn, c"stat")
    }
    pub(super) fn real_lstat() -> StatFn {
        resolve!(R_LSTAT, StatFn, c"lstat")
    }
    pub(super) fn real_fstatat() -> FstatatFn {
        resolve!(R_FSTATAT, FstatatFn, c"fstatat")
    }
    pub(super) fn real_access() -> AccessFn {
        resolve!(R_ACCESS, AccessFn, c"access")
    }

    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn open(path: *const c_char, flags: c_int, mode: c_int) -> c_int {
        unsafe { super::do_open(path, flags, mode) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn open64(path: *const c_char, flags: c_int, mode: c_int) -> c_int {
        unsafe { super::do_open(path, flags, mode) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn openat(d: c_int, path: *const c_char, f: c_int, m: c_int) -> c_int {
        unsafe { super::do_openat(d, path, f, m) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn openat64(d: c_int, path: *const c_char, f: c_int, m: c_int) -> c_int {
        unsafe { super::do_openat(d, path, f, m) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn stat(path: *const c_char, buf: *mut c_void) -> c_int {
        unsafe { super::do_stat(path, buf) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn lstat(path: *const c_char, buf: *mut c_void) -> c_int {
        unsafe { super::do_lstat(path, buf) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn fstatat(
        d: c_int,
        path: *const c_char,
        buf: *mut c_void,
        f: c_int,
    ) -> c_int {
        unsafe { super::do_fstatat(d, path, buf, f) }
    }
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn access(path: *const c_char, mode: c_int) -> c_int {
        unsafe { super::do_access(path, mode) }
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;

    // open/openat are variadic; on aarch64-apple-darwin variadic args go on
    // the stack while named args go in registers, so a non-variadic decl
    // would forward `mode` in x2 where libc reads from stack.
    unsafe extern "C" {
        fn open(path: *const c_char, flags: c_int, ...) -> c_int;
        fn openat(dirfd: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
        fn stat(path: *const c_char, buf: *mut c_void) -> c_int;
        fn lstat(path: *const c_char, buf: *mut c_void) -> c_int;
        fn fstatat(dirfd: c_int, path: *const c_char, buf: *mut c_void, flags: c_int) -> c_int;
        fn access(path: *const c_char, mode: c_int) -> c_int;
    }

    unsafe extern "C" fn real_open_shim(p: *const c_char, f: c_int, m: c_int) -> c_int {
        unsafe { open(p, f, m) }
    }
    unsafe extern "C" fn real_openat_shim(d: c_int, p: *const c_char, f: c_int, m: c_int) -> c_int {
        unsafe { openat(d, p, f, m) }
    }

    pub(super) fn real_open() -> OpenFn {
        real_open_shim
    }
    pub(super) fn real_openat() -> OpenatFn {
        real_openat_shim
    }
    pub(super) fn real_stat() -> StatFn {
        stat
    }
    pub(super) fn real_lstat() -> StatFn {
        lstat
    }
    pub(super) fn real_fstatat() -> FstatatFn {
        fstatat
    }
    pub(super) fn real_access() -> AccessFn {
        access
    }

    pub(super) unsafe extern "C" fn my_open(p: *const c_char, f: c_int, m: c_int) -> c_int {
        unsafe { super::do_open(p, f, m) }
    }
    pub(super) unsafe extern "C" fn my_openat(
        d: c_int,
        p: *const c_char,
        f: c_int,
        m: c_int,
    ) -> c_int {
        unsafe { super::do_openat(d, p, f, m) }
    }
    pub(super) unsafe extern "C" fn my_stat(p: *const c_char, b: *mut c_void) -> c_int {
        unsafe { super::do_stat(p, b) }
    }
    pub(super) unsafe extern "C" fn my_lstat(p: *const c_char, b: *mut c_void) -> c_int {
        unsafe { super::do_lstat(p, b) }
    }
    pub(super) unsafe extern "C" fn my_fstatat(
        d: c_int,
        p: *const c_char,
        b: *mut c_void,
        f: c_int,
    ) -> c_int {
        unsafe { super::do_fstatat(d, p, b, f) }
    }
    pub(super) unsafe extern "C" fn my_access(p: *const c_char, m: c_int) -> c_int {
        unsafe { super::do_access(p, m) }
    }

    #[repr(C)]
    pub struct Interpose {
        pub new: *const c_void,
        pub old: *const c_void,
    }
    // SAFETY: read-only static of immutable, process-global function pointers.
    unsafe impl Sync for Interpose {}

    // KNOWN LIMITATION: stable Rust can't *define* variadic fns, so on
    // aarch64-apple-darwin `my_open`/`my_openat` read garbage `mode` from
    // x2 when the caller used `O_CREAT` (real value sits on the stack per
    // Apple's ABI). We only forward `mode`, and nix eval doesn't use O_CREAT
    // on files we care about, so this is currently invisible.
    #[used]
    #[unsafe(link_section = "__DATA,__interpose")]
    pub static INTERPOSE_TABLE: [Interpose; 6] = [
        Interpose {
            new: my_open as *const c_void,
            old: open as *const c_void,
        },
        Interpose {
            new: my_openat as *const c_void,
            old: openat as *const c_void,
        },
        Interpose {
            new: my_stat as *const c_void,
            old: stat as *const c_void,
        },
        Interpose {
            new: my_lstat as *const c_void,
            old: lstat as *const c_void,
        },
        Interpose {
            new: my_fstatat as *const c_void,
            old: fstatat as *const c_void,
        },
        Interpose {
            new: my_access as *const c_void,
            old: access as *const c_void,
        },
    ];
}
