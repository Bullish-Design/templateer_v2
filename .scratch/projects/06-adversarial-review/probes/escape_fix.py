"""B1 -- compare the current escaper with the selected round-2 fix."""

# ruff: noqa: E402, I001

import ast
import json
import re
import sys
import tomllib

sys.path.insert(0, "/home/andrew/Documents/Projects/templateer_v2/src")
import yaml
from templateer.escaping import EscapeError, escape_string

# Selected fix: reject lone surrogates. Escape the codepoints that break at
# least one target grammar. U+2028 and U+2029 need escaping because YAML folds
# adjacent spaces around raw separators. U+FEFF round-trips unchanged.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufffe\uffff]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")


def escape_string_fixed(value: str) -> str:
    if _SURROGATE.search(value):
        raise EscapeError("lone surrogate")
    out = json.dumps(value, ensure_ascii=False)[1:-1]
    return _UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()), out)


LOADERS = {
    "toml": lambda t: tomllib.loads('k = "%s"' % t)["k"],
    "json": lambda t: json.loads('{"k": "%s"}' % t)["k"],
    "yaml": lambda t: yaml.safe_load('k: "%s"' % t)["k"],
    "python": lambda t: ast.literal_eval('"%s"' % t),
}

for name, fn in (("current", escape_string), ("candidate", escape_string_fixed)):
    broken = []
    rejected = []
    for cp in list(range(0, 0x120)) + [0x2028, 0x2029, 0xD800, 0xDFFF, 0xFEFF, 0x1F600]:
        s = "a" + chr(cp) + "b"
        try:
            e = fn(s)
        except EscapeError:
            rejected.append("U+%04X" % cp)
            continue
        for lang, load in LOADERS.items():
            try:
                if load(e) != s:
                    broken.append((lang, "U+%04X" % cp, "mismatch"))
            except Exception as ex:
                broken.append((lang, "U+%04X" % cp, type(ex).__name__))
    print("%-10s broken codepoint/language pairs: %d" % (name, len(broken)))
    print("           rejected codepoints:", rejected)
    if broken:
        langs = sorted({b[0] for b in broken})
        print("           affected languages:", langs)
