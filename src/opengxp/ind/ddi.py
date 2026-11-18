"""ICH M12 (2024): can the in vitro data exclude a clinical drug interaction with the investigational drug
as the precipitant (inhibitor or inducer)?

Order of evaluation per target, as in M12:
1. basic method (sections 2.1.2, 2.1.4.1, 2.2.2 / Table 1);
2. if not excluded and data are given: basic kinetic model for induction (2.1.4.3) and the mechanistic
   static model (7.5.1, AUCR within 0.80-1.25);
3. if still not excluded: further evaluation (PBPK) or a clinical DDI study with a sensitive index substrate.

All concentrations are micromolar (µM). Doses are mg, converted with the molecular weight.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from opengxp.core import Finding, GxPInputError, GxPResult, provenance
from opengxp.ind.common import M12_URL, cite, effective_fu, sig

CYP = Literal["CYP1A2", "CYP2B6", "CYP2C8", "CYP2C9", "CYP2C19", "CYP2D6", "CYP3A"]
UGT = Literal["UGT1A1", "UGT1A4", "UGT1A9", "UGT2B7", "UGT2B15"]
Transporter = Literal["P-gp", "BCRP", "OATP1B1", "OATP1B3", "OAT1", "OAT3", "OCT2", "MATE1", "MATE2-K"]

# M12 Table 6: hepatic kdeg (/min); CYP3A uses CYP3A4
KDEG_HEPATIC = {"CYP1A2": 0.00030, "CYP2B6": 0.00036, "CYP2C8": 0.00053, "CYP2C9": 0.00011,
                "CYP2C19": 0.00044, "CYP2D6": 0.00023, "CYP3A": 0.00032}
KDEG_GUT_CYP3A4 = 0.00048
Q_H_L_PER_MIN = 97 / 60     # M12 Table 3: hepatic blood flow, 97 L/h/70 kg
Q_EN_L_PER_MIN = 18 / 60    # M12 Table 3: enterocyte blood flow, 18 L/h/70 kg
GUIDE = "ICH M12 (2024)"


class Perpetrator(BaseModel):
    """The investigational drug at the highest recommended clinical dose, at steady state."""
    cmax_total_uM: float = Field(gt=0, description="Steady-state total (bound + free) plasma Cmax at the highest dose, µM.")
    fu_p: float = Field(gt=0, le=1, description="Fraction unbound in plasma.")
    fu_p_reliable_below_1pct: bool = Field(False, description="True only if a measured fu,p < 0.01 has been shown accurate "
                                                              "and precise (validated assay, positive controls). Otherwise 0.01 is used.")
    dose_mg: float = Field(gt=0, description="Highest single clinical dose, mg.")
    mw_g_per_mol: float = Field(gt=0, description="Molecular weight, g/mol (converts doses to µM).")
    route: Literal["oral", "parenteral"] = "oral"
    fa: float = Field(1.0, gt=0, le=1, description="Fraction absorbed; M12 default 1.")
    fg: float = Field(1.0, gt=0, le=1, description="Fraction escaping gut metabolism (of the investigational drug); M12 default 1.")
    ka_per_min: float = Field(0.1, gt=0, description="Absorption rate constant, /min; M12 default 0.1.")
    blood_to_plasma: float | None = Field(None, gt=0, description="Blood-to-plasma ratio RB. If omitted, 1 is used and flagged.")


class InductionDonor(BaseModel):
    donor_id: str
    concentrations_uM: list[float] = Field(min_length=1, description="Test concentrations, µM.")
    fold_change: list[float] = Field(min_length=1, description="mRNA fold change over vehicle at each concentration.")
    positive_control_fold: float | None = Field(None, gt=0, description="mRNA fold change of the positive control inducer.")
    concentration_dependent: bool | None = Field(None, description="Your judgement of a concentration-dependent increase. "
                                                                   "If omitted, the tool infers it from the trend and says so.")

    @model_validator(mode="after")
    def _same_length(self):
        if len(self.concentrations_uM) != len(self.fold_change):
            raise ValueError(f"donor {self.donor_id}: concentrations_uM and fold_change differ in length")
        return self


class EnzymeData(BaseModel):
    enzyme: CYP | UGT
    ki_u_uM: float | None = Field(None, gt=0, description="Unbound reversible inhibition constant Ki,u, µM.")
    KI_u_uM: float | None = Field(None, gt=0, description="Time-dependent inhibition: unbound KI, µM.")
    kinact_per_min: float | None = Field(None, gt=0, description="Time-dependent inhibition: kinact, /min.")
    kdeg_per_min: float | None = Field(None, gt=0, description="Override hepatic kdeg; default from M12 Table 6.")
    induction_donors: list[InductionDonor] | None = Field(None, description="Basic mRNA fold-change data, one entry per donor (≥3).")
    emax: float | None = Field(None, ge=0, description="Maximum induction effect as fold increase above vehicle (max fold change - 1).")
    ec50_u_uM: float | None = Field(None, gt=0, description="Unbound EC50 for induction, µM.")
    d: float = Field(1.0, gt=0, description="Induction scaling factor; 1 unless calibrated in the hepatocyte batch.")
    substrate_fm: float | None = Field(None, gt=0, le=1, description="Mechanistic static model: fraction of the victim's hepatic "
                                                                     "clearance via this enzyme. Enables the AUCR calculation.")
    substrate_fg: float = Field(1.0, gt=0, le=1, description="Mechanistic static model: victim's Fg (gut availability). CYP3A only.")


class TransporterData(BaseModel):
    transporter: Transporter
    ic50_u_uM: float = Field(gt=0, description="Unbound IC50 (≈ Ki,u when substrate << Km), µM.")


class DDICheck(BaseModel):
    target: str
    mechanism: Literal["reversible_inhibition", "gut_inhibition", "time_dependent_inhibition", "induction",
                       "inhibition", "combined", "transporter_inhibition"]
    method: str
    value: float | None = Field(description="Computed ratio / R / AUCR.")
    criterion: str = Field(description="What excludes the risk, as written in M12.")
    risk_excluded: bool | None = Field(description="None when inconclusive.")
    section: str
    detail: str | None = None


class TargetConclusion(BaseModel):
    target: str
    mechanism: str
    risk_excluded: bool | None
    decided_by: str
    next_step: str | None


class DDIRiskResult(GxPResult):
    cmax_u_uM: float
    dose_conc_gut_uM: float | None = Field(description="Dose/250 mL in µM (oral only).")
    cmax_inlet_u_uM: float
    i_gut_uM: float | None = Field(description="[I]g = Fa·ka·Dose/Qen, µM (oral only).")
    checks: list[DDICheck]
    conclusions: list[TargetConclusion]


def _ratio_check(target, mechanism, method, num, den, cut, crit, section, detail=None) -> DDICheck:
    r = num / den
    return DDICheck(target=target, mechanism=mechanism, method=method, value=sig(r), criterion=crit,
                    risk_excluded=r < cut, section=section, detail=detail)


def _concentration_dependent(d: InductionDonor) -> tuple[bool, bool]:
    """(dependent, inferred)."""
    if d.concentration_dependent is not None:
        return d.concentration_dependent, False
    pts = sorted(zip(d.concentrations_uM, d.fold_change))
    if len(pts) < 2:
        return False, True
    up = sum(b[1] > a[1] for a, b in pairwise(pts))
    down = sum(b[1] < a[1] for a, b in pairwise(pts))
    return pts[-1][1] > pts[0][1] and up >= down, True


def _mech_static(e: EnzymeData, ih: float, ig: float | None, oral: bool) -> dict[str, float]:
    gut = oral and e.enzyme == "CYP3A" and ig is not None
    kdeg_h = e.kdeg_per_min or KDEG_HEPATIC.get(e.enzyme, 0.0)

    def terms(i: float, kdeg: float, inhib: bool, induce: bool) -> float:
        a = 1 / (1 + i / e.ki_u_uM) if inhib and e.ki_u_uM else 1.0
        b = (kdeg / (kdeg + i * e.kinact_per_min / (i + e.KI_u_uM))
             if inhib and e.KI_u_uM and e.kinact_per_min and kdeg else 1.0)
        c = 1 + e.d * e.emax * i / (i + e.ec50_u_uM) if induce and e.emax is not None and e.ec50_u_uM else 1.0
        return a * b * c

    def aucr(inhib: bool, induce: bool) -> float:
        fm, fg = e.substrate_fm, e.substrate_fg if gut else 1.0
        g = 1.0 if not gut else 1 / (terms(ig, KDEG_GUT_CYP3A4, inhib, induce) * (1 - fg) + fg)
        h = 1 / (terms(ih, kdeg_h, inhib, induce) * fm + (1 - fm))
        return g * h

    out = {"combined": aucr(True, True)}
    has_inhib = bool(e.ki_u_uM or (e.KI_u_uM and e.kinact_per_min))
    has_induce = e.emax is not None and e.ec50_u_uM is not None
    if has_inhib and has_induce:  # M12 7.5.1.1: also evaluate each alone
        out["inhibition_only"] = aucr(True, False)
        out["induction_only"] = aucr(False, True)
    return out


def assess_ddi_risk(perpetrator: Perpetrator, enzymes: list[EnzymeData] | None = None,
                    transporters: list[TransporterData] | None = None) -> DDIRiskResult:
    enzymes, transporters = enzymes or [], transporters or []
    if not enzymes and not transporters:
        raise GxPInputError("Give at least one entry in enzymes or transporters (each with its in vitro parameters).")
    p = perpetrator
    oral = p.route == "oral"
    assumptions: list[str] = ["Concentrations in µM; Ki, KI, IC50 and EC50 are unbound values.",
                              "Cmax is the steady-state Cmax at the highest recommended clinical dose."]
    findings: list[Finding] = []
    fu, note = effective_fu(p.fu_p, p.fu_p_reliable_below_1pct)
    if note:
        assumptions.append(note)
    rb = p.blood_to_plasma
    if rb is None:
        rb = 1.0
        findings.append(Finding(level="warning", code="RB_DEFAULTED",
                                message="Blood-to-plasma ratio not given; RB = 1 used for the liver inlet concentration."))
    dose_umol = p.dose_mg / p.mw_g_per_mol * 1000
    cmax_u = p.cmax_total_uM * fu
    dose_conc = dose_umol / 0.25 if oral else None
    inlet_u = fu * (p.cmax_total_uM + p.fa * p.fg * p.ka_per_min * dose_umol / Q_H_L_PER_MIN / rb)
    ig = p.fa * p.ka_per_min * dose_umol / Q_EN_L_PER_MIN if oral else None
    if (p.fa, p.fg, p.ka_per_min) == (1.0, 1.0, 0.1):
        assumptions.append("Fa = 1, Fg = 1, ka = 0.1/min (M12 worst-case defaults) for the liver inlet and gut concentrations.")

    checks: list[DDICheck] = []
    concl: list[TargetConclusion] = []
    s_rev, s_tdi = "M12 2.1.2.1", "M12 2.1.2.2"

    for e in enzymes:
        is_ugt = e.enzyme.startswith("UGT")
        # --- inhibition: reversible (+ gut CYP3A) and TDI, basic method
        inhib_checks: list[DDICheck] = []
        if e.ki_u_uM:
            sec = "M12 2.1.3" if is_ugt else s_rev
            inhib_checks.append(_ratio_check(
                e.enzyme, "reversible_inhibition", "basic", cmax_u, e.ki_u_uM, 0.02, "Cmax,u / Ki,u < 0.02 (Ki,u > 50 × Cmax,u)",
                sec, "No established UGT cutoff; M12 allows the CYP criterion or a justified alternative." if is_ugt else None))
            if e.enzyme == "CYP3A" and oral:
                inhib_checks.append(_ratio_check(
                    "CYP3A (intestinal)", "gut_inhibition", "basic", dose_conc, e.ki_u_uM, 10,
                    "(Dose/250 mL) / Ki,u < 10 (Ki,u > 0.1 × Dose/250 mL)", s_rev))
        if e.KI_u_uM and e.kinact_per_min and not is_ugt:
            kdeg = e.kdeg_per_min or KDEG_HEPATIC[e.enzyme]
            kobs = e.kinact_per_min * 5 * cmax_u / (e.KI_u_uM + 5 * cmax_u)
            r2 = (kobs + kdeg) / kdeg
            inhib_checks.append(DDICheck(target=e.enzyme, mechanism="time_dependent_inhibition", method="basic",
                                         value=sig(r2), criterion="(kobs + kdeg) / kdeg < 1.25, kobs at 5 × Cmax,u",
                                         risk_excluded=r2 < 1.25, section=s_tdi,
                                         detail=f"kobs = {sig(kobs)} /min; kdeg = {kdeg:g} /min"
                                                + ("" if e.kdeg_per_min else " (M12 Table 6)")))
        elif (e.KI_u_uM is None) != (e.kinact_per_min is None):
            raise GxPInputError(f"{e.enzyme}: time-dependent inhibition needs both KI_u_uM and kinact_per_min.")
        checks += inhib_checks

        # --- induction, basic fold-change and basic kinetic model
        ind_checks: list[DDICheck] = []
        if e.enzyme == "CYP2D6" and (e.induction_donors or e.emax is not None):
            findings.append(Finding(level="info", code="CYP2D6_NOT_INDUCIBLE",
                                    message="CYP2D6 is generally considered not inducible by drugs (M12 2.1.1); induction data ignored."))
        elif e.induction_donors:
            if len(e.induction_donors) < 3:
                findings.append(Finding(level="warning", code="FEWER_THAN_3_DONORS",
                                        message=f"{e.enzyme}: M12 asks for hepatocytes from at least 3 donors; {len(e.induction_donors)} given."))
            limit = 50 * cmax_u
            positive, inconclusive, notes = False, False, []
            for d in e.induction_donors:
                dep, inferred = _concentration_dependent(d)
                below = [(c, f) for c, f in zip(d.concentrations_uM, d.fold_change) if c < limit]
                hit = any(f >= 2 for _, f in below)
                notes.append(f"donor {d.donor_id}: max fold {max((f for _, f in below), default=float('nan')):.2f} below 50×Cmax,u; "
                             f"concentration-dependent={dep}{' (inferred)' if inferred else ''}")
                if dep and hit:
                    positive = True
                elif d.positive_control_fold is not None and d.positive_control_fold < 6 and d.positive_control_fold > 1:
                    pct = [(f - 1) / (d.positive_control_fold - 1) * 100 for _, f in below if f < 2]
                    if any(x > 20 for x in pct):
                        inconclusive = True
                        notes.append(f"donor {d.donor_id}: positive control < 6-fold and response > 20% of it")
            ind_checks.append(DDICheck(
                target=e.enzyme, mechanism="induction", method="basic mRNA fold-change",
                value=None, criterion="Not excluded if, in any donor, mRNA rises concentration-dependently and is ≥ 2-fold "
                                      "at concentrations < 50 × Cmax,u",
                risk_excluded=False if positive else (None if inconclusive else True),
                section="M12 2.1.4.1", detail=f"50 × Cmax,u = {sig(limit)} µM; " + "; ".join(notes)))
        if e.emax is not None and e.ec50_u_uM and e.enzyme != "CYP2D6":
            r = 1 / (1 + e.d * e.emax * 10 * cmax_u / (e.ec50_u_uM + 10 * cmax_u))
            ind_checks.append(DDICheck(target=e.enzyme, mechanism="induction", method="basic kinetic model",
                                       value=sig(r), criterion="R > 0.8 (R = predicted AUC ratio of a sensitive substrate)",
                                       risk_excluded=r > 0.8, section="M12 2.1.4.3", detail=f"d = {e.d:g}"))
        checks += ind_checks

        # --- mechanistic static model
        mech = None
        if e.substrate_fm is not None and (inhib_checks or ind_checks) and not is_ugt:
            mech = _mech_static(e, inlet_u, ig, oral)
            has_inhib = bool(e.ki_u_uM or e.KI_u_uM)
            for k, v in mech.items():
                kind = ("induction" if k == "induction_only" or not has_inhib else
                        "inhibition" if k == "inhibition_only" or not ind_checks else "combined")
                checks.append(DDICheck(target=e.enzyme, mechanism=kind,
                                       method=f"mechanistic static AUCR ({k.replace('_', ' ')})", value=sig(v),
                                       criterion="0.80 ≤ AUCR ≤ 1.25", risk_excluded=0.80 <= v <= 1.25, section="M12 7.5.1",
                                       detail=f"[I]h = {sig(inlet_u)} µM" + (f", [I]g = {sig(ig)} µM" if ig and e.enzyme == "CYP3A" else "")
                                              + f"; victim fm = {e.substrate_fm:g}"))

        def conclude(mech_name: str, basics: list[DDICheck], model_ok: bool | None, model_label: str):
            if not basics:
                return
            if all(c.risk_excluded for c in basics):
                concl.append(TargetConclusion(target=e.enzyme, mechanism=mech_name, risk_excluded=True,
                                              decided_by="basic method", next_step=None))
            elif model_ok:
                concl.append(TargetConclusion(target=e.enzyme, mechanism=mech_name, risk_excluded=True,
                                              decided_by=model_label, next_step="Submit model inputs with supporting data."))
            else:
                incon = any(c.risk_excluded is None for c in basics) and not any(c.risk_excluded is False for c in basics)
                concl.append(TargetConclusion(
                    target=e.enzyme, mechanism=mech_name, risk_excluded=None if incon else False,
                    decided_by="inconclusive basic method" if incon else "basic method" + (" and " + model_label if model_ok is False else ""),
                    next_step="Mechanistic static or PBPK model, else a clinical DDI study with a sensitive index substrate."))

        def within(v: float | None) -> bool | None:
            return None if v is None else 0.80 <= v <= 1.25

        mech = mech or {}
        conclude("inhibition", inhib_checks, within(mech.get("inhibition_only", mech.get("combined"))),
                 "mechanistic static model")
        fold = [c for c in ind_checks if c.method == "basic mRNA fold-change"]
        kin = [c for c in ind_checks if c.method == "basic kinetic model"]
        if fold:
            ind_model_ok = kin[0].risk_excluded if kin else None
            if ind_model_ok is not True and mech:
                ind_model_ok = within(mech.get("induction_only", mech.get("combined")))
            conclude("induction", fold, ind_model_ok, "basic kinetic / mechanistic static model")
        elif kin:
            conclude("induction", kin, within(mech.get("induction_only")) if mech else None, "mechanistic static model")

    for t in transporters:
        n = t.transporter
        if n in ("P-gp", "BCRP"):
            if oral:
                c = _ratio_check(n, "transporter_inhibition", "basic", dose_conc, t.ic50_u_uM, 10,
                                 "(Dose/250 mL) / IC50,u < 10 (oral)", "M12 2.2.2 Table 1")
            else:
                c = _ratio_check(n, "transporter_inhibition", "basic", cmax_u, t.ic50_u_uM, 0.02,
                                 "Cmax,u / IC50,u < 0.02 (parenteral)", "M12 2.2.2")
        elif n in ("OATP1B1", "OATP1B3"):
            c = _ratio_check(n, "transporter_inhibition", "basic", inlet_u, t.ic50_u_uM, 0.1,
                             "Cmax,inlet,u / IC50,u < 0.1", "M12 2.2.2 Table 1")
        elif n in ("OAT1", "OAT3", "OCT2"):
            c = _ratio_check(n, "transporter_inhibition", "basic", cmax_u, t.ic50_u_uM, 0.1,
                             "Cmax,u / IC50,u < 0.1", "M12 2.2.2 Table 1")
        else:
            c = _ratio_check(n, "transporter_inhibition", "basic", cmax_u, t.ic50_u_uM, 0.02,
                             "Cmax,u / IC50,u < 0.02", "M12 2.2.2 Table 1")
        checks.append(c)
        concl.append(TargetConclusion(
            target=n, mechanism="transporter inhibition", risk_excluded=c.risk_excluded, decided_by="basic method",
            next_step=None if c.risk_excluded else "Consider a clinical study given likely co-medications that are substrates "
                                                   "of this transporter, or mechanistic static / PBPK / endogenous biomarkers."))

    open_ = [c for c in concl if c.risk_excluded is False]
    unclear = [c for c in concl if c.risk_excluded is None]
    for c in open_:
        findings.append(Finding(level="action_required", code="DDI_RISK_NOT_EXCLUDED",
                                message=f"{c.target} {c.mechanism}: risk not excluded in vitro. {c.next_step}"))
    for c in unclear:
        findings.append(Finding(level="warning", code="DDI_INCONCLUSIVE",
                                message=f"{c.target} {c.mechanism}: inconclusive; further evaluation recommended (M12 7.3.4)."))
    summary = (f"{len(concl) - len(open_) - len(unclear)} of {len(concl)} target/mechanism pairs excluded by in vitro data"
               + (f"; not excluded: {', '.join(f'{c.target} {c.mechanism}' for c in open_)}" if open_ else "")
               + (f"; inconclusive: {', '.join(f'{c.target} {c.mechanism}' for c in unclear)}" if unclear else "") + ".")
    sections = sorted({c.section for c in checks})
    cites = [cite(GUIDE, s, None, M12_URL) for s in sections]
    inputs = {"perpetrator": p.model_dump(), "enzymes": [e.model_dump() for e in enzymes],
              "transporters": [t.model_dump() for t in transporters]}
    return DDIRiskResult(
        summary=summary, findings=findings, assumptions=assumptions, citations=cites,
        provenance=provenance("ind_ddi_risk", GUIDE, inputs, ["M12 2.1-2.2 cutoffs and Table 3/6 equations; hand-computed cases"]),
        cmax_u_uM=sig(cmax_u), dose_conc_gut_uM=sig(dose_conc) if dose_conc else None, cmax_inlet_u_uM=sig(inlet_u),
        i_gut_uM=sig(ig) if ig else None, checks=checks, conclusions=concl)
