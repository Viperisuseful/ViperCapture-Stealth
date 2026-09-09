from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
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

    def test_preflight_reports_missing_compiler_rust_and_openssl(self) -> None:
        with mock.patch("launch.shutil.which", return_value=None):
            with mock.patch.object(launch, "intel_macos_openssl_ready", return_value=False):
                issues = launch.intel_macos_cryptography_issues()
        joined = " ".join(issues)
        self.assertEqual(len(issues), 3)
        self.assertIn("xcode-select --install", joined)
        self.assertIn("brew install rust", joined)
        self.assertIn("brew install openssl@3", joined)
        self.assertIn(launch.PYCA_INSTALL_DOCS, joined)

    def test_preflight_rejects_old_rustc(self) -> None:
        def which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in {"cc", "rustc", "cargo"} else None

        with mock.patch("launch.shutil.which", side_effect=which):
            with mock.patch.object(launch, "_command_output", return_value="rustc 1.74.0"):
                with mock.patch.object(launch, "intel_macos_openssl_ready", return_value=True):
                    issues = launch.intel_macos_cryptography_issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("1.83.0", issues[0])
        self.assertIn("1.74.0", issues[0])

    def test_preflight_passes_when_toolchain_present(self) -> None:
        def which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in {"cc", "rustc", "cargo"} else None

        with mock.patch("launch.shutil.which", side_effect=which):
            with mock.patch.object(
                launch, "_command_output", return_value="rustc 1.83.0 (abc 2026-01-01)"
            ):
                with mock.patch.object(launch, "intel_macos_openssl_ready", return_value=True):
                    self.assertEqual(launch.intel_macos_cryptography_issues(), [])

    def test_openssl_ready_uses_openssl_dir(self) -> None:
        with mock.patch.dict(os.environ, {"OPENSSL_DIR": "/opt/openssl@3"}, clear=False):
            with mock.patch.object(launch, "openssl_headers_present", return_value=True):
                self.assertTrue(launch.intel_macos_openssl_ready())

    def test_build_env_sets_homebrew_openssl_dir(self) -> None:
        with mock.patch.dict(os.environ, {"OPENSSL_DIR": ""}, clear=False):
            os.environ.pop("OPENSSL_DIR", None)
            os.environ.pop("PKG_CONFIG_PATH", None)
            with mock.patch.object(
                launch, "homebrew_openssl3_prefix", return_value="/usr/local/opt/openssl@3"
            ):
                env = launch.intel_macos_cryptography_build_env()
        self.assertEqual(env["OPENSSL_DIR"], "/usr/local/opt/openssl@3")
        self.assertEqual(
            env["PKG_CONFIG_PATH"],
            "/usr/local/opt/openssl@3/lib/pkgconfig",
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
                launch, "intel_macos_cryptography_issues", return_value=["Rust 1.83.0+"]
            ):
                with mock.patch.object(launch, "wait_and_exit", side_effect=SystemExit(1)) as exit_fn:
                    with self.assertRaises(SystemExit):
                        launch.prepare_intel_macos_cryptography_install()
                    exit_fn.assert_called_once_with(1)

    def test_prepare_returns_build_env_when_ready(self) -> None:
        extra = {"OPENSSL_DIR": "/usr/local/opt/openssl@3"}
        with mock.patch.object(launch, "is_intel_macos", return_value=True):
            with mock.patch.object(launch, "intel_macos_cryptography_issues", return_value=[]):
                with mock.patch.object(
                    launch, "intel_macos_cryptography_build_env", return_value=extra
                ):
                    self.assertEqual(launch.prepare_intel_macos_cryptography_install(), extra)

    def test_ensure_deps_preflight_before_install_on_intel_macos(self) -> None:
        with mock.patch.object(launch, "DEPS_STAMP") as stamp:
            stamp.exists.return_value = False
            with mock.patch.object(launch, "_req_hash", return_value="abc"):
                with mock.patch.object(launch, "is_intel_macos", return_value=True):
                    with mock.patch.object(
                        launch, "intel_macos_cryptography_issues", return_value=["Rust"]
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
