"""The deploy settings, read from `.railway/railway.ts` (PR 119).

Railway's infrastructure-as-code file replaced `railway.json`. The tests that
pinned the deploy (migrations run before it, a healthcheck gates it, the
Dockerfile builds it) read the same keys from the TypeScript `build` and
`deploy` objects. Only literal values are supported -- strings, numbers and
string arrays -- which is all the file is allowed to contain.
"""
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
IAC = REPO / ".railway" / "railway.ts"


def _block(name: str) -> dict:
    src = re.sub(r"//[^\n]*", "", IAC.read_text(encoding="utf-8"))
    m = re.search(rf"\b{name}:\s*\{{(.*?)\}}", src, re.S)
    assert m, f".railway/railway.ts has no `{name}: {{...}}` block"
    out = {}
    for k, v in re.findall(r"(\w+):\s*(\"[^\"]*\"|\[[^\]]*\]|-?\d+(?:\.\d+)?)", m.group(1)):
        out[k] = json.loads(v)
    return out


def deploy() -> dict:
    return _block("deploy")


def build() -> dict:
    return _block("build")
