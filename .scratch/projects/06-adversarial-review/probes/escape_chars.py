"""B1 -- verify structured-language escaping across the guarded character set."""

# ruff: noqa: E402, I001

import ast
import json
import sys
import tomllib

sys.path.insert(0, "/home/andrew/Documents/Projects/templateer_v2/src")
import yaml
from templateer.escaping import escape_string

LOADERS = {
    "toml": lambda t: tomllib.loads('k = "%s"' % t)["k"],
    "json": lambda t: json.loads('{"k": "%s"}' % t)["k"],
    "yaml": lambda t: yaml.safe_load('k: "%s"' % t)["k"],
    "python": lambda t: ast.literal_eval('"%s"' % t),
}

print("char-by-char round-trip of escape_string() output, per language")
print("%-10s %-9s %-9s %-9s %-9s" % ("codepoint", *LOADERS))
bad_total = 0
rejected = 0
for cp in list(range(0, 0x120)) + [0x2028, 0x2029, 0xD800, 0xDFFF, 0xFEFF, 0x1F600]:
    s = "a" + chr(cp) + "b"
    # B1 remediation: a lone surrogate now raises EscapeError up front, so
    # the call must sit inside the guard.  Rejection is the fix, not a break.
    try:
        e = escape_string(s)
    except Exception as ex:
        rejected += 1
        print("U+%04X    rejected up front: %s" % (cp, type(ex).__name__))
        continue
    row = []
    broken = False
    for lang, load in LOADERS.items():
        try:
            row.append("ok" if load(e) == s else "MISMATCH")
        except Exception as ex:
            row.append(type(ex).__name__[:9])
        if row[-1] != "ok":
            broken = True
    if broken:
        bad_total += 1
        print("U+%04X    %-9s %-9s %-9s %-9s   escaped=%r" % (cp, *row, e))
print()
print("codepoints that break at least one target language:", bad_total)
print("codepoints rejected up front by EscapeError:", rejected)
