import sys, ast, json, tomllib, re
sys.path.insert(0, "/home/andrew/Documents/Projects/templateer_v2/src")
import yaml
from templateer.escaping import escape_string

# Candidate fix: escape every codepoint that any target language treats
# specially, as \uXXXX -- the escape form all four grammars share.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")


def escape_string_fixed(value: str) -> str:
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
    for cp in list(range(0, 0x120)) + [0x2028, 0x2029, 0xD800, 0xDFFF, 0xFEFF, 0x1F600]:
        s = "a" + chr(cp) + "b"
        e = fn(s)
        for lang, load in LOADERS.items():
            try:
                if load(e) != s:
                    broken.append((lang, "U+%04X" % cp, "mismatch"))
            except Exception as ex:
                broken.append((lang, "U+%04X" % cp, type(ex).__name__))
    print("%-10s broken codepoint/language pairs: %d" % (name, len(broken)))
    if broken:
        langs = sorted({b[0] for b in broken})
        print("           affected languages:", langs)
