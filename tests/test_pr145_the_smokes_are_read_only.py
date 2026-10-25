"""PR 145: a smoke reads production; it never writes to it.

`smoke-all.ps1` section 5 POSTed /rules/evaluate, so every run fired rules and
un-latched them (the comment in its section 6 records a "deadlock" that was the
script's own write). The 30-minute price job records its rule check (PR 143);
the smoke reads that instead.
"""
import pathlib
import re

WRITES = re.compile(r"\bApi\s+(POST|PUT|PATCH|DELETE)\b|-Method\s+(Post|Put|Patch|Delete)\b", re.I)
# A POST that writes nothing: dry runs, and endpoints that only compute an answer.
READ_ONLY = re.compile(r"dry_run=true|/api/v1/(ask|whatif|portfolio/risk|portfolio/performance|"
                       r"plan/frontier/preview|plan/frontier/rule-impact|plan/optimizer/compare|auth/m2m)\b", re.I)
# Older smokes that refresh a cache or act on the book, from before this rule. Frozen:
# a NEW smoke that writes fails here. Review before re-running any of them.
# (Every smoke before PR 139 that refreshes the goal solve, a backtest or a review.)
LEGACY_WRITERS = {"smoke-beat-market.ps1", "smoke-pr101-total-return.ps1", "smoke-pr105-own-currency.ps1",
                  "smoke-pr110-doors.ps1", "smoke-pr131-one-number-per-sleeve.ps1"} | {
                  f"smoke-pr{n}-{s}.ps1" for n, s in (
                      (91, "frontier-card"), (92, "goal-seeking"), (93, "tail-check"), (94, "goal-core"),
                      (95, "rules-aware"), (96, "protection-tuning"), (97, "faster-reactions"),
                      (98, "one-basis"), (99, "goal-levers"))}


def test_no_smoke_writes_to_production():
    bad = []
    for p in sorted(pathlib.Path("scripts/smoke").glob("*.ps1")):
        if p.name in LEGACY_WRITERS:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if WRITES.search(line) and not READ_ONLY.search(line):
                bad.append(f"{p.name}:{n}: {line.strip()[:100]}")
    assert not bad, "a smoke writes to production:\n" + "\n".join(bad)


def test_smoke_all_is_read_only():
    """The one smoke run after every deploy: not even a legacy exception."""
    src = pathlib.Path("scripts/smoke/smoke-all.ps1").read_text(encoding="utf-8")
    assert "rules/evaluate" not in "\n".join(x for x in src.splitlines() if not x.lstrip().startswith("#"))


def test_the_pre_push_hook_and_its_installer_are_checked_in():
    assert pathlib.Path("scripts/ops/pre-push").is_file()
    inst = pathlib.Path("scripts/ops/install-pre-push.sh").read_text()
    assert "pre-push" in inst and "self-test" in inst
