import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import bootstrap


def info(python="python", version=(3, 12, 14), issues=()):
    return dict(python=str(python), version=list(version), bits=64,
                versions={}, issues=list(issues))


class BootstrapTests(unittest.TestCase):
    def test_incompatible_old_environment_is_preserved_and_a_fresh_environment_is_selected(self):
        with tempfile.TemporaryDirectory(prefix="TQ 环境 ") as directory:
            root = Path(directory)
            old = root/".venv"/"bin"/"python"; old.parent.mkdir(parents=True); old.write_text("old")
            def create(command, cwd):
                target = Path(command[-1])/"bin"/"python"
                target.parent.mkdir(parents=True); target.write_text("new")
                return 0
            with patch.object(bootstrap, "WINDOWS", False), \
                 patch.object(bootstrap, "probe", return_value=info(old, (3, 14, 0))), \
                 patch.object(bootstrap, "choose_base", return_value=[sys.executable]), \
                 patch.object(bootstrap, "run_logged", side_effect=create):
                result = bootstrap.ensure_environment(root)
                self.assertNotEqual(result, old)
                self.assertEqual(old.read_text(), "old")
                self.assertEqual(bootstrap.environment_python(root), result)
                self.assertEqual(json.loads((root/".tq-runtime.json").read_text())["environment"], ".venv-studio")

    def test_healthy_existing_environment_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = bootstrap.environment_python(root); python.parent.mkdir(parents=True); python.write_text("existing")
            with patch.object(bootstrap, "probe", return_value=info(python)), \
                 patch.object(bootstrap, "choose_base") as choose:
                self.assertEqual(bootstrap.ensure_environment(root), python)
                choose.assert_not_called()

    def test_windows_without_a_compatible_base_uses_private_python_and_keeps_old_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root/".venv/Scripts/python.exe"; old.parent.mkdir(parents=True); old.write_text("old")
            private = root/".python-tq/python.exe"
            def create(command, cwd):
                target = Path(command[-1])/"Scripts/python.exe"
                target.parent.mkdir(parents=True); target.write_text("new")
                return 0
            with patch.object(bootstrap, "WINDOWS", True), \
                 patch.object(bootstrap, "probe", return_value=info(old, (3, 14, 0))), \
                 patch.object(bootstrap, "choose_base", return_value=None), \
                 patch.object(bootstrap, "private_python", return_value=[str(private)]) as download, \
                 patch.object(bootstrap, "run_logged", side_effect=create) as create_env:
                python = bootstrap.ensure_environment(root)
                download.assert_called_once_with(root)
                self.assertEqual(create_env.call_args.args[0][:1], [str(private)])
                self.assertEqual(old.read_text(), "old")
                self.assertEqual(bootstrap.environment_python(root), python)

    def test_invalid_marker_cannot_select_an_executable_outside_the_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/".tq-runtime.json").write_text(json.dumps(dict(environment="../../outside")))
            self.assertEqual(bootstrap.environment_python(root).parent.parent, root/".venv")

    def test_missing_qt_is_repaired_before_the_same_interpreter_launches_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); python = root/".venv-studio"/"python"
            with patch.object(bootstrap, "ensure_environment", return_value=python), \
                 patch.object(bootstrap, "probe", side_effect=[info(python, issues=["PySide6缺失"]), info(python)]), \
                 patch.object(bootstrap, "install_requirements", return_value=True) as install, \
                 patch.object(bootstrap.subprocess, "call", return_value=0) as launch:
                self.assertEqual(bootstrap.main(["--demo"], root=root), 0)
                install.assert_called_once_with(root, python)
                self.assertEqual(launch.call_args.args[0], [str(python), str(root/"run.py"), "--demo"])
                self.assertEqual(launch.call_args.kwargs["env"]["TQ_STUDIO_BOOTSTRAPPED"], "1")

    def test_failed_install_never_launches_the_unprepared_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); python = root/"python"
            with patch.object(bootstrap, "ensure_environment", return_value=python), \
                 patch.object(bootstrap, "probe", return_value=info(python, issues=["PySide6缺失"])), \
                 patch.object(bootstrap, "install_requirements", return_value=False), \
                 patch.object(bootstrap.subprocess, "call") as launch:
                self.assertEqual(bootstrap.main([], root=root), 2)
                launch.assert_not_called()

    def test_install_action_repairs_without_starting_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); python = root/"python"
            with patch.object(bootstrap, "ensure_environment", return_value=python), \
                 patch.object(bootstrap, "probe", return_value=info(python)), \
                 patch.object(bootstrap, "install_requirements", return_value=True) as install, \
                 patch.object(bootstrap.subprocess, "call") as launch:
                self.assertEqual(bootstrap.main(["--install"], root=root), 0)
                install.assert_called_once()
                launch.assert_not_called()

    def test_failed_default_download_retries_with_https_mirror_using_same_python(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory)/"python"
            with patch.object(bootstrap.subprocess, "run", return_value=Mock(returncode=0)), \
                 patch.object(bootstrap, "run_logged", side_effect=[1, 0]) as run:
                self.assertTrue(bootstrap.install_requirements(Path(directory), python))
                commands = [call.args[0] for call in run.call_args_list]
                self.assertTrue(all(command[:3] == [python, "-m", "pip"] for command in commands))
                self.assertIn(bootstrap.MIRROR, commands[-1])
                self.assertNotIn("--trusted-host", commands[-1])

    def test_cancelled_install_does_not_restart_against_another_mirror(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bootstrap.subprocess, "run", return_value=Mock(returncode=0)), \
                 patch.object(bootstrap, "run_logged", return_value=130) as run:
                self.assertFalse(bootstrap.install_requirements(Path(directory), Path(directory)/"python"))
                self.assertEqual(run.call_count, 1)

    def test_real_clean_environment_detects_qt_missing_and_direct_run_has_actionable_message(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy(); env.pop("PYTHONPATH", None); env.pop("PYTHONHOME", None)
            env["TQ_STUDIO_BOOTSTRAPPED"] = "1"
            plain = Path(directory)/"plain"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(plain)], check=True,
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            python = plain/("Scripts/python.exe" if os.name == "nt" else "bin/python")
            result = subprocess.run([str(python), "-c", bootstrap.PROBE], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            report = json.loads(result.stdout)
            self.assertTrue(any("PySide6" in issue for issue in report["issues"]))
            root = Path(__file__).resolve().parents[1]
            result = subprocess.run([str(python), str(root/"run.py")], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 2)
            self.assertIn("安装依赖", result.stdout)
            self.assertNotIn("Traceback", result.stdout)


class RuntimePackageTests(unittest.TestCase):
    def package(self, root, extra=None):
        archive = Path(root)/"python.nupkg"
        with zipfile.ZipFile(archive, "w") as z:
            for name in ("tools/python.exe", "tools/Lib/venv/__init__.py", "tools/Lib/ensurepip/__init__.py"):
                z.writestr(name, b"fixture")
            if extra:
                z.writestr(extra, b"bad")
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_verified_runtime_extracts_complete_stdlib_without_running_an_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, checksum = self.package(directory)
            output = Path(directory)/"runtime"
            bootstrap.extract_runtime(archive, output, checksum)
            self.assertTrue((output/"python.exe").is_file())
            self.assertTrue((output/"Lib/venv/__init__.py").is_file())

    def test_checksum_failure_stops_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = self.package(directory)
            output = Path(directory)/"runtime"
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                bootstrap.extract_runtime(archive, output, "not-the-real-checksum")
            self.assertFalse(output.exists())

    def test_traversal_is_rejected_even_with_matching_fixture_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, checksum = self.package(directory, "tools/../outside.exe")
            output = Path(directory)/"runtime"
            with self.assertRaisesRegex(RuntimeError, "非法文件路径"):
                bootstrap.extract_runtime(archive, output, checksum)
            self.assertFalse((Path(directory)/"outside.exe").exists())


if __name__ == "__main__":
    unittest.main()
