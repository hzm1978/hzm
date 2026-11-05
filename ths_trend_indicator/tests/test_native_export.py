"""Test actual paste bytes and their complete numerical/drawing equivalence."""
from pathlib import Path
import hashlib
import json
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from check_formula import Parser,walk,FormulaError
from export_native import OUTPUTS,ascii_label,check_delivered,clean_export,load_primary
from generate import build,formula_source
from plot_audit import audit
from scenarios import NAMES,sample


class NativeExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.model=formula_source()
        cls.source,cls.profile=load_primary()
        cls.profiles=json.loads((ROOT/'导出配置.json').read_text(encoding='utf-8'))

    def test_primary_file_is_ascii_without_bom_comments_or_hidden_header(self):
        raw=(ROOT/'formulas'/'EWTN_ASCII.txt').read_bytes()
        self.assertTrue(raw.startswith(b'PN:=2;\r\n'))
        self.assertTrue(all(byte<128 for byte in raw))
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'END_ANALYSIS',raw)
        self.assertNotIn(b'{',raw);self.assertNotIn(b'}',raw)
        self.assertNotIn(b'//',raw);self.assertNotIn(b'/*',raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),self.profile['sha256'])

    def test_all_seven_delivered_files_use_native_export_checks(self):
        self.assertEqual(len(self.profiles['files']),7)
        for name,profile in self.profiles['files'].items():
            with self.subTest(name=name):
                raw=(ROOT/'formulas'/name).read_bytes()
                self.assertFalse(raw.startswith((b'\xef\xbb\xbf',b'\xff\xfe',b'\xfe\xff')))
                self.assertEqual(hashlib.sha256(raw).hexdigest(),profile['sha256'])
                report=check_delivered(raw.decode(profile['encoding']),profile)
                self.assertEqual(report['native_compile'],'not_run')

    def test_hidden_characters_and_leading_blank_line_are_rejected(self):
        profile={'ascii_only':False,'analysis_end_line':None,'comments':False}
        for prefix in ['\ufeff','\u200b','\u2060','\u00a0','\n']:
            with self.subTest(prefix=repr(prefix)),self.assertRaises(FormulaError):
                check_delivered(prefix+'PN:=2;',profile)

    def test_only_catalogued_comment_syntax_is_allowed_by_delivery_check(self):
        profile={'ascii_only':True,'analysis_end_line':None,'comments':True}
        check_delivered('PN:=2;// comment\n/* one\ntwo */\nOUT1:PN;',profile)
        with self.assertRaisesRegex(FormulaError,'brace comment'):
            check_delivered('PN:=2;{model comment}OUT1:PN;',profile)
        with self.assertRaisesRegex(FormulaError,'no comments'):
            check_delivered('PN:=2;// text\n',profile|{'comments':False})

    def test_phase_sidecar_preserves_causal_boundary_without_code_comments(self):
        end=self.profile['analysis_end_line']
        statements=Parser(self.source).parse()
        self.assertEqual(end,len(Parser(self.model.split('{ END_ANALYSIS',1)[0]).parse()))
        self.assertIn('REFX1',{node[1]for node in walk(statements[end].expression)if node[0]=='call'})
        with self.assertRaisesRegex(FormulaError,'leaks into analysis'):
            check_delivered(self.source,self.profile|{'analysis_end_line':end+1})

    def test_every_analysis_statement_is_identical_to_mathematical_model(self):
        old=Parser(self.model.split('{ END_ANALYSIS',1)[0]).parse()
        new=Parser('\n'.join(self.source.splitlines()[:self.profile['analysis_end_line']])).parse()
        signature=lambda s:(s.name,s.expression,s.output,s.styles)
        self.assertEqual([signature(s)for s in old],[signature(s)for s in new])

    def test_all_numeric_and_drawing_arguments_survive_export_unchanged(self):
        def signature(node):
            if node[0]=='string':return ('string','TEXT')
            if node[0]=='identifier':return ('identifier',OUTPUTS.get(node[1],node[1]))
            if node[0]=='call':return ('call',node[1],tuple(signature(a)for a in node[2]))
            if node[0]=='binary':return ('binary',node[1],signature(node[2]),signature(node[3]))
            if node[0]=='unary':return ('unary',node[1],signature(node[2]))
            return node
        def statement(s):return (OUTPUTS.get(s.name,s.name),signature(s.expression),s.output,s.styles)
        self.assertEqual([statement(s)for s in Parser(self.model).parse()],
                         [statement(s)for s in Parser(self.source).parse()])

    def test_hierarchies_targets_retracements_and_specific_patterns_have_legends(self):
        examples={'主5':'M5','子前4回调%':'P4 RET%','孙Q2':'GQ2',
                  '调整A·收盘确认':'A OK','关键位·鬼门关':'KEY RV01',
                  '转折观察·魂断蓝桥':'OBS RV05','1.618H1候选':'1.618H1?'}
        for original,label in examples.items():self.assertEqual(ascii_label(original),label)
        self.assertEqual(len(self.profile['labels']),181)
        self.assertTrue(all(text.isascii()for text in self.profile['labels'].values()))
        with self.assertRaises(FormulaError):ascii_label('未审核的新标注')

    def test_six_scenes_preserve_every_value_coordinate_and_label_event(self):
        for name in NAMES:
            with self.subTest(name=name):
                data,settings=sample(name)
                old=audit(self.model,data,settings)
                new=audit(self.source,data,settings,analysis_end_line=self.profile['analysis_end_line'])
                for key,array in old['env'].items():
                    np.testing.assert_allclose(array,new['env'][OUTPUTS.get(key,key)],
                                               rtol=0,atol=0,equal_nan=True,err_msg=name+':'+key)
                # Physical line numbers change when comments are removed.
                line=lambda x:{k:v for k,v in x.items()if k!='statement'}
                self.assertEqual([line(l)for l in old['lines']],[line(l)for l in new['lines']])
                labels=[]
                for label in old['labels']:
                    label=line(label);label['text']=ascii_label(label['text']);labels.append(label)
                self.assertEqual(labels,[line(l)for l in new['labels']])


if __name__=='__main__':unittest.main()
