"""Local parser/checker and numerical interpreter; NOT a native THS compiler.

The explicit, small function profile makes accidentally importing a different
vendor's function or arity an error. NumPy is needed only for numerical tests.
"""
from dataclasses import dataclass
from pathlib import Path
import re

ARITY = {
    "ABS": 1, "MAX": 2, "MIN": 2, "IF": 3, "REF": 2,
    "HHV": 2, "LLV": 2, "MA": 2, "SUM": 2, "COUNT": 2,
    "BARSLAST": 1, "FILTER": 2, "BACKSET": 2, "REFX1": 2,
    "DRAWLINE": 5, "DRAWTEXT": 3, "DRAWNUMBER1": 4,
}
PREDEFINED = {"O", "H", "L", "C", "OPEN", "HIGH", "LOW", "CLOSE",
              "BARPOS", "ISLASTBAR", "DRAWNULL"}
RESERVED = PREDEFINED | {"RANGE", "CONST", "NOT", "INTPART", "MOD", "ROUND",
                         "DATE", "TIME", "YEAR", "MONTH", "DAY", "WEEKDAY",
                         "VOL", "VOLUME", "AMOUNT", "BARSTATUS",
                         # Futures order instructions share the identifier
                         # namespace in the user's client (COND_TRADE:SK).
                         "BK", "SK", "BP", "SP", "BPK", "SPK", "CLOSEOUT"}
NUMBER, CONDITION, STRING, DRAWING = "number", "condition", "string", "drawing"
SIGNATURES = {
    "ABS": ((NUMBER,), NUMBER),
    "MAX": ((NUMBER, NUMBER), NUMBER), "MIN": ((NUMBER, NUMBER), NUMBER),
    "IF": ((CONDITION, NUMBER, NUMBER), NUMBER),
    "REF": ((NUMBER, NUMBER), NUMBER), "REFX1": ((NUMBER, NUMBER), NUMBER),
    "HHV": ((NUMBER, NUMBER), NUMBER), "LLV": ((NUMBER, NUMBER), NUMBER),
    "MA": ((NUMBER, NUMBER), NUMBER), "SUM": ((NUMBER, NUMBER), NUMBER),
    "COUNT": ((CONDITION, NUMBER), NUMBER), "BARSLAST": ((CONDITION,), NUMBER),
    "FILTER": ((CONDITION, NUMBER), CONDITION),
    "BACKSET": ((CONDITION, NUMBER), CONDITION),
    "DRAWLINE": ((CONDITION, NUMBER, CONDITION, NUMBER, NUMBER), NUMBER),
    "DRAWTEXT": ((CONDITION, NUMBER, STRING), DRAWING),
    "DRAWNUMBER1": ((CONDITION, NUMBER, NUMBER, NUMBER), DRAWING),
}
PREDEFINED_TYPES = {name: CONDITION if name == "ISLASTBAR" else NUMBER
                    for name in PREDEFINED}
FUTURE = {"BACKSET", "REFX1", "DRAWLINE"}
STYLES = {"NODRAW", "LINETHICK1", "LINETHICK2", "LINETHICK3"}
TOKEN = re.compile(r"\s+|\{[^}]*\}|//[^\n]*|:=|<>|>=|<=|"
                   r"[0-9]+(?:\.[0-9]+)?|'[^'\r\n]*'|"
                   r"[A-Za-z_\u4e00-\u9fff][A-Za-z_0-9\u4e00-\u9fff]*|"
                   r"[+\-*/=><(),:;]", re.S)
PRIORITY = {"OR": 1, "AND": 2, "=": 3, "<>": 3, ">": 3,
            "<": 3, ">=": 3, "<=": 3, "+": 4, "-": 4, "*": 5, "/": 5}


class FormulaError(ValueError):
    pass


@dataclass
class Statement:
    name: str | None
    expression: tuple
    output: bool
    styles: tuple[str, ...]
    line: int


