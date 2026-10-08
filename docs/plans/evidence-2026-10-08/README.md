# Evidence: 2026-10-08 investigation

The Evidence Blocks (symptom, evidence, ruled out, root cause, why silent, blast radius, confidence, fix) behind
[`../EXECUTION_PLAN_2026-10-08_hardening.md`](../EXECUTION_PLAN_2026-10-08_hardening.md). Produced read-only against
`93732f9` / `88bb5d4`, plus live production reads of `/health`, `/push/status`, `/data-status` and `/recommendations`, and the Railway CLI.

| File | Covers |
|---|---|
| `inv-funding.md` | C1, C2 (a/b/c), C3 (+ replace-book), C9, C10, L4 |
| `inv-jobs.md` | C4, C5, C11, L10, L11, O3, L5 (tests) |
| `inv-security.md` | C6, C7/C7b, C8, S1–S6, L1 deletion analysis |
| `inv-cleanup.md` | L2, L3, L5–L9, L12–L15, P2, P4 inventory |
| `probes/` | The scratch tests that reproduced each fault |

**Running a probe** (from the repo root; probes sit outside `testpaths`, so CI ignores them):

```bash
python -m pytest -q -s -p no:cacheprovider --rootdir . -c pyproject.toml docs/plans/evidence-2026-10-08/probes/scratch_funding/test_c2_drift_trim.py
```

Some probes print the faulty value and pass. They were written to *show* the defect, not to gate on it. When a PR fixes a
finding, turn its probe into a real test in `tests/` that **fails on the old code**: invert the assertion so it demands the correct value.
`probes/scratch_jobs/c11_yahoo.py` calls Yahoo live; the others are offline.
