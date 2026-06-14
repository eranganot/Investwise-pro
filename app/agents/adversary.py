"""ADVERSARY agent (Section 6) - mandatory red-team cross-examination.

Phase 1.3 upgrade: instead of a single final-word critique, the Adversary now
**examines the output of every agent as the pipeline runs**. After each
transition (Detect -> Vet -> Optimize -> Rank) the state is routed here, where a
set of deterministic invariant checks look for mathematical blind spots,
internal inconsistencies, and challenged assumptions *before* the next agent is
allowed to proceed. A BLOCK-severity finding becomes a hard veto.

Two design rules:
  * The checks are **deterministic** - provable assertions, not opinions - so
    they're reproducible and unit-testable. This is the core red-team.
  * An **optional LLM narrative** can be layered on top (off by default, behind
    ``adversary_llm_enabled`` + an ANTHROPIC_API_KEY env var). It only turns the
    deterministic findings into prose; it never invents numbers and never sits
    on the scoring path.

The legacy ``critique`` / ``should_veto`` helpers are preserved for the existing
final-word call sites.
"""
from __future__ import annotations

import os
from enum import Enum

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.schemas.scoring import IMPACT_WEIGHTS
from app.schemas.safety import SafetyReport
from app.schemas.state_machine import (
    DetectedSignal,
    OptimizedSignal,
    RankedSignal,
    VettedSignal,
)
from app.schemas.validation import STRICT

_TOL = 0.5  # tolerance for recomputation cross-checks (rounding headroom)


class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


class AdversaryNote(BaseModel):
    """One stage's cross-examination result."""
    model_config = STRICT
    stage: str
    severity: Severity = Severity.OK
    findings: list[str] = []
    critique: str = ""

    @property
    def blocks(self) -> bool:
        return self.severity == Severity.BLOCK


def _note(stage: str, findings: list[str], block: bool = False) -> AdversaryNote:
    if not findings:
        return AdversaryNote(stage=stage, severity=Severity.OK,
                             critique=f"{stage}: no blind spots found.")
    sev = Severity.BLOCK if block else Severity.WARN
    return AdversaryNote(stage=stage, severity=sev, findings=findings,
                         critique=f"{stage}: " + "; ".join(findings) + ".")


