"""Emit a conservative THS subset with explicit number/condition conversions.

Every stored variable is numerical, every conditional argument is a condition,
and internal identifiers receive a TT prefix. This also avoids futures order
names such as SK, which the user's actual compiler rejected.
"""
import re

from check_formula import (Parser, FormulaError, NUMBER, CONDITION, STRING,
                           SIGNATURES, PREDEFINED_TYPES, PRIORITY)

# Existing user settings keep their names, so upgrading does not obscure the
# documented parameter values. All other ASCII variables are internal.
SETTINGS = {"PN", "BN", "DIR", "KIND", "MANF", "MFROM", "MTO", "MFAGE",
            "MH1", "MBASE", "MORG", "MAGE", "STRONG", "SPAN",
            "SHOWQ", "SHOWK", "SHOWF", "SHOWT", "SHOWC"}


def variable_names(source):
    result = {}
    for st in Parser(source).parse():
        if st.name and st.name.isascii() and st.name not in SETTINGS:
            result[st.name] = "TTSTD" if st.name == "SK" else "TT"+st.name
    return result


def render(node, parent=0):
    kind = node[0]
    if kind == "number":
        # Scientific notation is deliberately excluded from this profile.
        v = node[1]
        return str(int(v)) if v == int(v) else format(v, ".12f").rstrip("0").rstrip(".")
    if kind == "string":
        return "'"+node[1]+"'"
    if kind == "identifier":
        return node[1]
    if kind == "call":
        return node[1]+"("+",".join(render(arg) for arg in node[2])+")"
    if kind == "unary":
        s = node[1]+render(node[2], 6)
        return "("+s+")" if parent>6 else s
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

    def identifier(name):
        return ("identifier", names.get(name, name))

    def one_if_zero(cond):
        return ("call", "IF", [cond, ("number", 1.0), ("number", 0.0)])

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
            return one_if_zero(cond(node))
        if kind == "call":
            if SIGNATURES[node[1]][1] == CONDITION:
                return one_if_zero(call(node))
            return call(node)
        raise FormulaError(f"cannot convert {kind} to number")

    def cond(node):
        kind = node[0]
        if kind == "identifier" and types[node[1]] == CONDITION:
            return identifier(node[1])
        if kind == "binary":
            if node[1] in ("AND", "OR"):
                return ("binary", node[1], cond(node[2]), cond(node[3]))
            if node[1] in ("=", "<>", ">", "<", ">=", "<="):
                return ("binary", node[1], num(node[2]), num(node[3]))
        if kind == "call" and SIGNATURES[node[1]][1] == CONDITION:
            return call(node)
        return ("binary", "<>", num(node), ("number", 0.0))

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
    return "\n".join(lines)+"\n"
