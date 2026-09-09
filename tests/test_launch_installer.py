from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import launch  # noqa: E402


class FindUvTests(unittest.TestCase):
    def test_find_uv_returns_which_result(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIPERCAPTURE_USE_UV", None)
            with mock.patch("launch.shutil.which", return_value="/opt/uv") as which:
                self.assertEqual(launch.find_uv(), "/opt/uv")
                which.assert_called_once_with("uv")

    def test_find_uv_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_USE_UV": "0"}):
            with mock.patch("launch.shutil.which", return_value="/opt/uv") as which:
                self.assertIsNone(launch.find_uv())
                which.assert_not_called()


class InstallerCommandTests(unittest.TestCase):
    def test_venv_prefers_uv(self) -> None:
        venv_dir = Path("/tmp/.venv")
        command = launch.venv_command("/usr/bin/python3", venv_dir, "/opt/uv")
        self.assertEqual(
            command,
            ["/opt/uv", "venv", "--python", "/usr/bin/python3", str(venv_dir)],
        )

    def test_venv_falls_back_to_stdlib(self) -> None:
        venv_dir = Path("/tmp/.venv")
        command = launch.venv_command("/usr/bin/python3", venv_dir, None)
        self.assertEqual(
            command,
            ["/usr/bin/python3", "-m", "venv", str(venv_dir)],
        )

    def test_deps_prefer_uv_pip(self) -> None:
        requirements = Path("/app/requirements.txt")
        commands = launch.deps_commands("/venv/bin/python", requirements, "/opt/uv")
        self.assertEqual(
            commands,
            [(
                [
                    "/opt/uv",
                    "pip",
                    "install",
                    "--python",
                    "/venv/bin/python",
                    "-r",
                    str(requirements),
                ],
                "uv pip install",
            )],
        )

    def test_deps_fall_back_to_pip(self) -> None:
        requirements = Path("/app/requirements.txt")
        commands = launch.deps_commands("/venv/bin/python", requirements, None)
        self.assertEqual(
            [label for _command, label in commands],
            ["pip upgrade", "pip install"],
        )
        self.assertEqual(
            commands[1][0],
            [
                "/venv/bin/python",
                "-m",
                "pip",
                "install",
                "-r",
                str(requirements),
            ],
        )

    def test_deps_force_cryptography_sdist_on_intel_macos(self) -> None:
        requirements = Path("/app/requirements.txt")
        uv_commands = launch.deps_commands(
            "/venv/bin/python", requirements, "/opt/uv", intel_macos=True
        )
        pip_commands = launch.deps_commands(
            "/venv/bin/python", requirements, None, intel_macos=True
        )
        self.assertEqual(uv_commands[0][0][-2:], ["--no-binary", "cryptography"])
        self.assertEqual(pip_commands[1][0][-2:], ["--no-binary", "cryptography"])

    def test_patchright_installs_chromium_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT", None)
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            os.environ.pop("VIPERCAPTURE_IN_DOCKER", None)
            self.assertEqual(launch.browser_install_targets(), ["chromium"])
            command = launch.patchright_install_command("/venv/bin/python", with_deps=True)
            self.assertEqual(
                command,
                [
                    "/venv/bin/python",
                    "-m",
                    "patchright",
                    "install",
                    "--with-deps",
                    "chromium",
                ],
            )

    def test_patchright_can_install_chrome_channel(self) -> None:
        with mock.patch.dict(os.environ, {"VIPERCAPTURE_BROWSER_CHANNEL": "chrome"}):
            self.assertEqual(launch.browser_install_targets(), ["chrome"])
            command = launch.patchright_install_command("/venv/bin/python", with_deps=False)
            self.assertEqual(
                command,
                ["/venv/bin/python", "-m", "patchright", "install", "chrome"],
            )

    def test_patchright_installs_chrome_when_display_sweetspot(self) -> None:
        with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=False):
            os.environ.pop("VIPERCAPTURE_BROWSER_CHANNEL", None)
            os.environ.pop("VIPERCAPTURE_PATCHRIGHT_SWEETSPOT", None)
            os.environ.pop("VIPERCAPTURE_IN_DOCKER", None)
            with mock.patch(
                "vipercapture.browser_launch.running_in_docker", return_value=False
            ):
                self.assertEqual(launch.browser_install_targets(), ["chrome"])


