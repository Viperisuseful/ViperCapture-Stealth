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


def _command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return f"{result.stdout or ''}{result.stderr or ''}"


def openssl_headers_present(root: str) -> bool:
    return Path(root, "include", "openssl").is_dir()


def homebrew_openssl3_prefix() -> str | None:
    brew = shutil.which("brew")
    if not brew:
        return None
    output = _command_output([brew, "--prefix", "openssl@3"])
    if not output:
        return None
    prefix = output.strip().splitlines()[0].strip()
    if prefix and openssl_headers_present(prefix):
        return prefix
    return None


def intel_macos_openssl_ready() -> bool:
    openssl_dir = os.environ.get("OPENSSL_DIR", "").strip()
    if openssl_dir and openssl_headers_present(openssl_dir):
        return True
    if homebrew_openssl3_prefix():
        return True
    pkg_config = shutil.which("pkg-config")
    if not pkg_config:
        return False
    result = subprocess.run(
        [pkg_config, "--exists", "libcrypto"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def intel_macos_cryptography_issues() -> list[str]:
    """Missing official tools for a cryptography sdist build on Intel macOS."""
    issues: list[str] = []
    if not shutil.which("cc") and not shutil.which("clang"):
        issues.append(
            "C compiler (clang). Install Xcode Command Line Tools: xcode-select --install"
        )
    rustc = shutil.which("rustc")
    cargo = shutil.which("cargo")
    if not rustc or not cargo:
        issues.append(
            "Rust 1.83.0+ (rustc and cargo). Install with: brew install rust "
            f"— or rustup: https://rustup.rs. Docs: {PYCA_INSTALL_DOCS}"
        )
    else:
        parsed = parse_rustc_version(_command_output([rustc, "--version"]) or "")
        if parsed is None or parsed < CRYPTOGRAPHY_MIN_RUST:
            found = ".".join(str(part) for part in parsed) if parsed else "unknown"
            issues.append(
                "Rust 1.83.0 or newer is required to build cryptography "
                f"(found {found}). Upgrade with rustup or: brew upgrade rust"
            )
    if not intel_macos_openssl_ready():
        issues.append(
            "OpenSSL 3 headers (Apple LibreSSL is not supported). "
            "Install with: brew install openssl@3 "
            f"— {PYCA_INSTALL_DOCS}"
        )
    return issues


def intel_macos_cryptography_build_env() -> dict[str, str]:
    """Point the official sdist at Homebrew OpenSSL 3 when OPENSSL_DIR is unset."""
    extra: dict[str, str] = {}
    if os.environ.get("OPENSSL_DIR", "").strip():
        return extra
    prefix = homebrew_openssl3_prefix()
    if not prefix:
        return extra
    extra["OPENSSL_DIR"] = prefix
    pkgconfig = str(Path(prefix) / "lib" / "pkgconfig")
    existing = os.environ.get("PKG_CONFIG_PATH", "").strip()
    extra["PKG_CONFIG_PATH"] = f"{pkgconfig}:{existing}" if existing else pkgconfig
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


def prepare_intel_macos_cryptography_install() -> dict[str, str] | None:
    """Preflight Intel macOS sdist tools. Returns extra env, or None on other hosts."""
    if not is_intel_macos():
        return None
    issues = intel_macos_cryptography_issues()
    if issues:
        print("\n".join(intel_macos_cryptography_error_lines(issues)))
        wait_and_exit(1)
    extra = intel_macos_cryptography_build_env()
    print(
        "  Intel macOS: cryptography >=50 will be built from the official sdist "
        "(no PyPI x86_64/universal2 wheel)."
    )
    if extra.get("OPENSSL_DIR"):
        print(f"  Using Homebrew OpenSSL at {extra['OPENSSL_DIR']}")
    return extra


def venv_command(python: str, venv_dir: Path, uv: str | None) -> list[str]:
    if uv:
        return [uv, "venv", "--python", python, str(venv_dir)]
    return [python, "-m", "venv", str(venv_dir)]


def deps_commands(
    python: str, requirements: Path, uv: str | None
) -> list[tuple[list[str], str]]:
    if uv:
        return [(
            [uv, "pip", "install", "--python", python, "-r", str(requirements)],
            "uv pip install",
        )]
    return [
        ([python, "-m", "pip", "install", "--upgrade", "pip", "-q"], "pip upgrade"),
        ([python, "-m", "pip", "install", "-r", str(requirements)], "pip install"),
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

    uv = find_uv()
    if uv:
        print("  [2/3] Installing Python packages with uv...")
    else:
        if not _venv_has_pip(sys.executable):
            print("\n  ERROR: pip is not available in .venv and uv was not found.")
            print("  Install uv (https://docs.astral.sh/uv/) or delete .venv and rerun.")
            wait_and_exit(1)
        print("  [2/3] Installing Python packages...")

    for command, label in deps_commands(sys.executable, ROOT / "requirements.txt", uv):
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