class Adversary:
    """Deterministic red-team examiner with an optional LLM narrative layer."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- per-stage deterministic examiners ---
    def examine_detected(self, det: DetectedSignal) -> AdversaryNote:
        f: list[str] = []
        er, vol = det.expected_return_pct, det.volatility_pct
        if er is not None and vol is not None:
            if vol == 0.0 and er > 0:
                f.append("a positive expected return with zero volatility implies "
                         "risk-free arbitrage - suspect the input data")
            elif vol > 0 and er / vol > 3.0:
                f.append(f"return/volatility ratio {er/vol:.1f} (>3) is implausibly "
                         f"high - verify the return assumption isn't overstated")
        if det.gross_gain_ils is not None and det.gross_gain_ils > 0 and det.loss_carry_forward_ils > 5 * det.gross_gain_ils:
            f.append("loss carry-forward dwarfs the gain - confirm it isn't double-counted")
        return _note("Detected", f)

    def examine_vetted(self, v: VettedSignal) -> AdversaryNote:
        f: list[str] = []
        if v.probability_of_ruin is not None and v.max_drawdown is None:
            f.append("probability of ruin computed but max drawdown is missing - "
                     "inconsistent risk output")
        if v.probability_of_ruin is None and v.volatility is not None:
            f.append("volatility present but ruin probability not computed")
        if not v.veto_flag and v.probability_of_ruin is not None \
                and v.probability_of_ruin > self.settings.ruin_probability_cap:
            f.append(f"ruin probability {v.probability_of_ruin:.0%} exceeds the "
                     f"{self.settings.ruin_probability_cap:.0%} cap yet was not vetoed")
        return _note("Vetted", f)

    def examine_optimized(self, o: OptimizedSignal) -> AdversaryNote:
        f: list[str] = []
        block = False
        det = o.source.source
        gross = det.gross_gain_ils
        if o.net_gain_delta is not None and gross is not None and gross > 0 \
                and o.net_gain_delta > gross + 1e-6:
            f.append(f"net-after-tax ({o.net_gain_delta:,.0f}) exceeds gross gain "
                     f"({gross:,.0f}) - tax math is wrong")
            block = True
        if o.tax_saved is not None and o.tax_saved < -1e-6:
            f.append(f"negative tax saved ({o.tax_saved:,.0f}) is impossible")
            block = True
        if (o.actual_tax_cost or 0) > 0 and (o.tax_deferred or 0) > 0:
            f.append("tax is reported as both realized now and deferred - ambiguous")
        return _note("Optimized", f, block=block)

    def examine_ranked(self, r: RankedSignal) -> AdversaryNote:
        f: list[str] = []
        s = r.scores
        w = IMPACT_WEIGHTS
        recomputed = (w["return"] * s.ret + w["tax"] * s.tax + w["risk"] * s.risk
                      + w["liquidity"] * s.liquidity + w["conviction"] * s.conviction) / r.complexity_factor
        if abs(recomputed - r.impact_score) > _TOL:
            f.append(f"impact_score {r.impact_score:.1f} disagrees with the weighted "
                     f"sub-score recomputation {recomputed:.1f} - scoring drift")
        if r.impact_score >= 80 and r.confidence < 40:
            f.append(f"high impact ({r.impact_score:.0f}) at low confidence "
                     f"({r.confidence:.0f}%) - challenge the conviction")
        return _note("Ranked", f)

    # --- optional LLM narrative (off by default; never on the scoring path) ---
    def narrate(self, notes: list[AdversaryNote], *, context: str = "") -> str | None:
        """Optional Google Gemini narrative over the deterministic findings.

        Off unless ``adversary_llm_enabled`` and a ``GOOGLE_API_KEY`` (or
        ``GEMINI_API_KEY``) is set. Never invents numbers and never raises - a
        narrative failure must not affect the deterministic scoring path.
        Works with either the ``google-generativeai`` or the newer ``google-genai``
        SDK (whichever is installed).
        """
        if not getattr(self.settings, "adversary_llm_enabled", False):
            return None
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        model_name = getattr(self.settings, "adversary_llm_model", "gemini-2.0-flash")
        bullet = "\n".join(f"- [{n.severity.value}] {n.critique}" for n in notes)
        prompt = (
            "You are a skeptical investment risk red-teamer. Given these "
            "deterministic findings from a wealth pipeline, write 2-3 sentences "
            "challenging the logic and assumptions. Do NOT invent numbers; only "
            f"reference what is given.\nContext: {context}\nFindings:\n{bullet}"
        )
        try:  # defensive: a narrative failure must never break the pipeline
            try:
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=key)
                resp = genai.GenerativeModel(model_name).generate_content(prompt)
            except ImportError:
                from google import genai  # type: ignore  # newer google-genai SDK
                resp = genai.Client(api_key=key).models.generate_content(
                    model=model_name, contents=prompt)
            return (getattr(resp, "text", "") or "").strip() or None
        except Exception:
            return None


# --- legacy final-word helpers (unchanged API, used by the feed/war-room) ---
def critique(*, path: str, risk_critique: str, confidence: float,
             impact: float, safety: SafetyReport | None = None) -> str:
    parts = [risk_critique.rstrip(".") + "."] if risk_critique else []
    parts.append(
        f"Confidence {confidence:.0f}% on the {path} path; re-validate if the "
        f"divergence closes or volatility regime shifts."
    )
    if safety and safety.flags:
        parts.append("Safety: " + "; ".join(f.detail for f in safety.flags) + ".")
    return " ".join(parts)


def should_veto(safety: SafetyReport | None) -> bool:
    return bool(safety and safety.verdict == "block")
