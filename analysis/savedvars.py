"""Parse WoW's literal SavedVariables format without executing Lua. Stdlib only."""
import re

TOKEN = re.compile(r'''\s+|--\[\[[\s\S]*?\]\]|--[^\n]*|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[{}\[\]=,;]''')


def string(token):
    value, i = bytearray(), 1
    escapes = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11}
    while i < len(token) - 1:
        c = token[i]
        i += 1
        if c != "\\":
            value.extend(c.encode("utf-8"))
            continue
        c = token[i]
        i += 1
        if c.isdigit():
            digits = c
            while i < len(token) - 1 and len(digits) < 3 and token[i].isdigit():
                digits += token[i]
                i += 1
            value.append(int(digits))
        elif c in escapes:
            value.append(escapes[c])
        elif c in ("\\", '"', "'", "\n"):
            value.extend(c.encode("utf-8"))
        else:
            raise ValueError("Unsupported Lua string escape")
    return value.decode("utf-8")


def loads(source):
    source = source.lstrip("\ufeff")
    tokens, offset = [], 0
    for match in TOKEN.finditer(source):
        if match.start() != offset:
            raise ValueError(f"Nonliteral Lua at offset {offset}")
        offset = match.end()
        token = match[0]
        if not token.isspace() and not token.startswith("--"):
            tokens.append(token)
    if offset != len(source):
        raise ValueError("Trailing nonliteral Lua")
    index = 0

    def take(expected=None):
        nonlocal index
        if index >= len(tokens):
            raise ValueError("Truncated SavedVariables")
        token = tokens[index]
        index += 1
        if expected is not None and token != expected:
            raise ValueError(f"Expected {expected}, got {token[:50]}")
        return token

    def value(depth=0):
        if depth > 100:
            raise ValueError("SavedVariables nesting limit exceeded")
        token = take()
        if token == "{":
            result, implicit = {}, 1
            while index < len(tokens) and tokens[index] != "}":
                if tokens[index] == "[":
                    take("[")
                    key = value(depth + 1)
                    if not isinstance(key, (str, int, float)) or isinstance(key, bool):
                        raise ValueError("Unsupported table key")
                    take("]")
                    take("=")
                elif index + 1 < len(tokens) and tokens[index + 1] == "=":
                    key = take()
                    take("=")
                else:
                    key, implicit = implicit, implicit + 1
                if key in result:
                    raise ValueError("Duplicate table key")
                result[key] = value(depth + 1)
                if index < len(tokens) and tokens[index] in (",", ";"):
                    take()
                elif index >= len(tokens) or tokens[index] != "}":
                    raise ValueError("Missing table separator")
            take("}")
            return result
        if token.startswith(('"', "'")):
            return string(token)
        if token in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[token]
        if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", token):
            return float(token) if any(c in token for c in ".eE") else int(token)
        raise ValueError(f"Nonliteral value: {token[:50]}")

    take("ForeverStateDB")
    take("=")
    result = value()
    if index < len(tokens) and tokens[index] == ";":
        take()
    if index != len(tokens) or not isinstance(result, dict):
        raise ValueError("Unexpected content after ForeverStateDB")
    return result


def array(table):
    if not isinstance(table, dict) or set(table) != set(range(1, len(table) + 1)):
        raise ValueError("Expected dense Lua array")
    return [table[i] for i in range(1, len(table) + 1)]
