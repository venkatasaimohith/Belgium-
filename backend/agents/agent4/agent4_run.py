#!/usr/bin/env python3
"""
Agent 4 — Detect + Assign (orchestrator)
========================================
Runs the two stages in the order you asked for:

  1) Image Enhancement + frontage column ranges                  (agent4_assign.py)
  2) Detect  — keep every mailbox returned by RF-DETR          (agent4_detect.py)

Debug either stage alone with its own script; use this runner for the full Agent 4.

Usage
-----
    python agent4_run.py \\
        --images-json ../output/agent3_images.json \\
        --site-json ../output/agent2_site.json \\
        --out-dir ../output \\
        --demo-boxes --skip-plausibility
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

try:
    from . import agent4_assign, agent4_detect
except ImportError:
    import agent4_assign
    import agent4_detect


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Agent 4 - Image Enhancement then Detect.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--images-json", required=True, help="agent3_images.json")
    p.add_argument("--site-json", required=True, help="agent2_site.json")
    p.add_argument("--out-dir", "-o", default=".")
    p.add_argument("--buffer-m", type=float, default=1.0)
    p.add_argument("--enhancement-mode", choices=("auto", "enhance", "ai"), default="ai")
    p.add_argument("--enhancement-scale", type=float, default=2.0)
    p.add_argument("--enhancement-weights", type=Path, default=agent4_assign.DEFAULT_WEIGHTS)
    p.add_argument("--enhancement-device", choices=("auto", "cpu", "cuda"), default="auto")
    p.add_argument("--enhancement-workers", type=int, default=10)
    p.add_argument("--detections-json", default=None)
    p.add_argument("--demo-boxes", action="store_true")
    p.add_argument("--score-threshold", type=float, default=0.70)
    p.add_argument("--cam-h", type=float, default=2.5)
    p.add_argument("--bank-gap-m", type=float, default=1.5)
    p.add_argument("--skip-plausibility", action="store_true")
    p.add_argument("--max-sites", type=int, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir).resolve()

    print("\n>>> Stage 1/2 — Image Enhancement\n")
    agent4_assign.run(
        images_json=Path(args.images_json).resolve(),
        site_json=Path(args.site_json).resolve(),
        out_dir=out_dir,
        buffer_m=args.buffer_m,
        enhancement_mode=args.enhancement_mode,
        enhancement_scale=args.enhancement_scale,
        enhancement_weights=args.enhancement_weights,
        enhancement_device=args.enhancement_device,
        enhancement_workers=max(1, args.enhancement_workers),
        max_sites=args.max_sites,
    )

    print("\n>>> Stage 2/2 — Detect (target parcel)\n")
    agent4_detect.run(
        assign_json=out_dir / "agent4_assign.json",
        out_dir=out_dir,
        detections_json=(
            Path(args.detections_json).resolve() if args.detections_json else None
        ),
        demo_boxes=args.demo_boxes,
        score_threshold=args.score_threshold,
        buffer_m=args.buffer_m,
        cam_h=args.cam_h,
        bank_gap_m=args.bank_gap_m,
        skip_plausibility=args.skip_plausibility,
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
