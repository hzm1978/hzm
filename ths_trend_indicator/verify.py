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
from export_native import check_delivered
from native_layers import windows_text
from catalog import evidence as catalog_evidence

ROOT = Path(__file__).resolve().parent


def main():
    build()
    suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    reports = {}
    profiles=json.loads((ROOT/'导出配置.json').read_text(encoding='utf-8'))['files']
    for relative,profile in sorted(profiles.items()):
        path=ROOT/'formulas'/relative
        data = path.read_bytes()
        encoding = profile['encoding']
        source = data.decode(encoding).replace("\r\n", "\n")
        reports[relative] = check_delivered(source,profile) | {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "characters":len(data.decode(encoding)),"windows_characters":len(windows_text(source)),
            "lines": len(source.splitlines()), "encoding": encoding,'bom':False,
            'ascii_only':source.isascii(),'analysis_end_line':profile['analysis_end_line'],'role':profile['role']}
    evidence = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "numpy": numpy.__version__,
        "local_tests": {"run": result.testsRun, "failures": len(result.failures),
                        "errors": len(result.errors), "skipped": len(result.skipped),
                        "passed": result.wasSuccessful()},
        "native_compile": {"status": "not_run", "target_client_version": "4.39.1.2",
                           "reason": "No Windows THS Futures PC client in this environment"},
        "files": reports}
    # An optional actual DLL report is separate from this local interpreter.
    # Never silently reuse evidence for different source bytes or a different
    # harness. The target 4.39 UI remains untested even when this passes.
    native_path=ROOT/'docs'/'官方解析检查.json'
    if native_path.exists():
        native=json.loads(native_path.read_text(encoding='utf-8'))
        current=(native.get('harness_sha256')==hashlib.sha256((ROOT/'native_preparse.py').read_bytes()).hexdigest()
                 and all(name in reports and item['source_sha256']==reports[name]['sha256']
                         for name,item in native.get('files',{}).items()))
        evidence['official_native_preparse']={'report':'官方解析检查.json','evidence_matches_current_bytes':current,
                                             'parser_client_version':native.get('parser_client_version'),
                                             'passed':current and native.get('passed',False),
                                             'target_client_ui_run':False,'network_content_check':'not_run'}
    function_evidence=catalog_evidence(fn for report in reports.values() for fn in report['functions'])
    (ROOT/'docs'/'使用函数核对.json').write_text(json.dumps(function_evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    evidence['function_catalog']={k:v for k,v in function_evidence.items() if k!='used'}
    evidence['development_budgets']={'note':'Project review budgets, NOT published client limits',
                                    'source_gb18030_bytes':524288,'variables':4096}
    feedback = ROOT/"diagnostics"/"client_compile_feedback.json"
    if feedback.exists():
        evidence["client_compile_feedback"] = json.loads(feedback.read_text(encoding="utf-8"))
    (ROOT/"docs"/"本地检查结果.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if not result.wasSuccessful():
        sys.exit(1)
    print("Local checks passed; native THS compilation NOT performed.")


if __name__ == "__main__":
    main()
