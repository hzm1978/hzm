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
from check_formula import Parser
from export_native import load_primary
from generate import build
from native_layers import MAX_INSTALL_BYTES
from plot_audit import audit
from scenarios import NAMES,sample


def line_key(line):
    return (tuple(map(float,line['start'])),tuple(map(float,line['end'])),
            line['color'],line['function'],line['styles'])


def label_key(label):
    return (label['x'],label['y'],label['text'],label['align'],label['color'],label['function'])


class InstallationLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.full,cls.full_profile=load_primary()
        cls.profile=json.loads((ROOT/'导出配置.json').read_text(encoding='utf8'))

    def test_all_drawing_roots_are_installed_exactly_once_with_shared_settings(self):
        counts=Counter()
        self.assertEqual(len(self.profile['install']),4)
        for name in self.profile['install']:
            raw=(ROOT/'formulas'/name).read_bytes()
            profile=self.profile['files'][name]
            self.assertEqual(profile['role'],'install_layer')
            self.assertLessEqual(len(raw),MAX_INSTALL_BYTES)
            self.assertTrue(raw.isascii())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),profile['sha256'])
            counts.update(profile['root_ids'])
            settings={s.name for s in Parser(raw.decode('ascii')).parse()if s.name in SETTINGS}
            self.assertEqual(settings,SETTINGS)
        self.assertEqual(len(counts),self.profile['installation_layers']['drawing_roots'])
        self.assertEqual(set(counts.values()),{1})

    def compare_scene(self,name,overrides=None):
        data,settings=sample(name)
        settings=dict(settings or {})|dict(overrides or {})
        expected=audit(self.full,data,settings,analysis_end_line=self.full_profile['analysis_end_line'])
        lines=[];labels=[]
        for relative in self.profile['install']:
            profile=self.profile['files'][relative]
            source=(ROOT/'formulas'/relative).read_text(encoding='ascii')
            result=audit(source,data,settings,analysis_end_line=profile['analysis_end_line'])
            for key,array in result['env'].items():
                # Installation uses no generated numerical-mask G helpers.
                if key.startswith('J')and key[1:].isdigit():continue
                self.assertIn(key,expected['env'],name+':'+key)
                np.testing.assert_allclose(array,expected['env'][key],rtol=0,atol=0,equal_nan=True,
                                           err_msg=relative+':'+name+':'+key)
            lines.extend(result['lines']);labels.extend(result['labels'])
        self.assertEqual(Counter(map(line_key,lines)),Counter(map(line_key,expected['lines'])),name)
        self.assertEqual(Counter(map(label_key,labels)),Counter(map(label_key,expected['labels'])),name)

    def test_six_scenes_preserve_every_value_line_and_annotation(self):
        for name in NAMES:
            with self.subTest(name=name):self.compare_scene(name)

    def test_optional_legacy_and_detail_modes_are_preserved(self):
        self.compare_scene('fall_rebound',{'SHOWBOOK':0,'DETAIL':1})
        self.compare_scene('trend_targets',{'DETAIL':1,'STRONG':1})


if __name__=='__main__':unittest.main()
