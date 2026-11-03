# DML v53 — source and verification boundary

This record describes the current revision. The v52 records under `provenance/` are inherited statements, not new verification results.

## Work actually performed

- Reorganized the manuscript so that the fixed-input-sector optimization is introduced before peak-sensitivity applications.
- Checked the internal normalization and quantifiers of the exact minimax and four-outcome arguments.
- Added and derived a same-sector complex Haar baseline using convexity and a beta integral; checked the formula numerically.
- Executed the supplied finite-dimensional reproduction code.
- Independently compared the five-outcome facet response with probabilities from positive density matrices.
- Constructed an outward interval certificate for all ten facet distances using mpmath.iv 1.3.0.
- Clarified several supplementary assumptions and equality statements, and rebuilt the four documents.

## External sources

This revision does **not** complete an external full-text or novelty audit. The current environment has restricted network access and no web-search connector. The relevant external paper destinations are outside its startup allowlist; no access restriction was bypassed.

Two established references have been added for their general framework:

1. W. Matthews, S. Wehner, and A. Winter, *Distinguishability of quantum states under restricted families of measurements with an application to quantum data hiding*, Commun. Math. Phys. 291, 813–843 (2009).
2. G. Aubrun and C. Lancien, *Zonoids and sparsification of quantum measurements*, Positivity 20, 1–23 (2016).

Their bibliographic details and exact source correspondence were not newly checked against publisher records in this session. The manuscript uses conservative framework-level attribution, not a claim that these works omit every special case considered here. Their detailed relation to the claimed exact restricted-sector optimization remains a priority author check.

## Mathematical inputs requiring primary-source confirmation

- Siegel: the positive-measure zonoid approximation statement for the ambient three-dimensional ball / directions on S2, with exponent 5/4 and its hypotheses. Probability normalization is handled explicitly in the revised proof.
- Bourgain–Lindenstrauss–Milman: the Euclidean-ball zonotope lower bound and the generating-segment convention.
- Bartlett–Mendelson, matrix Bernstein, and Bousquet: verify the precise source formulations against the constants and confidence parameters used in the finite-outcome theorem.
- The original recent imaginarity citations: confirm metadata, publication status, and that each supports the attributed task. No claim of a current complete literature search is made.

## What the computations do not establish

They do not certify literature priority, unrestricted five-outcome optimality, matching accuracy exponents, finite-copy minimax risk, every supplemental structural theorem, or the physical cost of implementing the POVM. Interval certification is conditional on the documented numerical implementation; it is not a formal proof-assistant verification.

## Author and journal matters

Confirm author name, institution, email, funding/conflict statements, ORCID, exclusivity and previous submission history, and the target journal's current policy. The Chinese affiliation was aligned with the supplied English Wuyi University / Jiangmen address, rather than independently authenticated.

ChatGPT assisted with analytic exposition, the added baseline derivation, restructuring, bilingual editing, and verification code. Any disclosure should reflect the actual workflow and target policy. No editorial correspondence, journal submission, new public deposit or DOI registration was performed.
