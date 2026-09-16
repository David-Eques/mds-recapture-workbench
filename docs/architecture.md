# Architecture

The workbench has two independent evidence paths that converge at the counterfactual boundary.

```text
MDS XML/JSON ───────────────────────────────┐
                                            ├─ counterfactual MDS ── CMS grouper ── review response
Signals JSON/CSV ── structured rules ──────┤
                                            │
Documents ── Poppler/Tesseract ── text rules┘
```

The MDS assessment supplies the as-coded state and assessment anchors. Structured signals are parsed
directly and evaluated with deterministic date/trust rules. Documents are decoded or OCRed locally;
only exact source substrings survive citation verification, and OCR citations require at least 0.85
confidence. Both paths propose item changes but never compute money.

The counterfactual builder applies only accepted changes. The CMS Java grouper owns PDPM
classification; the pricing module maps returned HIPPS components to pinned FY2026 Scenario-A rates.
Text results remain uncounted regardless of the resulting HIPPS movement.

The API is stateless and exposes only `/`, `/healthz`, `/readyz`, `/api/capabilities`, and
`/api/analyze`. Readiness verifies local tools and the checksum-pinned CMS runtime.