class Parser:
    def __init__(self, source):
        self.source = source
        self.tokens = []
        offset = 0
        for m in TOKEN.finditer(source):
            if m.start() != offset:
                raise FormulaError(f"unexpected text at line {source.count(chr(10), 0, offset)+1}: {source[offset:m.start()]!r}")
            t = m.group()
            if not (t.isspace() or t.startswith("{") or t.startswith("//")):
                self.tokens.append((t if t.startswith("'") else t.upper(), source.count("\n", 0, m.start())+1))
            offset = m.end()
        if offset != len(source):
            raise FormulaError(f"unclosed comment or unsupported text: {source[offset:offset+50]!r}")
        self.tokens.append(("<EOF>", source.count("\n")+1))
        self.i = 0

    def peek(self, ahead=0):
        return self.tokens[min(self.i+ahead, len(self.tokens)-1)][0]

    def take(self, expected=None):
        t, line = self.tokens[self.i]
        if expected and t != expected:
            raise FormulaError(f"line {line}: expected {expected}, found {t}")
        self.i += 1
        return t

    def expression(self, priority=0):
        t = self.take()
        if t in ("-", "+"):
            value = ("unary", t, self.expression(6))
        elif t == "(":
            value = self.expression()
            self.take(")")
        elif t.startswith("'"):
            value = ("string", t[1:-1])
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t):
            value = ("number", float(t))
        elif re.fullmatch(r"[A-Z_\u4e00-\u9fff][A-Z_0-9\u4e00-\u9fff]*", t):
            if self.peek() == "(":
                self.take("(")
                args = []
                if self.peek() != ")":
                    while True:
                        args.append(self.expression())
                        if self.peek() != ",":
                            break
                        self.take(",")
                self.take(")")
                value = ("call", t, args)
            else:
                value = ("identifier", t)
        else:
            raise FormulaError(f"invalid expression token {t!r}")
        while self.peek() in PRIORITY and PRIORITY[self.peek()] > priority:
            op = self.take()
            value = ("binary", op, value, self.expression(PRIORITY[op]))
        return value

    def parse(self):
        statements = []
        while self.peek() != "<EOF>":
            line = self.tokens[self.i][1]
            name, output = None, False
            if self.peek(1) in (":=", ":"):
                name = self.take()
                output = self.take() == ":"
            expression = self.expression()
            styles = []
            while self.peek() == ",":
                self.take(",")
                styles.append(self.take())
            self.take(";")
            statements.append(Statement(name, expression, output, tuple(styles), line))
        return statements


def walk(node):
    yield node
    if node[0] == "call":
        for arg in node[2]:
            yield from walk(arg)
    elif node[0] == "binary":
        yield from walk(node[2])
        yield from walk(node[3])
    elif node[0] == "unary":
        yield from walk(node[2])


def infer_type(node, types, line):
    """Use a deliberately strict condition/number split, without implicit casts.

    This is a local compatibility profile, not the THS compiler. Explicit IF
    casts and comparisons keep the generated code within this smaller subset.
    """
    kind = node[0]
    if kind == "number":
        return NUMBER
    if kind == "string":
        return STRING
    if kind == "identifier":
        return types[node[1]]

    def require(actual, expected, where):
        if actual != expected:
            raise FormulaError(f"line {line}: {where} requires {expected}, got {actual}; use an explicit IF/condition conversion")

    if kind == "unary":
        require(infer_type(node[2], types, line), NUMBER, node[1])
        return NUMBER
    if kind == "binary":
        op = node[1]
        expected = CONDITION if op in ("AND", "OR") else NUMBER
        require(infer_type(node[2], types, line), expected, op)
        require(infer_type(node[3], types, line), expected, op)
        return NUMBER if op in ("+", "-", "*", "/") else CONDITION
    fn, args = node[1], node[2]
    signature, output = SIGNATURES[fn]
    for i, (arg, expected) in enumerate(zip(args, signature), 1):
        require(infer_type(arg, types, line), expected, f"{fn} argument {i}")
    return output


