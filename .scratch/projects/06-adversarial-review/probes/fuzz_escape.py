import sys, ast, json, tomllib, random
sys.path.insert(0, "/home/andrew/Documents/Projects/templateer_v2/src")
import yaml
from templateer.escaping import escape_string

random.seed(7)
alphabet = list('ab"\\') + [
    "\n", "\r", "\t", "\x00", "\x7f", "\x1b", "\x85", " ",
    "'", "`", "$", "{", "}", "[", "]", "#", "*", "&", "!", "|", ">", "%",
    "@", ",", ":", "-", " ", "", "é", "\U0001F600", "\ud800",
]
bad = []
N = 4000
for _ in range(N):
    s = "".join(random.choice(alphabet) for _ in range(random.randint(0, 8)))
    e = escape_string(s)
    for lang, load in (
        ("toml", lambda t: tomllib.loads('k = "%s"' % t)["k"]),
        ("json", lambda t: json.loads('{"k": "%s"}' % t)["k"]),
        ("yaml", lambda t: yaml.safe_load('k: "%s"' % t)["k"]),
        ("python", lambda t: ast.literal_eval('"%s"' % t)),
    ):
        try:
            got = load(e)
            if got != s:
                bad.append((lang, repr(s), "MISMATCH " + repr(got)))
        except Exception as ex:
            bad.append((lang, repr(s), "%s: %s" % (type(ex).__name__, str(ex)[:70])))

print("  cases:", N * 4, "| failures:", len(bad))
seen = set()
for b in bad:
    k = (b[0], b[2][:45])
    if k in seen:
        continue
    seen.add(k)
    print("   ", b)
    if len(seen) > 10:
        break
