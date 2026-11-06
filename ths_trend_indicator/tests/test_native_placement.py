"""Regressions for failures observed in the official native parser/shapes."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from book_layers import Builder
from check_formula import check,FormulaError,Parser
from native_preparse import shape_summary
from native_layers import typed_model,emit
from plot_audit import audit


class NativePlacementTests(unittest.TestCase):
    def test_labels_use_latest_gate_age_price_and_value_on_historical_candle(self):
        # The destination bar is down and has a different count/price/value.
        # Moving only the gate without freezing its inputs gives a wrong label.
        data={
            'O':np.array([9,9,11,11,10,12,12,14],float),
            'C':np.array([10,8,12,10,9,11,13,15],float),
            'H':np.array([11,10,13,12,11,13,14,17],float),
            'L':np.array([8,7,10,9,8,10,11,13],float),
        }
        builder=Builder()
        builder.number('C>O','AA','H','BARPOS*10','COLORYELLOW',1)
        builder.text('C>O','AA','H+4','Q LABEL','COLORCYAN',1)
        source='{ END_ANALYSIS }\nVX:=DATACOUNT-BARPOS;\nAA:=COUNT(C<O,0);\n'+'\n'.join(builder.lines)
        labels=audit(source,data)['labels']
        self.assertEqual([(l['x'],l['y'],l['text'],l['align'])for l in labels],
                         [(3,17.0,'80.0','left'),(3,21.0,'Q LABEL','center')])
        data['O'][-1]=16  # Latest gate false: no historical stale labels.
        self.assertEqual(audit(source,data)['labels'],[])

    def test_dynamic_text_offsets_are_rejected_before_delivery(self):
        # The official pre-parser accepts some wrappers but emits NaN or
        # INT_MIN in shape JSON. Reject the whole dynamic-offset family.
        for x in ['AA','INTPART(AA)','MAX(0,AA)']:
            for call in [f"KTEXT(C>O,{x},H,1,COLORYELLOW,'LABEL');",
                         f'DRAWSHIFTNUMBER(C>O,H,C,2,COLORYELLOW,0,{x});']:
                with self.subTest(call=call),self.assertRaises(FormulaError):
                    check('AA:=BARPOS;\n'+call,enforce_boundary=False)
        check("KTEXT(C>O,0,H,1,COLORYELLOW,'LABEL');",enforce_boundary=False)
        check('DRAWSHIFTNUMBER(C>O,H,C,2,COLORYELLOW,0,5);',enforce_boundary=False)

    def test_invalid_native_shape_offsets_cannot_be_reported_as_passed(self):
        samples=[b'[{"ShapeName":"TextShape","HorizontalOffset":nan}]',
                 b'[{"ShapeName":"TextShape","HorizontalOffset":NaN}]',
                 b'[{"ShapeName":"TextShape","HorizontalOffset":-2147483648.0}]']
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(shape_summary(sample)['status'],'failed')
        valid=shape_summary(b'[{"ShapeName":"TextShape","HorizontalOffset":0.0,"VerticalOffset":0.0}]')
        self.assertEqual(valid['status'],'passed')
        self.assertEqual(valid['max_absolute_offset'],0)

    def test_snapshot_guard_keeps_expensive_scan_inside_last_bar_branch(self):
        statements,_=typed_model('BC:=BARPOS;\n{ END_ANALYSIS }\nAA:=REFX1(IF(ISLASTBAR,HHV(H,BC),0),100000);')
        guarded=[s.expression for s in statements if s.expression[0:2]==('call','IF')]
        self.assertEqual(len(guarded),1)
        self.assertEqual(guarded[0][2][0],('identifier','ISLASTBAR'))
        self.assertEqual(guarded[0][2][1][0:2],('call','HHV'))

    def test_guarded_prefix_has_history_and_boolean_sum_uses_count(self):
        from check_formula import walk
        raw='BC:=BARPOS;FLAG:=C>O;\n{ END_ANALYSIS }\nAA:=REFX1(IF(ISLASTBAR,SUM(FLAG,0),0),100000);'
        statements,_=typed_model(raw)
        prefix=[s for s in statements if s.expression[0:2]==('call','COUNT')and s.expression[2][1]==('number',0.)]
        self.assertEqual(len(prefix),1)
        guarded=[n for s in statements for n in walk(s.expression)if n[0:2]==('call','IF')and n[2][0]==('identifier','ISLASTBAR')]
        self.assertEqual(guarded[0][2][1],('identifier',prefix[0].name))
        self.assertFalse(any(n[0:2]==('call','SUM')for s in statements for n in walk(s.expression)))

    def test_negative_parameter_assignment_has_no_complete_rhs_wrapper(self):
        statement=Parser('DIR:=(0-1);').parse()[0]
        # Parser represents binary parentheses structurally, so this is
        # also safe when a caller edits a negative source parameter.
        self.assertEqual(emit(statement),'DIR:=0-1;')
        statement=Parser('DIR:=-1;').parse()[0]
        self.assertEqual(emit(statement),'DIR:=0-1;')


if __name__=='__main__':unittest.main()
