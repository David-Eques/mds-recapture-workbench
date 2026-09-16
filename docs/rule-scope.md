# Deterministic Rule Scope

The workbench does not perform general semantic clinical interpretation. It recognizes a small,
reviewable set of phrases and structured records, then applies deterministic context rules.

## Structured evidence

- NTA I8000 additions: `K86.1` and `E11.351`, requiring dated coded diagnosis plus active treatment.
- Section I checkboxes: pneumonia, septicemia, CVA/TIA/stroke, paraplegia, quadriplegia, and
  asthma/COPD/chronic lung disease when the primary diagnosis and active-status records qualify.
- K0520C3: an active, in-window texture-modified diet order.
- Selected GG walking and transfer observations with an explicit task and mapped assistance level.

## Text evidence

Text uses sentence/line phrase matching. It rejects target-scoped negation, historical/resolved/prior
status, copied-forward statements, discontinued treatment, uncertainty, stale metadata dates,
conflicting current records, and OCR citations below 0.85 confidence.

Text-derived output is always a `text_review_candidate`. It is never represented as confirmed coding
support and never contributes to counted movement.

## Known misses and risks

Paraphrases outside the lexicon, complex cross-sentence reasoning, ambiguous temporal language,
handwriting, poor scans, and unsupported MDS concepts may be missed. OCR errors can still distort text.
The system mitigates false favorable findings conservatively but does not establish clinical truth.
Qualified human review remains mandatory.
