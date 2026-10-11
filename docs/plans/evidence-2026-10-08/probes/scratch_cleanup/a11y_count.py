import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
lines = src.splitlines()
tags = re.findall(r"<([a-zA-Z0-9]+)\b[^<>]*?\bonclick=", src)
from collections import Counter
print("onclick by tag:", Counter(t.lower() for t in tags))
print("tabindex:", len(re.findall(r"tabindex", src)))
print('role="button":', len(re.findall(r'role="button"', src)))
print(":focus rules:", len(re.findall(r":focus", src)))
print("outline:none/0:", len(re.findall(r"outline:\s*(none|0)", src)))
print("alert(:", len(re.findall(r"\balert\(", src)), "confirm(:", len(re.findall(r"\bconfirm\(", src)), "prompt(:", len(re.findall(r"\bprompt\(", src)))
print("style=\" inline:", len(re.findall(r'style="', src)))
for i, l in enumerate(lines, 1):
    if re.search(r"keydown|keyup|Escape|aria-modal|role=\"dialog\"", l):
        print(i, l.strip()[:180])
for i, l in enumerate(lines, 1):
    if re.search(r"function (openSheet|closeSheet|closeCash|openCash)\b", l):
        print("DEF", i, l.strip()[:120])
# modal/overlay containers
print("class=\"ov / modal\":", len(re.findall(r'class="(ov|modal|sheet)', src)))
for i, l in enumerate(lines, 1):
    if re.search(r'class="ov[ "]|id="[a-z]*ov"', l):
        print("OV", i, l.strip()[:140])