def constant_number(node):
    """Evaluate numeric constant windows, including canonical (0-1).

    This does not evaluate identifiers or functions, nor depend on market data.
    """
    if node[0] == "number":
        return node[1]
    if node[0] == "unary":
        value = constant_number(node[2])
        return None if value is None else (-value if node[1] == "-" else value)
    if node[0] != "binary" or node[1] not in ("+", "-", "*", "/"):
        return None
    a, b = constant_number(node[2]), constant_number(node[3])
    if a is None or b is None:
        return None
    if node[1] == "+": return a+b
    if node[1] == "-": return a-b
    if node[1] == "*": return a*b
    return None if b == 0 else a/b


def check(source, enforce_boundary=True):
    statements = Parser(source).parse()
    names = set(PREDEFINED)
    calls = {}
    future_calls = []
    types = dict(PREDEFINED_TYPES)
    end = source.find("{ END_ANALYSIS")
    boundary = source.count("\n", 0, end)+1 if end >= 0 else 0
    for st in statements:
        if st.name:
            if st.name in names or st.name in ARITY or st.name in RESERVED:
                raise FormulaError(f"line {st.line}: duplicate or reserved name {st.name}")
            if len(st.name) > 16:
                raise FormulaError(f"line {st.line}: identifier longer than 16 characters: {st.name}")
        for node in walk(st.expression):
            if node[0] == "identifier" and node[1] not in names:
                raise FormulaError(f"line {st.line}: undefined or forward variable {node[1]}")
            if node[0] == "call":
                fn, args = node[1], node[2]
                if fn not in ARITY:
                    raise FormulaError(f"line {st.line}: unreviewed function {fn}")
                if len(args) != ARITY[fn]:
                    raise FormulaError(f"line {st.line}: {fn} expects {ARITY[fn]} args, got {len(args)}")
                calls[fn] = calls.get(fn, 0)+1
                if fn in FUTURE:
                    future_calls.append((fn, st.line))
                    if enforce_boundary and (not boundary or st.line <= boundary):
                        raise FormulaError(f"line {st.line}: {fn} leaks into analysis")
                if fn == "REF" and args[1][0] == "unary" and args[1][1] == "-":
                    raise FormulaError(f"line {st.line}: negative REF")
                if fn in ("REF", "REFX1", "HHV", "LLV", "MA", "SUM", "COUNT", "BACKSET", "FILTER"):
                    window = constant_number(args[1])
                    if fn == "REF" and window is not None and window < 0:
                        raise FormulaError(f"line {st.line}: negative REF")
                    if window is not None and (window != int(window) or window < 0):
                        raise FormulaError(f"line {st.line}: invalid literal lookback")
                if fn == "DRAWTEXT" and args[2][0] != "string":
                    raise FormulaError(f"line {st.line}: text must be a literal in this compatibility profile")
            if node[0] == "unary":
                raise FormulaError(f"line {st.line}: unary sign is excluded by the client compatibility profile; use (0-X)")
        for style in st.styles:
            if style not in STYLES and not re.fullmatch(r"COLOR(?:[0-9A-F]{6}|RED|GREEN|BLUE|CYAN|MAGENTA|YELLOW|WHITE|BLACK)", style):
                raise FormulaError(f"line {st.line}: unreviewed style {style}")
        result_type = infer_type(st.expression, types, st.line)
        if st.output and result_type != NUMBER:
            raise FormulaError(f"line {st.line}: plotted output requires number, got {result_type}")
        if st.name:
            if result_type in (STRING, DRAWING):
                raise FormulaError(f"line {st.line}: cannot assign {result_type} to {st.name}")
            names.add(st.name)
            types[st.name] = result_type
    return {"statements": len(statements), "variables": len(names-PREDEFINED),
            "outputs": sum(st.output for st in statements), "functions": calls,
            "future_calls": future_calls, "type_check": "passed", "unary_signs": "none",
            "native_compile": "not_run"}


