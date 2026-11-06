"""Verify every installed layer against the complete independent model."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from compatibility import SETTINGS
from check_formula import Parser,walk
from export_native import load_primary
from generate import build
from native_layers import MAX_INSTALL_BYTES,FIXED_DISPLAY,windows_text
from plot_audit import audit
from scenarios import NAMES,sample


def line_key(line):
    return (tuple(round(float(v),7)for v in line['start']),tuple(round(float(v),7)for v in line['end']),
            line['color'],line['function'],line['styles'])


def label_key(label):
    return (label['x'],round(float(label['y']),7),label['text'],label['align'],label['color'],label['function'],label.get('font_size'))


class InstallationLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.full,cls.full_profile=load_primary()
        cls.profile=json.loads((ROOT/'导出配置.json').read_text(encoding='utf8'))

    def test_all_drawing_roots_are_installed_exactly_once_with_shared_settings(self):
        counts=Counter()
        self.assertEqual(len(self.profile['install']),3)
        self.assertEqual(len(self.profile['recommended_install']),3)
        self.assertEqual(self.profile['optional_install'],[])
        for name in self.profile['install']:
            raw=(ROOT/'formulas'/name).read_bytes()
            profile=self.profile['files'][name]
            self.assertEqual(profile['role'],'install_layer')
            self.assertLessEqual(len(raw.decode(profile['encoding'])),MAX_INSTALL_BYTES)
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(hashlib.sha256(raw).hexdigest(),profile['sha256'])
            counts.update(profile['root_ids'])
            settings={s.name for s in Parser(raw.decode(profile['encoding'])).parse()if s.name in SETTINGS}
            self.assertEqual(settings,SETTINGS-set(FIXED_DISPLAY))
            self.assertFalse({'V','LR','PR','TP','TV'}&set(profile['native_variable_aliases'].values()))
        self.assertEqual(len(counts),self.profile['installation_layers']['drawing_roots'])
        self.assertEqual(set(counts.values()),{1})

    def compare_scene(self,name,overrides=None,recommended=False):
        data,settings=sample(name)
        settings=dict(settings or {})|dict(overrides or {})
        expected=audit(self.full,data,settings,analysis_end_line=self.full_profile['analysis_end_line'])
        lines=[];labels=[]
        for relative in self.profile['recommended_install']if recommended else self.profile['install']:
            profile=self.profile['files'][relative]
            source=(ROOT/'formulas'/relative).read_text(encoding='utf-8')
            result=audit(source,data,settings,analysis_end_line=profile['analysis_end_line'])
            original_names={alias:original for original,alias in profile['native_variable_aliases'].items()}
            for key,array in result['env'].items():
                key=original_names.get(key,key)
                # Installation uses no generated numerical-mask G helpers.
                if (key.startswith('J')and key[1:].isdigit())or(key.startswith('PURE')and key[4:].isdigit()):continue
                self.assertIn(key,expected['env'],name+':'+key)
                np.testing.assert_allclose(array,expected['env'][key],rtol=0,atol=0,equal_nan=True,
                                           err_msg=relative+':'+name+':'+key)
            lines.extend(result['lines']);labels.extend(result['labels'])
        self.assertEqual(Counter(map(line_key,lines)),Counter(map(line_key,expected['lines'])),name)
        self.assertEqual(Counter(map(label_key,labels)),Counter(map(label_key,expected['labels'])),name)

    def test_six_scenes_preserve_every_value_line_and_annotation(self):
        for name in NAMES:
            with self.subTest(name=name):self.compare_scene(name)

    def test_supported_parameters_and_recommended_pair_are_preserved(self):
        self.compare_scene('fall_rebound',{'SHOWGRAND':0},recommended=True)
        self.compare_scene('trend_targets',{'STRONG':1,'SHOWK':0})

    def test_subwave_lines_numbers_and_prices_share_one_complete_layer(self):
        profiles=self.profile['files']
        self.assertEqual(set(profiles['install/EWT02.txt']['families']),{'new_P','prices'})
        self.assertEqual(profiles['install/EWT01.txt']['families'],['new_M'])
        self.assertEqual(profiles['install/EWT03.txt']['families'],['new_S'])
        self.assertFalse((ROOT/'formulas/install/EWT04.txt').exists())

    def test_backset_series_is_materialized_before_native_consumers(self):
        count=0
        for relative in self.profile['install']:
            source=(ROOT/'formulas'/relative).read_text(encoding='utf-8')
            for statement in Parser(source).parse():
                for node in walk(statement.expression):
                    if node[0:2]==('call','BACKSET'):
                        count+=1
                        self.assertTrue(statement.name)
                        self.assertEqual(node,statement.expression)
        self.assertGreater(count,0)

    def test_windows_paste_cannot_push_any_layer_over_the_saving_budget(self):
        baseline=json.loads((ROOT/'tests/fixtures/v1_2_0_install_signatures.json').read_text())
        self.assertGreater(baseline['files']['install/EWT02.txt']['characters_crlf'],80000)
        for relative in self.profile['install']:
            profile=self.profile['files'][relative]
            source=(ROOT/'formulas'/relative).read_bytes().decode(profile['encoding'])
            windows=windows_text(source)
            self.assertLessEqual(len(windows),MAX_INSTALL_BYTES,relative)
            self.assertEqual(windows_text(windows),windows)
            self.assertEqual(len(windows),profile['windows_characters'])

    def test_repacking_preserves_every_published_statement_and_causal_boundary(self):
        baseline=json.loads((ROOT/'tests/fixtures/v1_2_0_install_signatures.json').read_text())['files']
        def digest(statements):
            values=[(s.name,s.expression,s.output,s.styles)for s in statements]
            return hashlib.sha256(json.dumps(values,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
        for relative in self.profile['install']:
            profile=self.profile['files'][relative]
            source=(ROOT/'formulas'/relative).read_bytes().decode(profile['encoding'])
            self.assertEqual(digest(Parser(source).parse()),baseline[relative]['statements_ast_sha256'])
            prefix='\n'.join(source.splitlines()[:profile['analysis_end_line']])
            self.assertEqual(digest(Parser(prefix).parse()),baseline[relative]['analysis_ast_sha256'])
            self.assertEqual(len(Parser(source).parse()),baseline[relative]['statements'])
        for relative in ('install/EWT01.txt','install/EWT03.txt'):
            self.assertEqual(self.profile['files'][relative]['sha256'],baseline[relative]['source_sha256'])

    def test_native_cases_preserve_delivered_crlf_and_editable_parameter_rows(self):
        from native_scenarios import cases
        relative='install/EWT02.txt'
        source=(ROOT/'formulas'/relative).read_bytes().decode('utf8')
        self.assertIn('\r\n',source)
        for s in Parser(source).parse():
            if s.name in SETTINGS:
                row=source.splitlines()[s.line-1]
                self.assertEqual(len(Parser(row).parse()),1,s.name)
        case=next(c for c in cases()if c['name']=='fall_rebound/'+relative)
        self.assertEqual(case['source'].encode('utf8'),(ROOT/'formulas'/relative).read_bytes())


if __name__=='__main__':unittest.main()
