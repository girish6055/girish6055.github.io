#!/usr/bin/env python3
"""Downloads the YOLO11 weights into models/ so the build can bundle them.

Run before building, or any time you want to refresh the weights:
    python setup_models.py            # yolo11n (fastest, default)
    python setup_models.py yolo11s    # more accurate, slower
"""
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen

RELEASE = "https://github.com/ultralytics/assets/releases/download/v8.3.0"
MODELS_DIR = Path(__file__).resolve().parent / "models"


def download(name: str) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = MODELS_DIR / f"{name}.pt"
    if target.exists() and target.stat().st_size > 1_000_000:
        print(f"[ok] {target} already present ({target.stat().st_size / 1e6:.1f} MB)")
        return target

    url = f"{RELEASE}/{name}.pt"
    print(f"[..] downloading {url}")
    try:
        with urlopen(url, timeout=120) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] direct download failed ({exc}); trying via ultralytics")
        try:
            from ultralytics import YOLO

            model = YOLO(f"{name}.pt")
            source = Path(getattr(model, "ckpt_path", "") or f"{name}.pt")
            if source.exists():
                shutil.copy(source, target)
        except Exception as inner:  # noqa: BLE001
            print(f"[!!] could not obtain {name}.pt: {inner}")
            return target
    if target.exists():
        print(f"[ok] saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return target


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "yolo11n"
    path = download(name)
    if not path.exists():
        print("\nWeights could not be downloaded. On an offline PC, copy the .pt file")
        print(f"into {MODELS_DIR} manually and set engine.model in config/config.json.")
        return 1
    print("\nOptional: place a site-trained PPE model at models/ppe.pt to enable the")
    print("PPE violation analytic (helmet / vest classes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
