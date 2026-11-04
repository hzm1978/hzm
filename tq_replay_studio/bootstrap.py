"""Standard-library installer and launcher; usable before Qt is installed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from urllib.request import urlopen

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
WINDOWS = os.name == "nt"
RUNTIME_VERSION = "3.13.17"
RUNTIME_URL = "https://api.nuget.org/v3-flatcontainer/python/3.13.17/python.3.13.17.nupkg"
RUNTIME_SHA256 = "dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5"
PROBE = r'''
import importlib, importlib.metadata, importlib.util, json, struct, sys
issues = []
if not (3, 11) <= sys.version_info[:2] < (3, 14):
    issues.append("需要 Python 3.11至3.13，推荐3.12")
if struct.calcsize("P") != 8:
    issues.append("需要64位Python")
packages = {"numpy": ((2,0), (3,0)), "pandas": ((2,2), (3,1)),
            "pyarrow": ((14,0), None), "PySide6": ((6,8), (7,0)), "tqsdk": None}
versions = {}
for name, limits in packages.items():
    try:
        if importlib.util.find_spec(name) is None:
            raise ModuleNotFoundError(name)
        version = importlib.metadata.version("PySide6-Essentials" if name == "PySide6" else name)
        versions[name] = version
        parts = version.split(".")
        numeric = tuple(int("".join(c for c in part if c.isdigit()) or "0") for part in parts[:2])
        if name == "tqsdk" and version != "3.10.4":
            issues.append("tqsdk需要3.10.4，当前"+version)
        elif limits and (numeric < limits[0] or (limits[1] and numeric >= limits[1])):
            issues.append(name+"版本不匹配："+version)
        if name == "PySide6":
            importlib.import_module("PySide6.QtWidgets")
    except Exception as exc:
        issues.append(name+" 未安装完整或无法加载："+str(exc))
print(json.dumps(dict(python=sys.executable, prefix=sys.prefix, version=list(sys.version_info[:3]),
    bits=struct.calcsize("P")*8, versions=versions, issues=issues), ensure_ascii=True))
'''
BASE_PROBE = "import json,struct,sys;print(json.dumps([list(sys.version_info[:2]),struct.calcsize('P')*8]))"


def environment_python(root):
    root = Path(root)
    suffix = "Scripts/python.exe" if WINDOWS else "bin/python"
    try:
        active = json.loads((root/".tq-runtime.json").read_text(encoding="utf-8")).get("environment", "")
        if (isinstance(active, str) and active.startswith(".venv-studio") and
                "/" not in active and "\\" not in active and ":" not in active):
            target = root/active/suffix
            if target.is_file():
                return target
    except (OSError, ValueError, AttributeError):
        pass
    managed = root/".venv-studio"/suffix
    return managed if managed.is_file() else root/".venv"/suffix


def same_python(left, right):
    # Do not resolve symlinks: a venv Python can point to the base binary.
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def probe(python):
    result = subprocess.run([str(python), "-X", "utf8", "-c", PROBE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if result.returncode:
        return dict(python=str(python), version=[], bits=0, versions={},
                    issues=["解释器检查失败："+result.stderr.strip()[-1500:]])
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return dict(python=str(python), version=[], bits=0, versions={}, issues=["解释器未返回有效检查结果"])


def supported_base(command):
    try:
        result = subprocess.run([*command, "-c", BASE_PROBE], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        version, bits = json.loads(result.stdout.strip())
        return result.returncode == 0 and (3, 11) <= tuple(version) < (3, 14) and bits == 64
    except (OSError, ValueError, TypeError):
        return False


def choose_base():
    candidates = []
    launcher = shutil.which("py") if WINDOWS else None
    if launcher:
        candidates.extend([[launcher, version] for version in ("-3.12", "-3.13", "-3.11")])
    candidates.append([sys.executable])
    return next((command for command in candidates if supported_base(command)), None)


def extract_runtime(archive, destination, expected_sha=RUNTIME_SHA256):
    archive, destination = Path(archive), Path(destination)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_sha:
        raise RuntimeError("Python运行时SHA-256校验失败，已停止安装。")
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            if not item.filename.startswith("tools/"):
                continue
            parts = item.filename.split("/")[1:]
            if ".." in parts or "\\" in item.filename or ":" in item.filename:
                raise RuntimeError("Python运行时包含非法文件路径。")
            parts = [part for part in parts if part]
            if not parts:
                continue
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Python运行时不允许符号链接。")
            target = destination.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    required = ("python.exe", "Lib/venv/__init__.py", "Lib/ensurepip/__init__.py")
    if any(not (destination/name).is_file() for name in required):
        raise RuntimeError("Python运行时未包含完整的venv／pip组件。")


def private_python(root):
    root = Path(root)
    parent = root/".python-tq"; parent.mkdir(parents=True, exist_ok=True)
    folder = parent/RUNTIME_VERSION
    python = folder/"python.exe"
    if python.is_file() and supported_base([str(python)]):
        return [str(python)]
    print("未找到兼容Python。正在为本程序下载官方64位Python "+RUNTIME_VERSION+
          "（约15MB，不修改系统Python）……", flush=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=parent) as directory:
        temporary = Path(directory)
        archive = temporary/"python.nupkg"
        with urlopen(RUNTIME_URL, timeout=30) as response, archive.open("wb") as output:
            total, reported = 0, 0
            while True:
                data = response.read(1024*1024)
                if not data:
                    break
                output.write(data); total += len(data)
                if total-reported >= 4*1024*1024:
                    print("Python已下载 %.1f MB" % (total/(1024*1024)), flush=True)
                    reported = total
        extracted = temporary/"runtime"
        extract_runtime(archive, extracted)
        if not supported_base([str(extracted/"python.exe")]):
            raise RuntimeError("独立Python无法运行，请确认使用64位Windows并查看上方错误。")
        if folder.exists():
            folder.rename(parent/(RUNTIME_VERSION+".previous-"+uuid.uuid4().hex[:8]))
        extracted.rename(folder)
    print("专用Python已准备："+str(python), flush=True)
    return [str(python)]


def run_logged(command, root):
    """Stream install progress to console and a local setup log, without a shell."""
    log_path = Path(root) / "setup.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen([str(x) for x in command], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
            errors="replace", bufsize=1)
        try:
            for line in process.stdout:
                print(line, end="", flush=True); log.write(line); log.flush()
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            print("\n安装已中断。重新运行启动工作台即可继续。", flush=True)
            return 130


def ensure_environment(root):
    root = Path(root)
    python = environment_python(root)
    if python.is_file():
        info = probe(python)
        version = tuple(info.get("version", [])[:2])
        if not ((3, 11) <= version < (3, 14) and info.get("bits") == 64):
            print("原环境Python："+(".".join(str(x) for x in info.get("version", [])) or "不可用")+
                  " / "+str(info.get("bits", 0))+"位。正在准备新的兼容环境；原环境保留。", flush=True)
        else:
            return python
    base = choose_base()
    if base is None and WINDOWS:
        base = private_python(root)
    if base is None:
        raise RuntimeError("没有找到64位Python3.11至3.13。请安装64位Python3.12：https://www.python.org/downloads/")
    target = root/".venv-studio"
    if target.exists():
        target = root/(".venv-studio-"+uuid.uuid4().hex[:8])
    python = target/("Scripts/python.exe" if WINDOWS else "bin/python")
    print("正在创建本程序独立环境："+str(python.parent.parent), flush=True)
    if run_logged([*base, "-m", "venv", str(target)], root) != 0:
        raise RuntimeError("创建环境未完成，请将源码完整解压到可写目录，并查看setup.log。")
    if not python.is_file():
        raise RuntimeError("环境创建后未找到Python，请查看setup.log。")
    marker = root/".tq-runtime.json"
    marker.write_text(json.dumps(dict(environment=target.name), ensure_ascii=True)+"\n", encoding="utf-8")
    return python


def install_requirements(root, python):
    print("安装与启动固定使用："+str(python), flush=True)
    pip = subprocess.run([str(python), "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if pip.returncode and run_logged([python, "-m", "ensurepip", "--upgrade"], root):
        return False
    command = [python, "-m", "pip", "install", "--disable-pip-version-check",
        "--prefer-binary", "--retries", "2", "--timeout", "30",
        "-r", str(Path(root)/"requirements.txt")]
    result = run_logged(command, root)
    if result == 130:
        return False
    if result:
        print("默认源未完成安装，正在通过清华HTTPS镜像重试……", flush=True)
        result = run_logged([*command, "--index-url", MIRROR], root)
    return result == 0


def prepare(root, force_install=False):
    python = ensure_environment(root)
    info = probe(python)
    if force_install or info["issues"]:
        if info["issues"]:
            print("检测到依赖需要补齐：\n  "+" \n  ".join(info["issues"]), flush=True)
        if not install_requirements(root, python):
            raise RuntimeError("依赖没有安装完成，程序尚未启动。安装日志："+str(Path(root)/"setup.log"))
        info = probe(python)
    if info["issues"]:
        raise RuntimeError("依赖检查仍未通过：\n  "+"\n  ".join(info["issues"])+"\n请查看setup.log中的安装结果。")
    print("环境检查通过："+str(python), flush=True)
    return python


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description="天勤复盘工作台 · 环境安装与启动")
    parser.add_argument("--install", action="store_true", help="安装或修复依赖，不启动界面")
    parser.add_argument("--check", action="store_true", help="只检查独立环境")
    parser.add_argument("--build", action="store_true", help="在同一环境构建可执行程序")
    options, forwarded = parser.parse_known_args(argv)
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    try:
        if options.check:
            python = environment_python(root)
            if not python.is_file():
                raise RuntimeError("独立环境尚未创建。请双击启动工作台.bat，程序会自动创建并安装依赖。")
            info = probe(python)
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 2 if info["issues"] else 0
        python = prepare(root, options.install)
        if options.install:
            print("安装完成。现在可运行启动工作台.bat。", flush=True)
            return 0
        if options.build:
            if run_logged([python, "-m", "pip", "install", "pyinstaller"], root):
                raise RuntimeError("构建依赖安装未完成，请查看setup.log。")
            return run_logged([python, "-m", "PyInstaller", "--noconfirm", "--clean", "--console",
                "--name", "TqReplayStudio", "--collect-all", "tqsdk",
                "--hidden-import", "tq_workbench.sdk_worker", "--hidden-import", "tq_workbench.history_worker",
                "--hidden-import", "tq_workbench.trade_worker", str(root/"run.py")], root)
        env = os.environ.copy()
        env["TQ_STUDIO_BOOTSTRAPPED"] = "1"
        return subprocess.call([str(python), str(root/"run.py"), *forwarded], cwd=root, env=env)
    except (OSError, RuntimeError) as exc:
        print("\n启动未完成："+str(exc), flush=True)
        try:
            with (root/"setup.log").open("a", encoding="utf-8") as log:
                log.write("启动错误："+str(exc)+"\n")
        except OSError:
            pass
        print("日志位置："+str(root/"setup.log"), flush=True)
        print("若专用Python无法下载，可先安装64位Python3.12或3.13再重试：https://www.python.org/downloads/", flush=True)
        return 2
    except KeyboardInterrupt:
        print("\n操作已中断。", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
