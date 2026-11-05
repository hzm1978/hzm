"""Run local checks and persist evidence. This never invokes a native compiler."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest

import numpy

from generate import build
from check_formula import check

ROOT = Path(__file__).resolve().parent


def main():
    build()
    suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    reports = {}
    for path in sorted((ROOT/"formulas").glob("*.txt")):
        data = path.read_bytes()
        encoding = "utf-8-sig" if "UTF8" in path.name else "gb18030"
        source = data.decode(encoding).replace("\r\n", "\n")
        reports[path.name] = check(source, enforce_boundary="兼容检查" not in path.name) | {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "lines": len(source.splitlines()), "encoding": encoding}
    evidence = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "numpy": numpy.__version__,
        "local_tests": {"run": result.testsRun, "failures": len(result.failures),
                        "errors": len(result.errors), "skipped": len(result.skipped),
                        "passed": result.wasSuccessful()},
        "native_compile": {"status": "not_run", "target_client_version": "4.39.1.2",
                           "reason": "No Windows THS Futures PC client in this environment"},
        "files": reports}
    feedback = ROOT/"diagnostics"/"client_compile_feedback.json"
    if feedback.exists():
        evidence["client_compile_feedback"] = json.loads(feedback.read_text(encoding="utf-8"))
    (ROOT/"docs"/"本地检查结果.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if not result.wasSuccessful():
        sys.exit(1)
    print("Local checks passed; native THS compilation NOT performed.")


if __name__ == "__main__":
    main()
