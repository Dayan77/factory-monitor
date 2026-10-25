"""Weibull 2-parameter life analysis with right censoring (docs/reports.md §2).

Pure functions, no statistics library (deterministic):
  * MLE: β = root of the profile score g(β) by bisection on [0.05, 50] with the ages scaled by max(t)
    (§2.2); η = (Σ t^β / r)^(1/β);
  * 90 % two-sided Fisher-matrix confidence bounds with log transform (§2.3), also for B10;
  * derived quantities: MTTF = η·Γ(1 + 1/β), B10, B10 / nominal life (§2.4);
  * conditional probability of failure in the next D days for a unit in service (§2.5);
  * fixed interpretation of β (§2.6) and the Weibull-plot points: Johnson adjusted ranks + Bernard (§2.7).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

Z90 = 1.644854            # 90 % two-sided (reports.md §2.3)
BETA_LO, BETA_HI = 0.05, 50.0
BISECT_TOL = 1e-10
BISECT_MAX_IT = 200
MIN_FAILURES = 3          # r < 3 → insufficient_failures
LOW_CONFIDENCE_BELOW = 10  # 3 ≤ r < 10 → low_confidence

PATTERN_TEXT = {
    "infant_mortality": "Falhas precoces: revisar montagem, qualidade do lote e partida. "
                        "Troca preventiva não ajuda.",
    "random": "Falhas aleatórias: troca por tempo não reduz falhas; preferir monitoramento de condição (preditiva).",
    "wear_out": "Desgaste: troca preventiva faz sentido; B10 é uma referência de intervalo.",
}
PATTERN_LABEL = {"infant_mortality": "mortalidade infantil (β < 1)", "random": "aleatório (β ≈ 1)",
                 "wear_out": "desgaste (β > 1)"}
LOW_CONFIDENCE_TEXT = "amostra pequena: com poucas falhas o β estimado tende a sair alto"


@dataclass
class Unit:
    """One installation period of a component (§2.1)."""
    age: Optional[float]          # usage_since_install at the end of the unit, in the measure of the type
    failed: bool                  # δ = 1
    label: str = ""
    component_id: Optional[str] = None
    removal_reason: Optional[str] = None
    removed_at: Optional[str] = None
    in_service: bool = False
    extra: dict = field(default_factory=dict)


def g_score(beta: float, x_all: Sequence[float], lnx_fail_mean: float) -> float:
    """§2.2 g(β) on scaled ages x = t / s (the ln s terms cancel)."""
    num = den = 0.0
    for x in x_all:
        p = x ** beta
        den += p
        num += p * math.log(x)
    return num / den - 1.0 / beta - lnx_fail_mean


def mle(failures: Sequence[float], suspensions: Sequence[float]) -> dict:
    """β, η by MLE with right censoring. Returns {'beta': None, 'reason': ...} when undefined."""
    alls = [float(t) for t in list(failures) + list(suspensions)]
    r = len(failures)
    if r < MIN_FAILURES:
        return {"beta": None, "eta": None, "reason": "insufficient_failures"}
    s = max(alls)
    x_all = [t / s for t in alls]
    lnf = sum(math.log(t / s) for t in failures) / r
    lo, hi = BETA_LO, BETA_HI
    glo, ghi = g_score(lo, x_all, lnf), g_score(hi, x_all, lnf)
    if not (glo < 0 < ghi):
        return {"beta": None, "eta": None, "reason": "degenerate"}
    for _ in range(BISECT_MAX_IT):
        mid = (lo + hi) / 2
        gm = g_score(mid, x_all, lnf)
        if gm < 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < BISECT_TOL:
            break
    beta = (lo + hi) / 2
    eta = (sum(x ** beta for x in x_all) / r) ** (1 / beta) * s
    return {"beta": beta, "eta": eta, "reason": None}


def fisher_cov(beta: float, eta: float, failures: Sequence[float], all_ages: Sequence[float]) -> Optional[tuple]:
    """(Var β, Var η, Cov β η) = (−H)⁻¹ with the observed information of §2.3."""
    r = len(failures)
    z = [(t / eta) ** beta for t in all_ages]
    lg = [math.log(t / eta) for t in all_ages]
    sz = sum(z)
    szl = sum(zi * li for zi, li in zip(z, lg))
    szll = sum(zi * li * li for zi, li in zip(z, lg))
    hbb = -r / beta ** 2 - szll
    hee = r * beta / eta ** 2 - (beta / eta ** 2) * sz - (beta ** 2 / eta ** 2) * sz
    hbe = -r / eta + sz / eta + (beta / eta) * szl
    # −H = [[a, b], [b, d]]
    a, b, d = -hbb, -hbe, -hee
    det = a * d - b * b
    if det <= 0 or not math.isfinite(det):
        return None
    return d / det, a / det, -b / det


def analyze(failures: Sequence[float], suspensions: Sequence[float], nominal_life: Optional[float] = None,
            z: float = Z90) -> dict:
    """Full result of one group (reports.md §2.2–§2.6). Undefined values are None with `reason`."""
    failures = [float(t) for t in failures]
    suspensions = [float(t) for t in suspensions]
    n, r = len(failures) + len(suspensions), len(failures)
    out: dict = {"n": n, "r": r, "suspensions": len(suspensions),
                 "simple_mttf": (sum(failures) / r) if r else None,
                 "simple_mttf_reason": None if r else "no_failures",
                 "beta": None, "eta": None, "se_beta": None, "se_eta": None, "cov_beta_eta": None,
                 "beta_lo": None, "beta_hi": None, "eta_lo": None, "eta_hi": None,
                 "mttf": None, "b10": None, "b10_lo": None, "b10_hi": None,
                 "b10_over_nominal": None, "nominal_life": nominal_life, "below_nominal": None,
                 "pattern": None, "pattern_text": None,
                 "low_confidence": MIN_FAILURES <= r < LOW_CONFIDENCE_BELOW, "reason": None}
    fit = mle(failures, suspensions)
    if fit["beta"] is None:
        out["reason"] = fit["reason"]
        out["low_confidence"] = False
        return out
    beta, eta = fit["beta"], fit["eta"]
    out.update(beta=beta, eta=eta, mttf=eta * math.gamma(1 + 1 / beta))
    k = math.log(-math.log(0.9))
    b10 = eta * (-math.log(0.9)) ** (1 / beta)
    out["b10"] = b10
    cov = fisher_cov(beta, eta, failures, failures + suspensions)
    if cov is not None:
        vb, ve, cbe = cov
        sb, se = math.sqrt(vb), math.sqrt(ve)
        out.update(se_beta=sb, se_eta=se, cov_beta_eta=cbe,
                   beta_lo=beta * math.exp(-z * sb / beta), beta_hi=beta * math.exp(z * sb / beta),
                   eta_lo=eta * math.exp(-z * se / eta), eta_hi=eta * math.exp(z * se / eta))
        vl = ve / eta ** 2 + (k / beta ** 2) ** 2 * vb - 2 * (k / beta ** 2) * cbe / eta
        if vl >= 0:
            out.update(b10_lo=b10 * math.exp(-z * math.sqrt(vl)), b10_hi=b10 * math.exp(z * math.sqrt(vl)))
        out["pattern"] = pattern(out["beta_lo"], out["beta_hi"])
    else:
        out["ci_reason"] = "singular_information"
    out["pattern_text"] = PATTERN_TEXT.get(out["pattern"]) if out["pattern"] else None
    if nominal_life:
        out["b10_over_nominal"] = b10 / nominal_life
        out["below_nominal"] = out["b10_over_nominal"] < 0.8
    return out


def pattern(beta_lo: Optional[float], beta_hi: Optional[float]) -> Optional[str]:
    """§2.6: whole CI < 1 → infant mortality; contains 1 → random; whole CI > 1 → wear-out."""
    if beta_lo is None or beta_hi is None:
        return None
    if beta_hi < 1:
        return "infant_mortality"
    if beta_lo > 1:
        return "wear_out"
    return "random"


def reliability(t: float, beta: float, eta: float) -> float:
    return math.exp(-((t / eta) ** beta))


def conditional_failure(age: float, rate_per_day: Optional[float], beta: Optional[float], eta: Optional[float],
                        days: float = 30) -> dict:
    """§2.5: P = 1 − R(a + u·D) / R(a). u = 0 → null (no_recent_usage)."""
    if beta is None or eta is None:
        return {"p_fail": None, "reason": "no_fit"}
    if not rate_per_day or rate_per_day <= 0:
        return {"p_fail": None, "reason": "no_recent_usage"}
    a = max(float(age or 0.0), 0.0)
    # 1 − exp(−((a+uD)/η)^β + (a/η)^β), computed without dividing two tiny reliabilities
    h = ((a + rate_per_day * days) / eta) ** beta - (a / eta) ** beta
    return {"p_fail": -math.expm1(-h), "reason": None, "age_end": a + rate_per_day * days}


def plot_points(failures: Sequence[float], suspensions: Sequence[float]) -> list[dict]:
    """§2.7: Johnson adjusted ranks + Bernard median ranks; ties: failure before suspension."""
    items = sorted([(float(t), 0) for t in failures] + [(float(t), 1) for t in suspensions])
    n = len(items)
    prev = 0.0
    out = []
    for i, (t, is_susp) in enumerate(items):
        if is_susp:
            continue
        rr = n - i                                  # units with age ≥ this one (reverse rank)
        prev = prev + (n + 1 - prev) / (1 + rr)
        f = (prev - 0.3) / (n + 0.4)
        out.append({"t": t, "rank": prev, "f": f, "x": math.log(t) if t > 0 else None,
                    "y": math.log(-math.log(1 - f)) if 0 < f < 1 else None})
    return out


def fit_line(beta: float, eta: float, t_min: float, t_max: float, steps: int = 2) -> list[dict]:
    """Points of the MLE line on Weibull paper: y = β·(ln t − ln η)."""
    if t_min <= 0 or t_max <= t_min:
        return []
    out = []
    for k in range(steps):
        t = t_min * (t_max / t_min) ** (k / max(steps - 1, 1))
        out.append({"t": t, "x": math.log(t), "y": beta * (math.log(t) - math.log(eta))})
    return out


def eta_ci_overlap(a: dict, b: dict) -> Optional[bool]:
    """§1.5: two groups differ 'significantly' only when their η 90 % CIs do not overlap."""
    if None in (a.get("eta_lo"), a.get("eta_hi"), b.get("eta_lo"), b.get("eta_hi")):
        return None
    return not (a["eta_hi"] < b["eta_lo"] or b["eta_hi"] < a["eta_lo"])
