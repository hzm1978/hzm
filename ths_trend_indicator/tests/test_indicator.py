from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check_formula import FormulaError, Parser, RESERVED, check, walk, evaluate as raw_evaluate
from compatibility import SETTINGS, normalize, variable_names, render
from generate import build, core, drawing, probe, basic_probe, formula_source, formula_names
from plot_audit import audit

SOURCE = formula_source()
NAMES = formula_names()
REVERSE_NAMES = {native: original for original, native in NAMES.items()}


def evaluate(source, data, overrides=None):
    # The tests retain the semantic names used by the independent mathematical
    # expectations; the interpreter evaluates the actual TT-prefixed formula.
    overrides = {NAMES.get(k, k): v for k, v in (overrides or {}).items()}
    values = raw_evaluate(source, data, overrides)
    return {REVERSE_NAMES.get(k, k): v for k, v in values.items()}


def bars(n=360, seed=7):
    rng = np.random.default_rng(seed)
    c = 4000 + np.cumsum(rng.integers(-40, 41, n))
    o = c + rng.choice([-17, -9, -3, 4, 8, 13], n)
    return {"C": c, "O": o, "H": np.maximum(c, o)+rng.integers(1, 20, n),
            "L": np.minimum(c, o)-rng.integers(1, 20, n)}


def waves():
    prices = [4500, 4800, 4000, 4400, 3700, 4100, 3400, 3800,
              3100, 3500, 2800, 3200, 2500, 2900, 2200, 2550]
    c = np.concatenate([np.linspace(a, b, 22, endpoint=False)
                        for a, b in zip(prices, prices[1:])]+[np.array([prices[-1]])])
    o = np.r_[c[0]-1, c[:-1]]
    return {"C": c, "O": o, "H": np.maximum(c, o)+1,
            "L": np.minimum(c, o)-1}


def slice_data(data, length):
    return {k: v[:length] for k, v in data.items()}


