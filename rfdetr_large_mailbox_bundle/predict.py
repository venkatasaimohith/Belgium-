"""Run the packaged RF-DETR Large mailbox model on an image or folder."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
from rfdetr import RFDETRLarge


HERE = Path(__file__).resolve().parent
DEFAULT_CHECKPOINT = HERE / "weights" / "last_ema.pth"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Image file or directory")
    parser.add_argument("--output-dir", default="outputs_rfdetr_large_mailbox")
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--conf", type=float, default=0.7)
    parser.add_argument("--split-output", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    checkpoint = Path(args.checkpoint)
    output_dir = Path(args.output_dir)
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")
    if not checkpoint.is_file():
        raise SystemExit(f"Checkpoint not found: {checkpoint}")

    images = (
        sorted(path for path in source.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS)
        if source.is_dir()
        else [source]
    )
    detected_dir = output_dir / "detected" if args.split_output else output_dir
    empty_dir = output_dir / "non_detected" if args.split_output else output_dir
    detected_dir.mkdir(parents=True, exist_ok=True)
    empty_dir.mkdir(parents=True, exist_ok=True)

    print(f"[model]  RF-DETR Large mailbox")
    print(f"[weight] {checkpoint.resolve()}")
    print(f"[input]  {len(images)} image(s)")
    model = RFDETRLarge(pretrain_weights=str(checkpoint))
    class_names = list(getattr(model.model, "class_names", None) or ["mailbox"])

    total = 0
    detected_images = 0
    for image_path in images:
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"[skip] {image_path}")
            continue

        detections = model.predict(image, threshold=args.conf)
        count = len(detections.xyxy) if detections.xyxy is not None else 0
        for index in range(count):
            x1, y1, x2, y2 = map(int, detections.xyxy[index])
            score = float(detections.confidence[index])
            class_id = int(detections.class_id[index])
            label = class_names[class_id] if class_id < len(class_names) else "mailbox"
            cv2.rectangle(image, (x1, y1), (x2, y2), (255, 100, 0), 2)
            cv2.putText(
                image,
                f"{label} {score:.2f}",
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 100, 0),
                2,
            )

        total += count
        detected_images += int(count > 0)
        destination = (detected_dir if count else empty_dir) / image_path.name
        cv2.imwrite(str(destination), image)
        print(f"{image_path.name}: {count} detection(s)")

    print(f"[done] {total} detections in {detected_images}/{len(images)} images")
    print(f"[saved] {output_dir.resolve()}")


if __name__ == "__main__":
    main()