class CryptographyPinTests(unittest.TestCase):
    def test_requirements_keep_patched_floor_without_vulnerable_intel_pin(self) -> None:
        text = (ROOT / "requirements.txt").read_text()
        self.assertIn("cryptography>=50.0.0", text)
        self.assertNotRegex(text, r"cryptography[^#\n]*<")
        self.assertNotIn("<47.0.0", text)
        self.assertNotIn("platform_machine == \"x86_64\"", text)


class IntelMacosCryptographyTests(unittest.TestCase):
    def test_live_ci_host_is_not_intel_macos(self) -> None:
        if sys.platform == "darwin" and launch.host_platform.machine().lower() in launch.INTEL_MACOS_MACHINES:
            self.skipTest("this job is Intel macOS")
        self.assertFalse(launch.is_intel_macos())
        self.assertIsNone(launch.prepare_intel_macos_cryptography_install())

    def test_is_intel_macos_only_for_darwin_x86(self) -> None:
        self.assertTrue(launch.is_intel_macos(system="darwin", machine="x86_64"))
        self.assertTrue(launch.is_intel_macos(system="darwin", machine="X86_64"))
        self.assertTrue(launch.is_intel_macos(system="darwin", machine="amd64"))
        self.assertFalse(launch.is_intel_macos(system="darwin", machine="arm64"))
        self.assertFalse(launch.is_intel_macos(system="linux", machine="x86_64"))
        self.assertFalse(launch.is_intel_macos(system="win32", machine="AMD64"))

    def test_parse_rustc_version(self) -> None:
        self.assertEqual(
            launch.parse_rustc_version("rustc 1.83.0 (d6d39023e 2025-10-07)"),
            (1, 83, 0),
        )
        self.assertEqual(launch.parse_rustc_version("rustc 1.82.0"), (1, 82, 0))
        self.assertIsNone(launch.parse_rustc_version("not rustc"))
        self.assertLess(launch.parse_rustc_version("rustc 1.82.0"), launch.CRYPTOGRAPHY_MIN_RUST)
        self.assertGreaterEqual(
            launch.parse_rustc_version("rustc 1.83.0"), launch.CRYPTOGRAPHY_MIN_RUST
        )

    def _openssl_prefix(self, root: Path) -> str:
        include = root / "include" / "openssl"
        include.mkdir(parents=True)
        (include / "ssl.h").write_text("/* test */\n", encoding="utf-8")
        (include / "opensslv.h").write_text(
            "# define OPENSSL_VERSION_MAJOR  3\n"
            "# define OPENSSL_VERSION_MINOR  5\n",
            encoding="utf-8",
        )
        lib = root / "lib" / "pkgconfig"
        lib.mkdir(parents=True)
        (root / "lib" / "libcrypto.dylib").write_bytes(b"")
        (root / "lib" / "libssl.dylib").write_bytes(b"")
        return str(root)

    def _ready_which(self, **extra: str) -> Callable[[str], str | None]:
        mapping = {
            "cc": "/usr/bin/cc",
            "rustc": "/usr/local/bin/rustc",
            "cargo": "/usr/local/bin/cargo",
            "brew": "/usr/local/bin/brew",
            **extra,
        }

        def which(name: str) -> str | None:
            return mapping.get(name)

        return which

    def _ready_run(self, openssl_prefix: str | None = None):
        def run(cmd, **_kwargs):
            if cmd[:2] == ["/usr/bin/cc", "-v"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="", stderr="Apple clang version 17.0.0\n"
                )
            if cmd[:2] == ["/usr/local/bin/rustc", "--version"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="rustc 1.85.0 (hash)\n", stderr=""
                )
            if cmd[:2] == ["/usr/local/bin/cargo", "--version"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="cargo 1.85.0 (hash)\n", stderr=""
                )
            if (
                openssl_prefix
                and cmd[:3] == ["/usr/local/bin/brew", "--prefix", "openssl@3"]
            ):
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=openssl_prefix + "\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        return run

    def test_preflight_reports_missing_compiler_rust_and_openssl(self) -> None:
        toolchain = launch.probe_intel_macos_cryptography_toolchain(
            which=lambda _name: None,
            run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                [], 1, stdout="", stderr=""
            ),
            environ={},
        )
        issues = launch.intel_macos_cryptography_missing(toolchain)
        joined = " ".join(issues)
        self.assertEqual(len(issues), 3)
        self.assertIn("xcode-select --install", joined)
        self.assertIn("brew install rust", joined)
        self.assertIn("brew install openssl@3", joined)
        self.assertIn(launch.PYCA_INSTALL_DOCS, joined)

    def test_preflight_rejects_old_rustc(self) -> None:
        def run(cmd, **_kwargs):
            if cmd[:2] == ["/usr/bin/cc", "-v"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="", stderr="Apple clang version 17.0.0\n"
                )
            if cmd[:2] == ["/usr/bin/rustc", "--version"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="rustc 1.74.0\n", stderr=""
                )
            if cmd[:2] == ["/usr/bin/cargo", "--version"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="cargo 1.74.0\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        issues = launch.intel_macos_cryptography_issues(
            which=lambda name: {
                "cc": "/usr/bin/cc",
                "rustc": "/usr/bin/rustc",
                "cargo": "/usr/bin/cargo",
            }.get(name),
            run=run,
            environ={},
        )
        self.assertTrue(any("Rust 1.83.0+" in issue for issue in issues))

    def test_preflight_passes_when_toolchain_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            issues = launch.intel_macos_cryptography_issues(
                which=self._ready_which(),
                run=self._ready_run(prefix),
                environ={},
            )
            self.assertEqual(issues, [])

    def test_rejects_compiler_stub_that_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")

            def run(cmd, **_kwargs):
                if cmd[:2] == ["/usr/bin/cc", "-v"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        1,
                        stdout="",
                        stderr="xcode-select: note: no developer tools were found\n",
                    )
                return self._ready_run(prefix)(cmd)

            issues = launch.intel_macos_cryptography_issues(
                which=self._ready_which(),
                run=run,
                environ={},
            )
            self.assertTrue(any("xcode-select --install" in issue for issue in issues))

    def test_rejects_rustc_without_cargo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            mapping = {
                "cc": "/usr/bin/cc",
                "rustc": "/usr/local/bin/rustc",
                "brew": "/usr/local/bin/brew",
            }
            issues = launch.intel_macos_cryptography_issues(
                which=lambda name: mapping.get(name),
                run=self._ready_run(prefix),
                environ={},
            )
            self.assertTrue(any("rustc and cargo" in issue for issue in issues))

    def test_skips_header_only_openssl_and_uses_brew(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale"
            (stale / "include" / "openssl").mkdir(parents=True)
            (stale / "include" / "openssl" / "ssl.h").write_text(
                "/* headers only */\n", encoding="utf-8"
            )
            brew_prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            environ = {"OPENSSL_DIR": str(stale)}
            extra = launch.intel_macos_cryptography_build_env(
                which=self._ready_which(),
                run=self._ready_run(brew_prefix),
                environ=environ,
            )
            self.assertEqual(extra["OPENSSL_DIR"], brew_prefix)

    def test_rejects_libressl_prefix(self) -> None:
        self.assertFalse(
            launch.openssl_headers_are_usable(
                "# define LIBRESSL_VERSION_NUMBER 0x40000000L\n"
                "# define OPENSSL_VERSION_NUMBER  0x20000000L\n"
            )
        )
        self.assertTrue(
            launch.openssl_headers_are_usable("# define OPENSSL_VERSION_MAJOR  3\n")
        )
        self.assertFalse(
            launch.openssl_headers_are_usable(
                "# define OPENSSL_VERSION_NUMBER  0x101010cfL\n"
            )
        )

    def test_openssl_ready_uses_validated_openssl_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            toolchain = launch.probe_intel_macos_cryptography_toolchain(
                which=lambda _name: None,
                run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                    [], 1, stdout="", stderr=""
                ),
                environ={"OPENSSL_DIR": prefix},
            )
            self.assertEqual(toolchain["openssl_dir"], prefix)

    def test_build_env_sets_homebrew_openssl_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            extra = launch.intel_macos_cryptography_build_env(
                which=self._ready_which(),
                run=self._ready_run(prefix),
                environ={},
            )
        self.assertEqual(extra["OPENSSL_DIR"], prefix)
        self.assertEqual(
            extra["PKG_CONFIG_PATH"],
            str(Path(prefix) / "lib" / "pkgconfig"),
        )

    def test_error_lines_are_actionable_and_forbid_vulnerable_wheels(self) -> None:
        text = "\n".join(launch.intel_macos_cryptography_error_lines(["Rust 1.83.0+"]))
        self.assertIn("GHSA-jwv3-5hgf-82ww", text)
        self.assertIn(">=50.0.0", text)
        self.assertIn("brew install openssl@3 rust", text)
        self.assertIn("xcode-select --install", text)
        self.assertIn(launch.PYCA_INSTALL_DOCS, text)
        self.assertIn("48.x", text)
        self.assertNotIn("unofficial", text.lower())

    def test_prepare_skips_non_intel_hosts(self) -> None:
        with mock.patch.object(launch, "is_intel_macos", return_value=False):
            self.assertIsNone(launch.prepare_intel_macos_cryptography_install())

    def test_prepare_exits_when_toolchain_missing(self) -> None:
        with mock.patch.object(launch, "is_intel_macos", return_value=True):
            with mock.patch.object(
                launch, "intel_macos_cryptography_missing", return_value=["Rust 1.83.0+"]
            ):
                with mock.patch.object(
                    launch,
                    "probe_intel_macos_cryptography_toolchain",
                    return_value={},
                ):
                    with mock.patch.object(
                        launch, "wait_and_exit", side_effect=SystemExit(1)
                    ) as exit_fn:
                        with self.assertRaises(SystemExit):
                            launch.prepare_intel_macos_cryptography_install()
                        exit_fn.assert_called_once_with(1)

    def test_prepare_returns_build_env_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._openssl_prefix(Path(tmp) / "openssl@3")
            with mock.patch.object(launch, "is_intel_macos", return_value=True):
                extra = launch.prepare_intel_macos_cryptography_install(
                    which=self._ready_which(),
                    run=self._ready_run(prefix),
                    environ={},
                )
        self.assertIsNotNone(extra)
        assert extra is not None
        self.assertEqual(extra["OPENSSL_DIR"], prefix)

    def test_ensure_deps_preflight_before_install_on_intel_macos(self) -> None:
        with mock.patch.object(launch, "DEPS_STAMP") as stamp:
            stamp.exists.return_value = False
            with mock.patch.object(launch, "_req_hash", return_value="abc"):
                with mock.patch.object(launch, "is_intel_macos", return_value=True):
                    with mock.patch.object(
                        launch,
                        "probe_intel_macos_cryptography_toolchain",
                        return_value={},
                    ):
                        with mock.patch.object(
                            launch,
                            "intel_macos_cryptography_missing",
                            return_value=["Rust"],
                        ):
                            with mock.patch.object(
                                launch, "wait_and_exit", side_effect=SystemExit(1)
                            ) as exit_fn:
                                with mock.patch.object(launch, "run") as run:
                                    with self.assertRaises(SystemExit):
                                        launch.ensure_deps()
        exit_fn.assert_called_once_with(1)
        run.assert_not_called()

    def test_ensure_deps_passes_openssl_env_on_intel_macos(self) -> None:
        extra = {"OPENSSL_DIR": "/usr/local/opt/openssl@3"}
        with mock.patch.object(launch, "DEPS_STAMP") as stamp:
            stamp.exists.return_value = False
            stamp.write_text = mock.Mock()
            with mock.patch.object(launch, "_req_hash", return_value="abc"):
                with mock.patch.object(
                    launch, "prepare_intel_macos_cryptography_install", return_value=extra
                ):
                    with mock.patch.object(launch, "find_uv", return_value="/opt/uv"):
                        with mock.patch.object(launch, "run") as run:
                            launch.ensure_deps()
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs.get("env"), extra)

    def test_ensure_deps_skips_preflight_when_stamp_matches(self) -> None:
        with mock.patch.object(launch, "DEPS_STAMP") as stamp:
            stamp.exists.return_value = True
            stamp.read_text.return_value = "abc\n"
            with mock.patch.object(launch, "_req_hash", return_value="abc"):
                with mock.patch.object(
                    launch, "prepare_intel_macos_cryptography_install"
                ) as prepare:
                    with mock.patch.object(launch, "run") as run:
                        launch.ensure_deps()
        prepare.assert_not_called()
        run.assert_not_called()

    def test_run_forwards_extra_env(self) -> None:
        completed = mock.Mock(returncode=0)
        extra = {"OPENSSL_DIR": "/opt/openssl@3"}
        with mock.patch("launch.subprocess.run", return_value=completed) as subproc:
            with mock.patch.dict(os.environ, {"PATH": "/bin"}, clear=False):
                launch.run("true", label="noop", env=extra)
        kwargs = subproc.call_args.kwargs
        self.assertEqual(kwargs["env"]["OPENSSL_DIR"], "/opt/openssl@3")
        self.assertIn("PATH", kwargs["env"])


if __name__ == "__main__":
    unittest.main()
