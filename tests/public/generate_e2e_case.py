"""Generate independent browser-test inputs outside the repository."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def assessment() -> dict:
    return {
        "stay_id": "fresh-browser-case",
        "assessment_type": "01",
        "ard": "2026-05-22",
        "a2400b": "2026-05-17",
        "a2400c": "2026-06-16",
        "baseline": {"source": "claim_paid", "hipps": "KAXE1", "reconciled": True},
        "items": {
            "I0020B": "I50.22",
            "I2900": "1",
            "C0500": "14",
            "K0100A": "0",
            "K0100B": "0",
            "K0100C": "0",
            "K0100D": "0",
            "K0520C3": "0",
            "O0110E1B": "0",
            "O0110F1B": "0",
            "GG0130A1": "04",
            "GG0130B1": "04",
            "GG0130C1": "03",
            "GG0170B1": "03",
            "GG0170C1": "03",
            "GG0170D1": "03",
            "GG0170E1": "03",
            "GG0170F1": "03",
            "GG0170I1": "03",
            "GG0170J1": "03",
            "GG0170K1": "03",
        },
    }


def main(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "assessment.json").write_text(json.dumps(assessment()), encoding="utf-8")
    signals = {
        "signals": [
            {
                "signal_type": "diet_order",
                "trust": "order_active",
                "value": "Diet order: mechanical soft",
                "effective_date": "2026-05-20",
                "source_table": "generated_e2e:diet_order:1",
            }
        ]
    }
    (output_dir / "signals.json").write_text(json.dumps(signals), encoding="utf-8")

    image = Image.new("RGB", (2400, 600), "white")
    font = ImageFont.load_default(size=72)
    ImageDraw.Draw(image).text((90, 200), "Diet order: mechanical soft.", fill="black", font=font)
    image.save(output_dir / "unique-scanned-note.pdf", format="PDF", resolution=200)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
