"""Emit a conservative THS subset with explicit number/condition conversions.

Every stored variable is numerical, every conditional argument is a condition,
and internal identifiers receive a TT prefix. This also avoids futures order
names such as SK, which the user's actual compiler rejected.
"""
import re

from check_formula import (Parser, FormulaError, NUMBER, CONDITION, STRING,
                           SIGNATURES, PREDEFINED_TYPES, PRIORITY,
                           constant_number, walk)

# Existing user settings keep their names, so upgrading does not obscure the
# documented parameter values. All other ASCII variables are internal.
SETTINGS = {"PN", "BN", "DIR", "KIND", "MANF", "MFROM", "MTO", "MFAGE",
            "MH1", "MBASE", "MORG", "MAGE", "STRONG", "SPAN",
            "SHOWQ", "SHOWK", "SHOWF", "SHOWT", "SHOWC"}


def variable_names(source):
    result = {}
    for st in Parser(source).parse():
        if st.name and st.name.isascii() and st.name not in SETTINGS:
            if st.name == "SK":
                result[st.name] = "TTSTD"
            elif re.fullmatch(r"E[0-9]{2}", st.name):
                # Forty repeatedly referenced samples dominate source length.
                # Short names keep both exported encodings within our budget.
                result[st.name] = "TT"+str(int(st.name[1:]))
            else:
                result[st.name] = "TT"+st.name
    return result


def render(node, parent=0):
    kind = node[0]
    if kind == "number":
        # Scientific notation is deliberately excluded from this profile.
        v = node[1]
        literal = str(int(abs(v))) if v == int(v) else format(abs(v), ".12f").rstrip("0").rstrip(".")
        return "(0-"+literal+")" if v < 0 else literal
    if kind == "string":
        return "'"+node[1]+"'"
    if kind == "identifier":
        return node[1]
    if kind == "call":
        return node[1]+"("+",".join(render(arg) for arg in node[2])+")"
    if kind == "unary":
        # The user's compiler stopped at IF(...,1,-1). Never emit a unary
        # sign: keep binary subtraction explicitly grouped even in arguments.
        if node[1] == "+":
            return render(node[2], parent)
        return "(0-"+render(node[2], PRIORITY["-"]+1)+")"
    op = node[1]
    priority = PRIORITY[op]
    # A right child of equal priority needs parentheses to retain the AST,
    # including a/(b*c), a-(b-c), and nested comparisons.
    join = " "+op+" " if op in ("AND", "OR") else op
    s = render(node[2], priority)+join+render(node[3], priority+1)
    return "("+s+")" if priority < parent else s


