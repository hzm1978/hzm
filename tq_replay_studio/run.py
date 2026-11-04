import sys
import os
from pathlib import Path
import subprocess
import struct


def gui_main():
    root = Path(__file__).resolve().parent
    if not getattr(sys, "frozen", False):
        from bootstrap import environment_python, same_python
        if not (3, 11) <= sys.version_info[:2] < (3, 14) or struct.calcsize("P") != 8:
            if os.environ.get("TQ_STUDIO_BOOTSTRAPPED") == "1":
                print("独立环境Python仍不兼容，请运行“安装依赖.bat”修复。", flush=True)
                return 2
            print("当前Python不兼容，正在准备本程序的独立环境……", flush=True)
            return subprocess.call([sys.executable, str(root/"bootstrap.py"), *sys.argv[1:]], cwd=root)
        python = environment_python(root)
        if python.is_file() and not same_python(python, sys.executable):
            print("使用本程序独立环境："+str(python), flush=True)
            return subprocess.call([str(python), str(root/"run.py"), *sys.argv[1:]], cwd=root)
    try:
        from tq_workbench.app import main
    except ModuleNotFoundError as exc:
        dependency = (exc.name or "").split(".")[0]
        if dependency not in {"PySide6", "numpy", "pandas", "pyarrow", "tqsdk"}:
            raise
        print(f"缺少运行依赖：{dependency}\n当前Python：{sys.executable}", flush=True)
        if not getattr(sys, "frozen", False) and os.environ.get("TQ_STUDIO_BOOTSTRAPPED") != "1":
            print("正在转到独立环境安装与修复……", flush=True)
            return subprocess.call([sys.executable, str(root/"bootstrap.py"), *sys.argv[1:]], cwd=root)
        print("请运行“安装依赖.bat”修复后，再运行“启动工作台.bat”。", flush=True)
        return 2
    return main()

if __name__ == "__main__":
    if "--market-worker" in sys.argv:
        from tq_workbench.sdk_worker import main
    elif "--history-worker" in sys.argv:
        from tq_workbench.history_worker import main
    elif "--trade-worker" in sys.argv:
        from tq_workbench.trade_worker import main
    else:
        main = gui_main
    raise SystemExit(main())