class SyntaxTests(unittest.TestCase):
    def test_generated_sources_and_encodings_are_identical(self):
        samples = build()
        for name, text in samples.items():
            for suffix, encoding in (("UTF8", "utf-8-sig"), ("GB18030", "gb18030")):
                path = ROOT/"formulas"/f"{name}_{suffix}.txt"
                self.assertEqual(path.read_bytes().decode(encoding).replace("\r\n", "\n"), text)

    def test_whole_formula_static_check(self):
        report = check(SOURCE)
        self.assertGreater(report["statements"], 400)
        self.assertEqual(report["native_compile"], "not_run")
        self.assertIn("DRAWNUMBER1", report["functions"])
        self.assertIn("DRAWNUMBER", report["functions"])
        self.assertNotIn("DRAWSHIFTNUMBER", report["functions"])
        self.assertLessEqual(report["outputs"], 16)
        self.assertLessEqual(report["variables"], 2048)
        self.assertTrue(report["future_calls"])

    def test_compatibility_probe(self):
        check(probe(), enforce_boundary=False)

    def test_drawnumber_uses_five_argument_native_signature(self):
        with self.assertRaisesRegex(FormulaError, "expects 5 args"):
            check("DRAWNUMBER(ISLASTBAR,C,C);")
        check("DRAWNUMBER(ISLASTBAR,C,C,2,COLORCYAN);")

    def test_rejects_wrong_numeric_arity(self):
        for source in ("DRAWNUMBER1(ISLASTBAR,C,C);", "DRAWNUMBER1(ISLASTBAR,C,C,2,1);"):
            with self.subTest(source=source), self.assertRaisesRegex(FormulaError, "expects 4 args"):
                check(source)

    def test_rejects_wrong_line_arity(self):
        with self.assertRaisesRegex(FormulaError, "expects 5 args"):
            check("DRAWLINE(C>O,L,C<O,H);")

    def test_rejects_undefined_forward_and_duplicate_variables(self):
        for source in ("AA:=BB+1;BB:=2;", "AA:=1;aa:=2;", "H:=1;", "RANGE:=H-L;"):
            with self.subTest(source=source), self.assertRaises(FormulaError):
                check(source)

    def test_rejects_missing_semicolon_and_unclosed_comment(self):
        for source in ("AA:=1", "AA:=1; { unfinished", "AA:=IF(C>O,C);", "AA:=C$O;"):
            with self.subTest(source=source), self.assertRaises(FormulaError):
                check(source)

    def test_rejects_future_function_in_analysis(self):
        for fn in ("BACKSET", "REFX1"):
            with self.subTest(fn=fn), self.assertRaisesRegex(FormulaError, "leaks into analysis"):
                check(f"AA:={fn}(C>O,2);\n{{ END_ANALYSIS }}\nBB:=AA;")

    def test_rejects_negative_reference(self):
        with self.assertRaisesRegex(FormulaError, "negative REF"):
            check("AA:=REF(C,-1);")

    def test_rejects_unreviewed_style(self):
        with self.assertRaisesRegex(FormulaError, "unreviewed style"):
            check("均线:C,UNVERIFIEDSTYLE;")

    def test_source_below_development_budget(self):
        # Version 1.1 contains bounded degree/correction layers. This is our
        # own budget, never asserted to be a native editor's documented limit.
        self.assertLess(len(SOURCE.encode("gb18030")), 262144)
        self.assertLess(len(SOURCE.replace("\n", "\r\n").encode("utf-8-sig")), 262144)

    def test_rejects_futures_trade_names_from_client_error(self):
        for name in ("SK", "BK", "BP", "SP", "BPK", "SPK", "CLOSEOUT"):
            with self.subTest(name=name), self.assertRaisesRegex(FormulaError, "reserved name"):
                check(f"{name}:=IF(C>O,C-L,H-C);")

    def test_rejects_original_screenshot_ranking_expression(self):
        with self.assertRaisesRegex(FormulaError, "requires number, got condition"):
            check("E01:=1;E02:=2;R40:=1+(E01>=E02);")

    def test_rejects_conditions_used_as_arithmetic_numbers(self):
        for op in ("+", "-", "*", "/"):
            for expr in (f"2{op}(C>O)", f"(C>O){op}2"):
                with self.subTest(expr=expr), self.assertRaisesRegex(FormulaError, "requires number"):
                    check(f"VALUE1:={expr};")

    def test_rejects_conditional_input_in_numeric_function_positions(self):
        for expr in ("SUM(C>O,5)", "MAX(C>O,1)", "REF(C>O,1)",
                     "IFELSE(C>O,C>L,L)", "DRAWNUMBER1(C>O,C,C>L,2)"):
            with self.subTest(expr=expr), self.assertRaisesRegex(FormulaError, "requires number"):
                check(expr+";" if expr.startswith("DRAW") else "VALUE1:="+expr+";")

    def test_rejects_bare_numeric_condition_arguments(self):
        for expr in ("IFELSE(1,C,O)", "COUNT(1,5)", "BARSLAST(C)", "DRAWTEXT(1,C,'x')"):
            with self.subTest(expr=expr), self.assertRaisesRegex(FormulaError, "requires condition"):
                check(expr+";" if expr.startswith("DRAW") else "VALUE1:="+expr+";")

    def test_explicit_number_condition_casts_pass_strict_profile(self):
        result = check("VALUE1:=IFELSE(C>O,1,0);VALUE2:=1+IFELSE(VALUE1<>0,1,0);VALUE3:=SUM(VALUE1,5);VALUE4:=COUNT(VALUE1<>0,5);")
        self.assertEqual(result["type_check"], "passed")

    def test_all_generated_internal_variables_have_safe_namespace(self):
        for source in (SOURCE, probe()):
            for st in Parser(source).parse():
                if st.name and st.name.isascii() and st.name not in SETTINGS:
                    self.assertNotIn(st.name, RESERVED)
                    self.assertLessEqual(len(st.name), 8)
        self.assertEqual(NAMES["SK"], "TTSTD")

    def test_normalizer_handles_both_rankings_boolean_branches_and_numeric_flags(self):
        raw = "SK:=IF(C>O,C-L,H-C);FLAG:=C>O;RANK:=1+(C>O)+(FLAG AND L>0);VAL:=IF(FLAG,H>0,L>0);TOTAL:=SUM(C>O,5);"
        converted = normalize(raw)
        check(converted)
        mapping = variable_names(raw)
        self.assertNotIn("SK:=", converted)
        a = raw_evaluate(raw, bars(60))
        b = raw_evaluate(converted, bars(60))
        for name in ("SK", "FLAG", "RANK", "VAL", "TOTAL"):
            np.testing.assert_allclose(a[name], b[mapping[name]])

    def test_reference_selection_forms_are_not_universal_language_bans(self):
        # Public examples contain both forms; the user's passing COUNT probe
        # motivates our exporter strategy, not a claim that IF is always illegal.
        check("TTSTYPE:=IF(C>O,1,-1);")
        check("TTSTYPE:=IF(C>O,1,(0-1));")
        check("TTSTYPE:=IFELSE(C>O,1,-1);")
        check("TTSTYPE:=2*IFELSE(C>O,1,0)-1;")

    def test_all_emitted_expressions_exclude_unary_signs(self):
        for source in (SOURCE, probe()):
            for st in Parser(source).parse():
                self.assertFalse(any(node[0] == "unary" for node in walk(st.expression)), st.name)

    def test_all_negative_if_arguments_become_grouped_subtractions(self):
        original = "VALUE1:=IF(C>O,1,-1);VALUE2:=IF(C>O,-2,-3);VALUE3:=-1;"
        source = normalize(original)
        check(source)
        self.assertIn(":=(0-1);", source)
        data = bars(60)
        before, after = raw_evaluate(original, data), raw_evaluate(source, data)
        mapping = variable_names(original)
        for name in ("VALUE1", "VALUE2", "VALUE3"):
            np.testing.assert_allclose(before[name], after[mapping[name]])

    def test_sign_rewrite_retains_arithmetic_precedence(self):
        data = bars(50)
        for expr in ("-1", "-C", "-(C+O)", "C*-2", "C- -2",
                     "C/(-C)", "-(-C)", "IF(C>O,-1,-2)",
                     "IF(-C<0,-1,C)", "MIN(-C,0)", "C/(-(C+O))", "+C"):
            with self.subTest(expr=expr):
                original = "VALUE1:="+expr+";"
                normalized = normalize(original)
                check(normalized)
                a = raw_evaluate(original, data)["VALUE1"]
                b = raw_evaluate(normalized, data)[variable_names(original)["VALUE1"]]
                np.testing.assert_allclose(a, b)

    def test_negative_lookback_cannot_hide_inside_zero_minus_one(self):
        with self.assertRaisesRegex(FormulaError, "negative REF"):
            check("VALUE1:=REF(C,(0-1));")
        for expr in ("HHV(C,(0-1))", "COUNT(C>O,(0-1))", "MA(C,3-4)", "SUM(C,0-1)"):
            with self.subTest(expr=expr), self.assertRaisesRegex(FormulaError, "invalid literal lookback"):
                check("VALUE1:="+expr+";")

    def test_pivot_selection_simplification_matches_original_truth_table(self):
        # Verify the actual production line 166 without relying on the renderer
        # to compute the expected truth table.
        st = next(st for st in Parser(SOURCE).parse() if st.name == NAMES["STYPE"])
        self.assertEqual(st.expression[0], "binary")
        self.assertFalse(any(node[0] == "call" and node[1] == "IF"
                             for node in walk(st.expression)))
        for hs, ls, ht, lt, expected in ((1,0,4,1,1), (0,1,1,4,-1),
                                        (1,1,1,3,1), (1,1,3,1,-1), (0,0,0,0,-1)):
            data = bars(60)
            e = evaluate(SOURCE, data, {"SHS":hs, "SLS":ls, "SHT":ht, "SLT":lt})
            self.assertTrue(np.all(e["STYPE"] == expected))

    def test_previous_failed_selections_are_removed_by_exporter(self):
        prefix = "TTSHS:=1;TTSLS:=1;TTSHT:=1;TTSLT:=2;"
        for tail in ("(0-1)", "0"):
            old = prefix+"TTSTYPE:=IF(TTSHS<>0 AND (TTSLS=0 OR TTSHT<TTSLT),1,"+tail+");"
            with self.subTest(tail=tail):
                report = check(normalize(old))
                self.assertEqual(report["conditional_calls"], {})

    def test_no_compound_logic_remains_in_exported_expressions(self):
        for source in (SOURCE, probe()):
            for st in Parser(source).parse():
                self.assertFalse(any(n[0] == "binary" and n[1] in ("AND", "OR")
                                     for n in walk(st.expression)), st.name)

    def test_compound_conditions_lower_to_equivalent_numerical_flags(self):
        original = "A1:=C>O;B1:=H>C;D1:=L>O;VALUE1:=IF(A1 AND (B1 OR D1),H,L);VALUE2:=(A1 OR B1) AND (A1 OR D1);"
        rewritten = normalize(original)
        check(rewritten)
        mapping = variable_names(original)
        data = bars(100)
        before, after = raw_evaluate(original, data), raw_evaluate(rewritten, data)
        for key in mapping:
            np.testing.assert_allclose(before[key], after[mapping[key]])

    def test_lifted_condition_preserves_historical_ref_scope(self):
        original = "A1:=C>O;B1:=H>REF(H,1);VALUE1:=REF(IF(A1 AND B1,C,O),MIN(BARPOS-1,2));VALUE2:=IF(A1 OR B1,REF(C,1),O);"
        rewritten = normalize(original)
        check(rewritten)
        mapping = variable_names(original)
        data = bars(90)
        before, after = raw_evaluate(original, data), raw_evaluate(rewritten, data)
        for key in mapping:
            np.testing.assert_allclose(before[key], after[mapping[key]])

    def test_native_selection_only_guards_nullable_catalog_lookups(self):
        for source in (SOURCE, probe(), basic_probe()):
            functions = {n[1] for st in Parser(source).parse() for n in walk(st.expression) if n[0] == "call"}
            self.assertIn("COUNT", functions)
            self.assertNotIn("IF", functions)
            for st in Parser(source).parse():
                for node in walk(st.expression):
                    if node[0:2]==('call','IFELSE'):
                        self.assertTrue(any(n[0:2]==('call','ISNULL')for n in walk(node[2][0])))
            identifiers = {n[1] for st in Parser(source).parse() for n in walk(st.expression) if n[0] == "identifier"}
            self.assertFalse(identifiers & {"NULL", "DRAWNULL"})

    def test_native_drawline_fifth_argument_is_color_not_expand_flag(self):
        with self.assertRaisesRegex(FormulaError, "requires color"):
            check("DRAWLINE(C>O,L,C<O,H,0),COLORCYAN;", enforce_boundary=False)
        check("DRAWLINE(C>O,L,C<O,H,COLORCYAN);", enforce_boundary=False)

    def test_public_qht_value_and_color_call_forms(self):
        # Function forms from the cited QHT video description. This is a local
        # parsing baseline, not a claim that the video/native client was run.
        source = """W1:=1;
HT:=IFELSE(OPEN>CLOSE,OPEN,CLOSE);
LT:=IFELSE(OPEN<CLOSE,OPEN,CLOSE);
DRAWLINE(W1=1,HIGH,W1=1,HT,COLORCYAN);
DRAWLINE(W1=1,LOW,W1=1,LT,COLORCYAN);
N1:=IFELSE(CLOSE>OPEN,-3,1);
N2:=IFELSE(H<REF(H,1)&&REF(H,1)<REF(H,2),REF(H,2),0);
"""
        check(source, enforce_boundary=False)

    def test_nullable_selection_cannot_be_silently_rewritten_as_zero_times_null(self):
        for null in ("NULL", "DRAWNULL"):
            with self.subTest(null=null), self.assertRaisesRegex(FormulaError, "NULL cannot enter numerical masks"):
                normalize(f"EMPTY:=IF(C>O,C,{null});")

    def test_export_rejects_negative_history_before_clamping(self):
        for window in ("-1", "0-1", "1.5"):
            with self.subTest(window=window), self.assertRaises(FormulaError):
                normalize(f"VALUE1:=REF(C,{window});")

    def test_passing_native_probe_is_delivered_unchanged(self):
        expected = "QFLAG:=COUNT(C>O,1);\nQOTHER:=COUNT(C<O,1);\nQSIGN:=MAX(QFLAG,QOTHER)+QFLAG-QOTHER-1;\n检查:QSIGN;\n"
        self.assertEqual(basic_probe(), expected)
        check(expected)
        data = {"C":np.array([2,1,1]), "O":np.array([1,2,1]),
                "H":np.array([3,3,2]), "L":np.array([0,0,0])}
        e = raw_evaluate(expected, data)
        np.testing.assert_array_equal(e["QSIGN"], [1,-1,-1])

    def test_conditional_price_segments_do_not_create_nullable_plot_outputs(self):
        statements = Parser(SOURCE).parse()
        self.assertEqual({s.name for s in statements if s.output},
                         {"工作方向", "末段状态", "方向模式", "标准K", "工作回调百分比", "价格尺方向"})
        self.assertFalse(any(s.expression[0:2] == ("call","DRAWLINE") for s in statements))
        lines = [s for s in statements if s.expression[0:2] == ("call", "POLYLINE")]
        self.assertGreaterEqual(len(lines),10)
        self.assertTrue(any(s.expression[0:2]==('call','DRAWLINE3')for s in statements))

    def test_qht_drawline_cannot_join_predicates_on_different_candles(self):
        data=bars(10)
        separate="BC:=BARPOS;\n{ END_ANALYSIS }\nDRAWLINE(BARPOS=2,L,BARPOS=5,H,COLORCYAN);"
        self.assertEqual(audit(separate,data)['lines'],[])
        simultaneous=separate.replace('BARPOS=5','BARPOS=2')
        lines=audit(simultaneous,data)['lines']
        self.assertEqual(len(lines),1)
        self.assertEqual(lines[0]['start'][0],lines[0]['end'][0])