def normalize(source):
    names = variable_names(source)
    # All assignments below are emitted numerically, including flags.
    types = dict(PREDEFINED_TYPES)
    lines = []
    flags = set()
    helpers = {}
    occupied = set(names.values()) | set(SETTINGS)

    def lift(value):
        """Name a numerical series before its use, preserving REF semantics."""
        expression = render(value)
        if expression not in helpers:
            name = f"TTG{len(helpers)+1:04}"
            if name in occupied:
                raise FormulaError(f"helper name collision: {name}")
            occupied.add(name)
            helpers[expression] = name
            lines.append(name+":="+expression+";")
        return ("identifier", helpers[expression])

    def has_if(value):
        return any(n[0:2] == ("call", "IF") for n in walk(value))

    def is_flag(node):
        if node[0] == "identifier":
            return node[1] in flags or PREDEFINED_TYPES.get(node[1]) == CONDITION
        if node[0] == "binary":
            return node[1] not in ("+", "-", "*", "/")
        if node[0] != "call":
            return False
        fn, args = node[1], node[2]
        if SIGNATURES[fn][1] == CONDITION:
            return True
        if fn in ("REF", "REFX1"):
            return is_flag(args[0])
        if fn == "IF":
            def branch_flag(arg):
                return constant_number(arg) in (0, 1) or is_flag(arg)
            return branch_flag(args[1]) and branch_flag(args[2])
        return False

    def identifier(name):
        return ("identifier", names.get(name, name))

    def one_if_zero(cond):
        return ("call", "IF", [cond, ("number", 1.0), ("number", 0.0)])

    def flag(node):
        # Stored logical series are already exactly 0/1. Arithmetic works on
        # those numbers, never on an unconverted condition value.
        if node[0] == "identifier" and node[1] in flags:
            return identifier(node[1])
        if node[0] == "binary" and node[1] == "AND":
            return ("binary", "*", flag(node[2]), flag(node[3]))
        if node[0] == "binary" and node[1] == "OR":
            return ("call", "MAX", [flag(node[2]), flag(node[3])])
        if node[0] == "call" and SIGNATURES[node[1]][1] == NUMBER and is_flag(node):
            return num(node)
        return one_if_zero(cond(node))

    def num(node):
        kind = node[0]
        if kind == "number":
            return node
        if kind == "identifier":
            out = identifier(node[1])
            return one_if_zero(out) if types[node[1]] == CONDITION else out
        if kind == "unary":
            return ("unary", node[1], num(node[2]))
        if kind == "binary":
            if node[1] in ("+", "-", "*", "/"):
                return ("binary", node[1], num(node[2]), num(node[3]))
            return flag(node)
        if kind == "call":
            if SIGNATURES[node[1]][1] == CONDITION:
                return one_if_zero(call(node))
            if node[1] == "IF":
                a, b = constant_number(node[2][1]), constant_number(node[2][2])
                if (a, b) == (1, 0):
                    return flag(node[2][0])
                if (a, b) == (0, 1):
                    return ("binary", "-", ("number", 1.0), flag(node[2][0]))
                if (a, b) == (1, -1):
                    return ("binary", "-", ("binary", "*", ("number", 2.0), flag(node[2][0])), ("number", 1.0))
                if (a, b) == (-1, 1):
                    return ("binary", "-", ("number", 1.0), ("binary", "*", ("number", 2.0), flag(node[2][0])))
            return call(node)
        raise FormulaError(f"cannot convert {kind} to number")

    def cond(node):
        kind = node[0]
        if kind == "identifier" and types[node[1]] == CONDITION:
            return identifier(node[1])
        if kind == "binary":
            if node[1] in ("AND", "OR"):
                return ("binary", ">", lift(flag(node)), ("number", 0.0))
            if node[1] in ("=", "<>", ">", "<", ">=", "<="):
                left, right = num(node[2]), num(node[3])
                return ("binary", node[1], left, right)
        if kind == "call" and SIGNATURES[node[1]][1] == CONDITION:
            return call(node)
        value = num(node)
        if has_if(value): value = lift(value)
        return ("binary", "<>", value, ("number", 0.0))

    def call(node):
        fn, args = node[1], node[2]
        expected, _ = SIGNATURES[fn]
        if len(expected) != len(args):
            raise FormulaError(f"cannot normalize invalid call {fn}")
        output = []
        for arg, kind in zip(args, expected):
            if kind == NUMBER:
                output.append(num(arg))
            elif kind == CONDITION:
                output.append(cond(arg))
            elif kind == STRING and arg[0] == "string":
                output.append(arg)
            else:
                raise FormulaError(f"unsupported argument in {fn}")
        if fn == "IF":
            # Keep negative branch constants out of the client's IF argument
            # grammar. Signed two-way choices above use 2*flag-1 instead.
            for i in (1, 2):
                value = constant_number(output[i])
                if value is not None and value < 0:
                    output[i] = lift(output[i])
        return ("call", fn, output)

    # Each generated statement occupies one physical line. Keep the comments
    # and END_ANALYSIS marker so the drawing boundary remains auditable.
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith(("{", "//")):
            lines.append(line)
            continue
        for st in Parser(line).parse():
            node = num(st.expression) if st.name else call(st.expression)
            head = names.get(st.name, st.name)+(":" if st.output else ":=") if st.name else ""
            tail = ","+",".join(st.styles) if st.styles else ""
            lines.append(head+render(node)+tail+";")
            if st.name:
                types[st.name] = NUMBER
                if is_flag(st.expression):
                    flags.add(st.name)
    return "\n".join(lines)+"\n"
