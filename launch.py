#!/usr/bin/env python3
"""
ViperCapture Stealth launcher
-----------------------------
Run this file directly with Python.
Handles venv setup, dependency install, Patchright Chromium install,
server startup, and opening your browser automatically.

Prefers uv (https://docs.astral.sh/uv/) when it is on PATH.
Set VIPERCAPTURE_USE_UV=0 to force the stdlib venv + pip path.
Without uv, the launcher falls back to pip.

On Intel macOS, cryptography >=49 has no PyPI wheel (arm64-only). The
launcher preflights Rust, OpenSSL 3, and a C compiler before the sdist
build instead of failing inside pip/uv.

On subsequent runs, dependency checks are skipped unless
    requirements.txt has changed (hash-stamped in .venv/).
"""

from __future__ import annotations
import hashlib
from importlib.metadata import version
import os
import platform as host_platform
import re
import shutil
import sys
import subprocess
import socket
import time
import webbrowser
from pathlib import Path
from typing import Callable

ROOT             = Path(__file__).parent.resolve()
VENV_PYTHON      = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
HOST             = "127.0.0.1"
PORT             = 8000
URL              = f"http://{HOST}:{PORT}/"
DEPS_STAMP       = ROOT / ".venv" / ".deps_stamp"
PATCHRIGHT_STAMP = ROOT / ".venv" / ".patchright_stamp"

# cryptography 49+ publishes macOS arm64 wheels only. Intel hosts must
# sdist-build a GHSA-jwv3-5hgf-82ww-patched release (>=49; this project
# stays on >=50). PyCA MSRV is 1.83.0 as of cryptography 48+.
CRYPTOGRAPHY_MIN_RUST = (1, 83, 0)
INTEL_MACOS_MACHINES = frozenset({"x86_64", "amd64", "i386", "i686"})
_RUSTC_VERSION_RE = re.compile(r"rustc\s+(\d+)\.(\d+)\.(\d+)")
PYCA_INSTALL_DOCS = "https://cryptography.io/en/latest/installation/"


def browser_install_targets() -> list[str]:
    """Install Chrome when requested or when the DISPLAY sweet spot is active."""
    channel = os.environ.get("VIPERCAPTURE_BROWSER_CHANNEL", "").strip().lower()
    if not channel:
        try:
            from vipercapture.browser_launch import browser_channel

            channel = browser_channel()
        except Exception:
            channel = "chromium"
    if channel == "chrome":
        return ["chrome"]
    return ["chromium"]


def patchright_install_command(python: str, *, with_deps: bool | None = None) -> list[str]:
    command = [python, "-m", "patchright", "install"]
    if with_deps is None:
        with_deps = sys.platform.startswith("linux")
    if with_deps:
        command.append("--with-deps")
    command.extend(browser_install_targets())
    return command


# ── Helpers ───────────────────────────────────────────────────

def port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


def run(*cmd: str | Path, label: str = "", env: dict[str, str] | None = None) -> None:
    """Run a subprocess and exit hard if it fails."""
    merged = None
    if env:
        merged = os.environ.copy()
        merged.update(env)
    result = subprocess.run([str(c) for c in cmd], env=merged)
    if result.returncode != 0:
        tag = f" ({label})" if label else ""
        print(f"\n  ERROR{tag}: command exited with code {result.returncode}")
        print(f"  Command: {' '.join(str(c) for c in cmd)}")
        wait_and_exit(1)


def wait_and_exit(code: int = 0) -> None:
    if sys.stdin.isatty():
        input("\n  Press Enter to close...")
    sys.exit(code)


def _req_hash() -> str:
    """MD5 of requirements.txt — used to detect changes between runs."""
    req = ROOT / "requirements.txt"
    return hashlib.md5(req.read_bytes()).hexdigest() if req.exists() else ""