def evaluate(source, data, overrides=None):
    """Evaluate the causal analysis section against synthetic OHLC bars.

    Insufficient REF history uses the first observation. Analysis guards still
    suppress incomplete structures and the initial 40 standard-K samples.
    """
    import numpy as np
    n = len(data["C"])
    if n == 0:
        raise ValueError("empty OHLC")
    env = {key: np.asarray(data[key], dtype=float) for key in ("O", "H", "L", "C")}
    for long, short in (("OPEN", "O"), ("HIGH", "H"), ("LOW", "L"), ("CLOSE", "C")):
        env[long] = env[short]
    env["BARPOS"] = np.arange(1, n+1, dtype=float)
    env["ISLASTBAR"] = np.arange(n) == n-1
    env["DRAWNULL"] = np.full(n, np.nan)
    index = np.arange(n)
    overrides = {k.upper(): v for k, v in (overrides or {}).items()}

    def vec(x):
        return np.full(n, x, dtype=float) if np.isscalar(x) else np.asarray(x)

    def integer(x, minimum=0):
        x = vec(x)
        if not np.all(np.isfinite(x)) or not np.all(x == np.floor(x)) or np.any(x < minimum):
            raise FormulaError("nonintegral, nonfinite or negative lookback during evaluation")
        return x.astype(int)

    def ev(node):
        kind = node[0]
        if kind == "number":
            return vec(node[1])
        if kind == "identifier":
            return env[node[1]]
        if kind == "string":
            return node[1]
        if kind == "unary":
            a = ev(node[2])
            return -a if node[1] == "-" else a
        if kind == "binary":
            a, b, op = ev(node[2]), ev(node[3]), node[1]
            if op == "+": return a.astype(float)+b.astype(float)
            if op == "-": return a.astype(float)-b.astype(float)
            if op == "*": return a*b
            if op == "/": return a/b
            if op == "AND": return (a != 0) & (b != 0)
            if op == "OR": return (a != 0) | (b != 0)
            if op == "=": return a == b
            if op == "<>": return a != b
            if op == ">": return a > b
            if op == "<": return a < b
            if op == ">=": return a >= b
            if op == "<=": return a <= b
            raise FormulaError(f"unimplemented operator {op}")
        fn, args = node[1], [ev(arg) for arg in node[2]]
        if fn == "ABS": return np.abs(args[0])
        if fn == "MAX": return np.maximum(*args)
        if fn == "MIN": return np.minimum(*args)
        if fn == "IF": return np.where(args[0] != 0, args[1], args[2])
        if fn == "REF": return args[0][np.maximum(index-integer(args[1]), 0)]
        if fn == "BARSLAST":
            last = -1
            result = np.empty(n)
            for i, flag in enumerate(args[0]):
                if flag: last = i
                result[i] = i-last
            return result
        if fn in ("SUM", "COUNT", "HHV", "LLV", "MA"):
            a, windows = args[0], integer(args[1])
            if fn == "COUNT": a = a != 0
            result = np.empty(n)
            if fn in ("SUM", "COUNT", "MA"):
                cumulative = np.r_[0.0, np.cumsum(a)]
                begin = np.where(windows == 0, 0, np.maximum(index-windows+1, 0))
                result = cumulative[index+1]-cumulative[begin]
                if fn == "MA": result /= index-begin+1
            else:
                for i, w in enumerate(windows):
                    sample = a[0 if w == 0 else max(0, i-w+1):i+1]
                    result[i] = np.max(sample) if fn == "HHV" else np.min(sample)
            return result
        raise FormulaError(f"analysis interpreter refuses {fn}")

    source = source.split("{ END_ANALYSIS", 1)[0]
    for st in Parser(source).parse():
        if not st.name:
            raise FormulaError("drawing statement found in analysis")
        env[st.name] = vec(overrides[st.name]) if st.name in overrides else ev(st.expression)
    return env


def main():
    import json
    root = Path(__file__).resolve().parent
    reports = {}
    for path in sorted((root / "formulas").glob("*.txt")):
        encoding = "utf-8-sig" if "UTF8" in path.name else "gb18030"
        source = path.read_bytes().decode(encoding).replace("\r\n", "\n")
        reports[path.name] = check(source, enforce_boundary="兼容检查" not in path.name)
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