class NumericalTests(unittest.TestCase):
    def test_break_of_known_work_low_invalidates_stale_up_direction(self):
        c=np.r_[np.full(65,110.),99.,98.,97.,98.,99.]
        data={'C':c,'O':c+1,'H':c+2,'L':c-2}
        known=np.r_[np.zeros(20),np.ones(50)]
        e=evaluate(SOURCE,data,{'BK3':known,'BK1':known,'BH0':130.,'BH1':120.,
                                'BL0':100.,'BL1':90.})
        self.assertTrue(np.all(e['WD'][20:65]==1))
        self.assertTrue(np.all(e['WD'][65:]==-1))
        # Old higher high/low flags remain true; they must not overwrite the
        # fresh downside close on the next candle.
        self.assertTrue(np.all(e['DS'][65:]==1))

    def test_long_fall_rebound_keeps_whole_work_wave_and_seven_fib_segments(self):
        data=waves();e=evaluate(SOURCE,data)
        self.assertEqual(e['WD'][-1],-1)
        self.assertEqual(e['PHASE'][-1],2)
        self.assertEqual(e['FSTART'][-1],4801)
        self.assertEqual(e['FEND'][-1],2199)
        self.assertAlmostEqual(e['RET'][-1],(2550-2199)/(4801-2199)*100)
        result=audit(SOURCE,data)
        fib=[line for line in result['lines'] if line['color']=='COLORFFCC00']
        targets=[line for line in result['lines'] if line['color']=='COLORFF9900']
        self.assertEqual(len(fib),7);self.assertEqual(targets,[])
        for line in fib:
            self.assertLess(line['start'][0],line['end'][0])
            self.assertEqual(line['start'][1],line['end'][1])
            self.assertEqual(line['end'][0],len(data['C'])-1)
        self.assertGreater(len([line for line in result['lines'] if line['color']=='COLORCYAN']),8)

    def test_default_annotation_density_is_lower_than_detail_mode(self):
        data=waves()
        normal=audit(SOURCE,data)
        detail=audit(SOURCE,data,{NAMES.get('DETAIL','DETAIL'):1})
        # The full book layer must survive the concise mode. Additional wave,
        # retracement and A labels make the old "half of detail" budget stale.
        self.assertLess(len(normal['labels']),len(detail['labels']))
        for text in ('子前1','子前2','子前3','子前4','子前5','子前2回调%','子前4回调%'):
            self.assertTrue(any(label['text']==text for label in normal['labels']))
            self.assertTrue(any(label['text']==text for label in detail['labels']))
        self.assertFalse(any('Q收破' in label['text'] for label in normal['labels']))

    def test_sideways_without_trend_uses_last_complete_leg_fib_only(self):
        args={'WD':0,'KIND':3,'BK1':1,'BP0':3900.,'BP1':4100.,'BA1':30.}
        e=evaluate(SOURCE,bars(90),args)
        self.assertEqual(e['PHASE'][-1],3)
        self.assertTrue(e['FON'][-1])
        self.assertFalse(e['TON'][-1])
        self.assertEqual(e['F0'][-1],3900)
        self.assertEqual(e['F100'][-1],4100)

    def test_three_target_lines_have_prices_and_ratios_inside_their_segments(self):
        data={k:np.full(100,v) for k,v in (('O',3305.),('C',3300.),('H',3310.),('L',3290.))}
        settings={'DIR':-1,'KIND':1,'MH1':200,'MBASE':3500,'MORG':4000,'MAGE':5}
        result=audit(SOURCE,data,{NAMES.get(k,k):v for k,v in settings.items()})
        targets=[line for line in result['lines'] if line['color']=='COLORFF9900']
        self.assertEqual(len(targets),3)
        self.assertFalse(any(line['color']=='COLORFFCC00'for line in result['lines']))
        for line in targets:
            self.assertGreater(line['end'][0]-line['start'][0],30)
            matched=[label for label in result['labels'] if label['color']=='COLORFF9900'
                     and label['y']==line['start'][1]]
            self.assertEqual(len(matched),2)
            for label in matched:
                self.assertLess(line['start'][0],label['x'])
                self.assertLess(label['x'],line['end'][0]-3)

    def test_count_selection_handles_nested_branches_zero_and_negative_prices(self):
        original = "A1:=IF(C>O,C-L,H-C);A2:=IF(C<O,-C,IF(C=O,0,O));A3:=REF(IF(C>O,A1,A2),2);"
        converted = normalize(original)
        mapping = variable_names(original)
        data = {"O":np.array([-3,0,2,-4,5]), "C":np.array([-2,0,-1,-5,8]),
                "L":np.array([-4,-1,-2,-6,4]), "H":np.array([-1,1,3,-3,9])}
        before, after = raw_evaluate(original,data), raw_evaluate(converted,data)
        for name, native in mapping.items():
            np.testing.assert_allclose(after[native],before[name])

    def test_actual_exported_ref_windows_never_exceed_loaded_history(self):
        analysis = SOURCE.split("{ END_ANALYSIS",1)[0]
        windows = sorted({render(n[2][1]) for st in Parser(analysis).parse()
                          for n in walk(st.expression) if n[0:2] == ("call","REF")})
        instrumented = analysis+"\n"+"\n".join(f"WIN{i}:={expr};" for i,expr in enumerate(windows))
        for length in (1,2,14,40,90):
            with self.subTest(length=length):
                e = raw_evaluate(instrumented,bars(length))
                for i in range(len(windows)):
                    age = e[f"WIN{i}"]
                    self.assertTrue(np.all(age>=0), windows[i])
                    self.assertTrue(np.all(age<=np.arange(length)), windows[i])
                    np.testing.assert_array_equal(age,np.floor(age))

    def test_current_trend_reference_segment_reaches_its_real_anchor(self):
        e = evaluate(SOURCE,waves())
        for t in np.flatnonzero(e["LINEOK"]):
            if e["WD"][t] == 1:
                slope, age, price = e["LSLOPE"][t], e["LAGE1"][t], e["BL1"][t]
                pivot_slope=e["HSLOPE"][t]
                shifted_age, shifted_price = e["LAGE0"][t], e["BL0"][t]
            else:
                slope, age, price = e["HSLOPE"][t], e["HAGE1"][t], e["BH1"][t]
                pivot_slope=e["LSLOPE"][t]
                shifted_age, shifted_price = e["HAGE0"][t], e["BH0"][t]
            self.assertAlmostEqual(e["TRENDV"][t]-slope*age,price)
            self.assertAlmostEqual(e["PIVOTV"][t]-pivot_slope*shifted_age,shifted_price)

    def test_unchanged_measurements_still_match_previous_release(self):
        reference = (ROOT/"tests"/"fixtures"/"v1_0_2_math_reference.txt").read_text(encoding="utf-8")
        data = bars(180)
        cases = [{}, {"DIR":-1,"KIND":1,"MH1":200,"MBASE":4200,"MORG":4500,"MAGE":50},
                 {"DIR":-1,"KIND":2,"MANF":1,"MFROM":4800,"MTO":3100,"MFAGE":100}]
        unchanged = {s.name for s in Parser("\n".join(core()).split("DS:=",1)[0]).parse() if s.name}
        for settings in cases:
            def old_name(key):
                return key if key in SETTINGS else ("TTSTD" if key == "SK" else "TT"+key)
            old_overrides = {old_name(k):v for k,v in settings.items()}
            new_overrides = {NAMES.get(k,k):v for k,v in settings.items()}
            before = raw_evaluate(reference, data, old_overrides)
            after = raw_evaluate(SOURCE, data, new_overrides)
            for key, value in before.items():
                if key == "TTSTD":
                    semantic = "SK"
                elif key.startswith("TT"):
                    semantic = key[2:]
                else:
                    semantic = key
                # 1.0.6 deliberately changes direction invalidation, automatic
                # work-wave anchors, label spacing and correction phase gates.
                # Keep the independent baseline for standard K, pivots and Q
                # measurements, not the old behavior the user reported wrong.
                if semantic not in unchanged or semantic in {"PAD", "DETAIL"}:
                    continue
                current = NAMES.get(semantic,key)
                np.testing.assert_allclose(value, after[current], err_msg=f"{key}: {settings}", equal_nan=True)

    def test_compatibility_rewrite_preserves_every_analysis_value(self):
        data = bars(160)
        legacy = raw_evaluate("\n".join(core()+drawing()), data)
        patched = evaluate(SOURCE, data)
        for name in legacy:
            np.testing.assert_allclose(patched[name], legacy[name], err_msg=name, equal_nan=True)

    def test_standard_k_matches_independent_sorting_oracle(self):
        data = bars(160)
        e = evaluate(SOURCE, data)
        for t in range(40, len(data["C"])):
            indices = sorted(range(t-40, t), key=lambda i: (-abs(data["C"][i]-data["O"][i]), -i))[:5]
            values = [data["C"][i]-data["L"][i] if data["C"][i]>=data["O"][i]
                      else data["H"][i]-data["C"][i] for i in indices]
            self.assertAlmostEqual(e["SK"][t], sum(values)/5)
        np.testing.assert_array_equal(e["SK"][:40], 0)

    def test_standard_k_does_not_use_current_candle(self):
        data = bars(100)
        a = evaluate(SOURCE, data)
        changed = {k: v.copy() for k, v in data.items()}
        changed["H"][-1] = 100000
        changed["C"][-1] = 99999
        b = evaluate(SOURCE, changed)
        self.assertEqual(a["SK"][-1], b["SK"][-1])

    def test_top_five_selection_is_unique_and_ties_keep_recent_bars(self):
        e = evaluate(SOURCE, bars(90))
        ages = np.array([e[f"KAGE{i}"] for i in range(1,6)])
        for t in range(40, 90):
            self.assertEqual(len(np.unique(ages[:,t])),5)
            self.assertTrue(np.all((ages[:,t]>=1)&(ages[:,t]<=40)))
        flat={k:np.full(90,v) for k,v in (('O',100.),('C',101.),('H',102.),('L',99.))}
        f=evaluate(SOURCE,flat)
        np.testing.assert_array_equal([f[f'KAGE{i}'][-1]for i in range(1,6)],[1,2,3,4,5])

    def test_short_flat_and_zero_price_inputs_are_finite(self):
        for n in (1, 2, 14, 40, 41, 90):
            for price in (0, 3500):
                with self.subTest(n=n, price=price), np.errstate(all="raise"):
                    data = {k: np.full(n, price, dtype=float) for k in ("O", "H", "L", "C")}
                    e = evaluate(SOURCE, data)
                    for name in ("SK", "WD", "PHASE", "RET", "SRET", "TON", "FON", "QREL"):
                        self.assertTrue(np.all(np.isfinite(e[name])), name)
                    self.assertFalse(e["TON"].any())
                    self.assertFalse(e["FON"].any())

    def test_every_analysis_variable_is_prefix_causal(self):
        data = bars(280)
        whole = evaluate(SOURCE, data)
        for t in (43, 119, 213):
            prefix = evaluate(SOURCE, slice_data(data, t))
            for name in prefix:
                if name in ("ISLASTBAR", "DRAWNULL", "DATACOUNT"):
                    continue
                np.testing.assert_allclose(prefix[name], whole[name][:t], err_msg=f"{name} at {t}")

    def test_pivot_ages_alternate_and_reference_real_confirmations(self):
        data = waves()
        e = evaluate(SOURCE, data)
        self.assertTrue(e["BK5"].any())
        for p, n in (("B", 7), ("S", 2)):
            for t in range(len(data["C"])):
                for i in range(6):
                    if not e[f"{p}K{i}"][t]: continue
                    age = int(e[f"{p}A{i}"][t])
                    high = (e[f"{p}TYPE"][t] == 1) == (i % 2 == 0)
                    self.assertTrue(e[f"{p}{'HC' if high else 'LC'}"][t-age])
                    self.assertLess(age+n, t+1)
                    self.assertEqual(e[f"{p}P{i}"][t], data["H" if high else "L"][t-age-n])
                    if i: self.assertGreater(age, e[f"{p}A{i-1}"][t])

    def test_q_clock_is_actual_candle_count_excluding_origin(self):
        data = waves()
        e = evaluate(SOURCE, data)
        self.assertTrue(e["SDQ"].any())
        for p, n in (("S", 2), ("B", 7)):
            for direction in ("U", "D"):
                for t in np.flatnonzero(e[f"{p}{direction}Q"]):
                    age = int(e[f"{p}{direction}N"][t])
                    origin = t-age
                    self.assertGreaterEqual(origin, 0)
                    level = data["H" if direction == "U" else "L"][origin]
                    sign = 1 if direction == "U" else -1
                    self.assertGreater(sign*(data["C"][t]-level), 0)
                    # Confirmed extreme cannot have been exceeded by a close
                    # between its occurrence and this first observed breakout.
                    self.assertFalse(np.any(sign*(data["C"][origin+1:t]-level)>0))

    def test_q_boundaries_follow_book_small_and_large_rules(self):
        data = bars(80)
        cases = [(8, 10, 0), (8, 11, 1), (8, 13, 1), (8, 14, 1), (2, 8, 2),
                 (8, 6, 0), (8, 5, -1), (8, 2, -2),
                 (1000, 382, -1), (1000, 828, 0), (1000, 1208, 0),
                 (1000, 2618, 1), (1000, 2619, 2)]
        for old, new, expected in cases:
            with self.subTest(old=old, new=new):
                e = evaluate(SOURCE, data, {"QOLD": old, "QNEW": new})
                self.assertEqual(e["QREL"][-1], expected)

    def test_gold_example_fall_rebound_has_correct_orientation_and_prices(self):
        e = evaluate(SOURCE, bars(90), {"WD": -1, "FSTART": 3919.71,
                                      "FEND": 3174.45, "PHASE": 2, "WORKVALID": 1})
        expected = {"F0": 3174.45, "F236": 3350.33, "F382": 3459.14,
                    "F500": 3547.08, "F618": 3635.02, "F786": 3760.22, "F100": 3919.71}
        for key, value in expected.items():
            self.assertAlmostEqual(round(e[key][-1], 2), value, places=2)
        self.assertTrue(e["FON"][-1])
        self.assertFalse(e["TON"][-1])

    def test_manual_work_wave_anchors_preserve_the_whole_prior_leg(self):
        e = evaluate(SOURCE, waves(), {"DIR": -1, "KIND": 2, "MANF": 1,
                                      "MFROM": 4801, "MTO": 2199, "MFAGE": 300})
        self.assertTrue(e["FON"][-1])
        self.assertEqual(e["FSTART"][-1], 4801)
        self.assertEqual(e["FEND"][-1], 2199)
        self.assertEqual(e["FAGE"][-1], 300)
        self.assertAlmostEqual(e["F500"][-1], 3500)

    def test_manual_work_wave_rejects_anchors_outside_loaded_history(self):
        e = evaluate(SOURCE, bars(90), {"DIR": -1, "KIND": 2, "MANF": 1,
                                      "MFROM": 4800, "MTO": 3100, "MFAGE": 100})
        self.assertFalse(e["FON"].any())

    def test_upward_main_wave_retracement_is_mirrored(self):
        e = evaluate(SOURCE, bars(90), {"WD": 1, "FSTART": 3174.45,
                                      "FEND": 3919.71, "PHASE": 2, "WORKVALID": 1})
        self.assertAlmostEqual(e["F0"][-1], 3919.71)
        self.assertAlmostEqual(e["F100"][-1], 3174.45)
        self.assertLess(e["F618"][-1], e["F382"][-1])

    def test_retracement_can_exceed_100_percent(self):
        data = bars(90)
        e = evaluate(SOURCE, data, {"WD": 1, "FSTART": 4200, "FEND": 4400,
                                  "WORKVALID": 1})
        self.assertTrue((e["RET"]>100).any())

    def test_fib_and_targets_are_mutually_exclusive_for_all_phases(self):
        for phase in (0, 1, 2, 3):
            e = evaluate(SOURCE, waves(), {"PHASE": phase})
            self.assertFalse(np.any((e["TON"] != 0) & (e["FON"] != 0)))

    def test_targets_only_three_nearest_unhit_with_correct_origins(self):
        data = {k: np.full(100, value, dtype=float) for k, value in
                (("O", 3305), ("C", 3300), ("H", 3310), ("L", 3290))}
        settings = {"DIR": -1, "KIND": 1, "MH1": 200, "MBASE": 3500,
                    "MORG": 4000, "MAGE": 50}
        e = evaluate(SOURCE, data, settings)
        self.assertAlmostEqual(e["T1"][-1], 3500-1.208*200)
        self.assertAlmostEqual(e["T7"][-1], 4000-3.236*200)
        candidates = [(e[f"T{i}"][-1], i) for i in range(1, 9) if e[f"T{i}OK"][-1]]
        nearest = {i for _, i in sorted(candidates, reverse=True)[:3]}
        drawn = {i for i in range(1, 9) if e[f"T{i}OK"][-1] and e[f"T{i}R"][-1]<=3}
        self.assertEqual(nearest, drawn)
        self.assertEqual(len(drawn), 3)
        actual = [e[f"TT{i}"][-1] for i in range(1, 4)]
        expected = [price for price, _ in sorted(candidates, reverse=True)[:3]]
        np.testing.assert_allclose(actual, expected)
        for i in (4, 5, 6): self.assertFalse(e[f"T{i}OK"][-1])

    def test_manual_target_base_origin_zero_is_not_a_missing_value(self):
        data = {k: np.full(80, -20.0) for k in ("O", "H", "L", "C")}
        e = evaluate(SOURCE, data, {"DIR": -1, "KIND": 1, "MH1": 10,
                                  "MBASE": 0, "MORG": 0, "MAGE": 10})
        self.assertEqual(e["TBASE"][-1], 0)
        self.assertEqual(e["TORG"][-1], 0)
        self.assertAlmostEqual(e["T1"][-1], -12.08)

    def test_no_structure_does_not_create_automatic_targets(self):
        c = np.arange(80, dtype=float)+3000
        data = {"O": c-1, "C": c, "H": c+1, "L": c-2}
        e = evaluate(SOURCE, data)
        self.assertFalse(e["TON"].any())

    def test_trend_high_low_sequence_is_not_relabelled_as_abc_correction(self):
        e = evaluate(SOURCE, bars(90), {"WD": 1, "BTYPE": -1, "BK3": 1,
                                      "BK5": 0, "BP3": 100, "BP2": 90,
                                      "BP1": 130, "BP0": 115})
        self.assertFalse(e["ABC"][-1])
        self.assertFalse(e["ABCS"][-1])
        self.assertFalse(e["ACAND"][-1])

    def test_abc_geometry_candidate_is_symmetric_and_does_not_claim_confirmation(self):
        data = bars(90)
        args = {"WD": 1, "PHASE":2, "BTYPE": -1, "BK3": 1, "BK5": 0,
                "BP3": 400, "BP2": 350, "BP1": 380, "BP0": 330}
        e = evaluate(SOURCE, data, args)
        self.assertTrue(e["ABC"][-1])
        self.assertTrue(e["ACAND"][-1])
        mirror = args | {"WD": -1, "BTYPE": 1}
        mirror.update({f"BP{i}": 1000-args[f"BP{i}"] for i in range(4)})
        f = evaluate(SOURCE, data, mirror)
        self.assertTrue(f["ABC"][-1])
        self.assertTrue(f["ACAND"][-1])

    def test_price_scale_invariance(self):
        data = bars(180)
        a = evaluate(SOURCE, data)
        b = evaluate(SOURCE, {k: v*10 for k, v in data.items()})
        for key in ("SK", "FSTART", "FEND", "H1"):
            np.testing.assert_allclose(b[key], a[key]*10, err_msg=key)
        for key in ("WD", "LEG", "PHASE", "RET", "SRET", "SUQ", "SDQ"):
            np.testing.assert_allclose(b[key], a[key], atol=1e-9, err_msg=key)

    def test_key_reversal_needs_previously_known_position_and_standard_size(self):
        e = evaluate(SOURCE, bars(140))
        for t in np.flatnonzero(e["BULLKEY"]):
            self.assertTrue(e["PREPOS"][t])
            self.assertTrue(e["BPQUAL"][t])
            self.assertTrue(e["BULLPAT"][t])
            self.assertEqual(e["WD"][t], 1)
        for t in np.flatnonzero(e["BEARKEY"]):
            self.assertTrue(e["PREPOS"][t])
            self.assertTrue(e["SPQUAL"][t])
            self.assertTrue(e["BEARPAT"][t])
            self.assertEqual(e["WD"][t], -1)

    def test_engulfing_positive_signal_and_position_size_filters(self):
        data = bars(90)
        for k, a, b in (("O", 3440, 3400), ("C", 3410, 3450),
                        ("H", 3450, 3460), ("L", 3400, 3380)):
            data[k][-2:] = [a, b]
        args = {"WD": 1, "NEARPOS": 1, "SKOK": 1, "SK": 30}
        e = evaluate(SOURCE, data, args)
        self.assertTrue(e["BULLENG"][-1])
        self.assertTrue(e["BULLKEY"][-1])
        for change in ({"NEARPOS": 0}, {"SK": 1000}, {"SKOK": 0}):
            e = evaluate(SOURCE, data, args | change)
            self.assertFalse(e["BULLKEY"][-1])
        mirror = {"O": 7000-data["O"], "C": 7000-data["C"],
                  "H": 7000-data["L"], "L": 7000-data["H"]}
        e = evaluate(SOURCE, mirror, args | {"WD": -1})
        self.assertTrue(e["BEARENG"][-1])
        self.assertTrue(e["BEARKEY"][-1])


if __name__ == "__main__":
    unittest.main()