def find_uv() -> str | None:
    """Return the uv executable when it should be used, otherwise None."""
    flag = os.environ.get("VIPERCAPTURE_USE_UV", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return None
    return shutil.which("uv")


def is_intel_macos(*, system: str | None = None, machine: str | None = None) -> bool:
    """True for darwin hosts that need a cryptography sdist (no x86_64 wheel)."""
    plat = sys.platform if system is None else system
    mach = host_platform.machine() if machine is None else machine
    return plat == "darwin" and mach.lower() in INTEL_MACOS_MACHINES


def parse_rustc_version(output: str) -> tuple[int, int, int] | None:
    match = _RUSTC_VERSION_RE.search(output)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _run_command(
    cmd: list[str],
    run: Callable[..., subprocess.CompletedProcess[str]],
    timeout: int = 15,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _command_stdout(
    cmd: list[str],
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> str | None:
    result = _run_command(cmd, run)
    if result is None or result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def _command_succeeded(
    cmd: list[str],
    run: Callable[..., subprocess.CompletedProcess[str]],
    *,
    needles: tuple[str, ...] = (),
) -> bool:
    result = _run_command(cmd, run)
    if result is None or result.returncode != 0:
        return False
    if not needles:
        return True
    text = f"{result.stdout or ''}{result.stderr or ''}".lower()
    return any(needle.lower() in text for needle in needles)


def _working_compiler(
    which: Callable[[str], str | None],
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> str | None:
    """Return a compiler that actually runs. Apple CLT stubs exist as /usr/bin/cc."""
    candidates: list[str] = []
    for name in ("cc", "clang"):
        found = which(name)
        if found and found not in candidates:
            candidates.append(found)
    developer_dir = _command_stdout(["xcode-select", "-p"], run)
    if developer_dir:
        for rel in ("usr/bin/clang", "usr/bin/cc"):
            nested = str(Path(developer_dir) / rel)
            if nested not in candidates:
                candidates.append(nested)
    for compiler in candidates:
        if _command_succeeded(
            [compiler, "-v"], run, needles=("clang", "gcc", "Apple LLVM")
        ):
            return compiler
    return None


def _has_openssl_libs(prefix: Path) -> bool:
    lib = prefix / "lib"
    if not lib.is_dir():
        return False
    names = {path.name for path in lib.iterdir()}

    def present(stem: str) -> bool:
        return any(
            name == f"lib{stem}.dylib"
            or name == f"lib{stem}.a"
            or name == f"lib{stem}.so"
            or name.startswith(f"lib{stem}.")
            and name.endswith(".dylib")
            for name in names
        )

    return present("crypto") and present("ssl")


def openssl_headers_are_usable(opensslv_h: str) -> bool:
    """Accept OpenSSL 3+; reject LibreSSL and older OpenSSL."""
    if re.search(r"^\s*#\s*define\s+LIBRESSL_VERSION_NUMBER\b", opensslv_h, re.M):
        return False
    major = re.search(
        r"^\s*#\s*define\s+OPENSSL_VERSION_MAJOR\s+(\d+)", opensslv_h, re.M
    )
    if major:
        return int(major.group(1)) >= 3
    number = re.search(
        r"^\s*#\s*define\s+OPENSSL_VERSION_NUMBER\s+(0x[0-9a-fA-F]+)",
        opensslv_h,
        re.M,
    )
    if number:
        return int(number.group(1), 16) >= 0x30000000
    return False


def _openssl_prefix_if_present(prefix: str) -> str | None:
    if not prefix:
        return None
    root = Path(prefix)
    header = root / "include" / "openssl" / "ssl.h"
    version_header = root / "include" / "openssl" / "opensslv.h"
    if not header.is_file() or not version_header.is_file():
        return None
    if not _has_openssl_libs(root):
        return None
    try:
        opensslv = version_header.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not openssl_headers_are_usable(opensslv):
        return None
    return prefix


def probe_intel_macos_cryptography_toolchain(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Discover a working compiler, rustc+cargo, and non-Apple OpenSSL 3 prefix."""
    env = os.environ if environ is None else environ
    compiler = _working_compiler(which, run)
    rustc = which("rustc")
    cargo = which("cargo")
    rust_version = None
    if rustc:
        rust_version = parse_rustc_version(
            _command_stdout([rustc, "--version"], run) or ""
        )
    cargo_ok = bool(
        cargo and _command_succeeded([cargo, "--version"], run, needles=("cargo",))
    )
    openssl_dir = _openssl_prefix_if_present(env.get("OPENSSL_DIR", "").strip())
    brew = which("brew")
    if openssl_dir is None and brew:
        for formula in ("openssl@3", "openssl"):
            prefix = _command_stdout([brew, "--prefix", formula], run)
            openssl_dir = _openssl_prefix_if_present(prefix or "")
            if openssl_dir:
                break
    if openssl_dir is None:
        openssl_dir = _openssl_prefix_if_present("/opt/local")
    if openssl_dir is None:
        pkg_config = which("pkg-config") or which("pkgconf")
        if pkg_config:
            prefix = _command_stdout(
                [pkg_config, "--variable=prefix", "libcrypto"], run
            )
            openssl_dir = _openssl_prefix_if_present(prefix or "")
    return {
        "compiler": compiler,
        "rustc": rustc,
        "rust_version": rust_version,
        "cargo": cargo if cargo_ok else None,
        "openssl_dir": openssl_dir,
    }


def intel_macos_cryptography_missing(toolchain: dict[str, object]) -> list[str]:
    missing: list[str] = []
    if not toolchain.get("compiler"):
        missing.append(
            "C compiler (clang). Install Xcode Command Line Tools: xcode-select --install"
        )
    rust_version = toolchain.get("rust_version")
    rust_ok = (
        isinstance(rust_version, tuple)
        and rust_version >= CRYPTOGRAPHY_MIN_RUST
        and toolchain.get("cargo")
    )
    if not rust_ok:
        missing.append(
            "Rust 1.83.0+ (rustc and cargo). Install with: brew install rust "
            f"— or rustup: https://rustup.rs. Docs: {PYCA_INSTALL_DOCS}"
        )
    if not toolchain.get("openssl_dir"):
        missing.append(
            "OpenSSL 3 headers and libssl/libcrypto (Apple LibreSSL is not supported). "
            "Install with: brew install openssl@3 "
            f"— {PYCA_INSTALL_DOCS}"
        )
    return missing


def intel_macos_cryptography_issues(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Missing official tools for a cryptography sdist build on Intel macOS."""
    return intel_macos_cryptography_missing(
        probe_intel_macos_cryptography_toolchain(
            which=which, run=run, environ=environ
        )
    )


def apply_intel_macos_cryptography_env(
    environ: dict[str, str], openssl_dir: str
) -> dict[str, str]:
    """Point the cryptography sdist at a real OpenSSL 3 prefix (PyCA OPENSSL_DIR)."""
    environ["OPENSSL_DIR"] = openssl_dir
    pkgconfig = Path(openssl_dir) / "lib" / "pkgconfig"
    if pkgconfig.is_dir():
        current = environ.get("PKG_CONFIG_PATH", "")
        prefix = str(pkgconfig)
        parts = [part for part in current.split(":") if part]
        if prefix not in parts:
            environ["PKG_CONFIG_PATH"] = ":".join([prefix, *parts])
    return environ


def intel_macos_cryptography_build_env(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolved OpenSSL 3 env for the sdist; ignores header-only / LibreSSL prefixes."""
    extra: dict[str, str] = {}
    env = os.environ if environ is None else environ
    toolchain = probe_intel_macos_cryptography_toolchain(
        which=which, run=run, environ=env
    )
    openssl_dir = toolchain.get("openssl_dir")
    if isinstance(openssl_dir, str) and openssl_dir:
        apply_intel_macos_cryptography_env(extra, openssl_dir)
    return extra


def intel_macos_cryptography_error_lines(issues: list[str]) -> list[str]:
    lines = [
        "",
        "  ERROR: Intel macOS cannot install cryptography from a wheel.",
        "  PyPI has no darwin x86_64 or universal2 wheel for cryptography 49+",
        "  (49.0.0 / 50.0.0 / 50.0.1 publish macosx_11_0_arm64 only).",
        "  GHSA-jwv3-5hgf-82ww requires >=49.0.0; this project keeps >=50.0.0.",
        "  Do not install cryptography 48.x (last universal2 wheels; still <=48).",
        "  Missing:",
    ]
    lines.extend(f"    - {issue}" for issue in issues)
    lines.extend(
        [
            "",
            "  Install official PyCA build tools, then rerun python launch.py:",
            "    xcode-select --install",
            "    brew install openssl@3 rust",
            "  Rust 1.83+: https://rustup.rs",
            f"  PyCA: {PYCA_INSTALL_DOCS}",
        ]
    )
    return lines


def prepare_intel_macos_cryptography_install(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environ: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Preflight Intel macOS sdist tools. Returns extra env, or None on other hosts."""
    if not is_intel_macos():
        return None
    env = os.environ if environ is None else environ
    toolchain = probe_intel_macos_cryptography_toolchain(
        which=which, run=run, environ=env
    )
    issues = intel_macos_cryptography_missing(toolchain)
    if issues:
        print("\n".join(intel_macos_cryptography_error_lines(issues)))
        wait_and_exit(1)
    extra: dict[str, str] = {}
    openssl_dir = toolchain.get("openssl_dir")
    if isinstance(openssl_dir, str) and openssl_dir:
        apply_intel_macos_cryptography_env(extra, openssl_dir)
        if environ is not None:
            apply_intel_macos_cryptography_env(environ, openssl_dir)
    print(
        "  Intel macOS: cryptography >=50 will be built from the official sdist "
        "(no PyPI x86_64/universal2 wheel)."
    )
    if extra.get("OPENSSL_DIR"):
        print(f"  Using OpenSSL 3 at {extra['OPENSSL_DIR']}")
    return extra


def venv_command(python: str, venv_dir: Path, uv: str | None) -> list[str]:
    if uv:
        return [uv, "venv", "--python", python, str(venv_dir)]
    return [python, "-m", "venv", str(venv_dir)]


def deps_commands(
    python: str,
    requirements: Path,
    uv: str | None,
    *,
    intel_macos: bool = False,
) -> list[tuple[list[str], str]]:
    source_build = ["--no-binary", "cryptography"] if intel_macos else []
    if uv:
        return [(
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "-r",
                str(requirements),
                *source_build,
            ],
            "uv pip install",
        )]
    return [
        ([python, "-m", "pip", "install", "--upgrade", "pip", "-q"], "pip upgrade"),
        (
            [python, "-m", "pip", "install", "-r", str(requirements), *source_build],
            "pip install",
        ),
    ]


def _venv_has_pip(python: str) -> bool:
    result = subprocess.run(
        [python, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


# ── Setup steps ───────────────────────────────────────────────

def ensure_venv() -> None:
    """
    If the venv doesn't exist yet, create it.
    If we're not running from the venv Python, re-launch with it so
    all subsequent imports and subprocess calls use the right Python.
    """
    if not VENV_PYTHON.exists():
        uv = find_uv()
        installer = "uv" if uv else "venv"
        print(f"  [1/3] Creating Python environment with {installer} (first run only)...")
        command = venv_command(sys.executable, ROOT / ".venv", uv)
        run(*command, label=f"{installer} venv")

    this = Path(sys.executable).resolve()
    want = VENV_PYTHON.resolve()
    if this != want:
        # Hand off to the venv Python — this process becomes just a waiter.
        result = subprocess.run([str(VENV_PYTHON), __file__] + sys.argv[1:])
        sys.exit(result.returncode)


def ensure_deps() -> None:
    """
    Install packages from requirements.txt.
    Skipped on subsequent runs unless requirements.txt has changed.
    Prefers uv when available; falls back to pip.
    """
    current_hash = _req_hash()
    if DEPS_STAMP.exists() and DEPS_STAMP.read_text().strip() == current_hash:
        print("  [2/3] Python packages already up to date — skipping.")
        return

    install_env = prepare_intel_macos_cryptography_install()
    intel_macos = is_intel_macos()

    uv = find_uv()
    if uv:
        print("  [2/3] Installing Python packages with uv...")
    else:
        if not _venv_has_pip(sys.executable):
            print("\n  ERROR: pip is not available in .venv and uv was not found.")
            print("  Install uv (https://docs.astral.sh/uv/) or delete .venv and rerun.")
            wait_and_exit(1)
        print("  [2/3] Installing Python packages...")

    for command, label in deps_commands(
        sys.executable,
        ROOT / "requirements.txt",
        uv,
        intel_macos=intel_macos,
    ):
        run(*command, label=label, env=install_env)
    DEPS_STAMP.write_text(current_hash)


def ensure_patchright() -> None:
    """
    Install Patchright's Chromium (or Chrome when VIPERCAPTURE_BROWSER_CHANNEL=chrome).
    Skipped when the installed browser matches the Patchright package version.
    """
    targets = ",".join(browser_install_targets())
    patchright_stamp = f"{version('patchright')}:{targets}"
    if (
        PATCHRIGHT_STAMP.exists()
        and PATCHRIGHT_STAMP.read_text().strip() == patchright_stamp
    ):
        print("  [3/3] Patchright browsers already installed — skipping.")
        return

    print(f"  [3/3] Installing Patchright browsers ({targets})...")
    command = patchright_install_command(sys.executable)
    run(*command, label="patchright install")
    PATCHRIGHT_STAMP.write_text(patchright_stamp)


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    print()
    print("  ViperCapture Stealth")
    print("  --------------------")
    print()

    ensure_venv()    # may re-exec this script under the venv Python
    ensure_deps()
    ensure_patchright()

    # Server already running from a previous session?
    if port_open():
        print(f"\n  Server already running. Opening {URL}")
        webbrowser.open(URL)
        return

    # ── Start the server ────────────────────────────────────────
    print(f"\n  Starting server at {URL}")
    try:
        from vipercapture.browser_launch import headed_chrome_hint

        hint = headed_chrome_hint()
        if hint:
            print(f"  Note: {hint}")
    except Exception:
        pass
    print("  Press Ctrl+C here to stop the server.\n")

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vipercapture.main:app",
         "--host", HOST, "--port", str(PORT)],
        cwd=str(ROOT),
    )

    # ── Wait for port (1 check/sec, 30s max) ────────────────────
    print("  Waiting for server to be ready...", end="", flush=True)
    ready = False
    for _ in range(30):
        if port_open():
            ready = True
            break
        if server.poll() is not None:
            # Server process already exited — don't wait the full 30s
            break
        time.sleep(1)
        print(".", end="", flush=True)
    print()

    if not ready:
        print("\n  ERROR: Server didn't start.")
        print("  Check the output above for details.")
        server.terminate()
        wait_and_exit(1)

    # ── Open browser ────────────────────────────────────────────
    webbrowser.open(URL)
    print(f"\n  Ready! Opened {URL} in your browser.")
    print("  Ctrl+C to stop the server.\n")

    # Keep this window alive — show server logs until Ctrl+C
    try:
        server.wait()
    except KeyboardInterrupt:
        print("\n  Stopping server...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print("  Server stopped.")


if __name__ == "__main__":
    main()
