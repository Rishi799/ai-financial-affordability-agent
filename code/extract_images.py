"""
Image amount-extraction workflow (Phase 2).

Finds every financial event whose `amount` is blank and which is linked to an
image (via images.csv), then extracts the amount from each payslip/bill/receipt
and writes it to code/amounts_cache.json. The pipeline (image_extraction.py)
reads that cache at run time, so the main run stays reproducible and offline.

Backends:
  * Claude VLM, when ANTHROPIC_API_KEY + the `anthropic` SDK are available.
  * Otherwise this prints the exact list of images that need a value, so they
    can be filled in (e.g. reviewed with a vision model) and cached.

Usage:  python code/extract_images.py [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os

import config
from data_loader import DataStore
from image_extraction import AmountResolver


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    store = DataStore()
    resolver = AmountResolver(store, verbose=args.verbose)

    # Events that need an image-derived amount.
    todo = []
    for ev in store.events:
        if ev.amount is not None:
            continue
        imgs = store.images_by_event.get(ev.event_id, [])
        if imgs:
            todo.append((ev, imgs))

    print(f"{len(todo)} blank-amount events are linked to images.")
    if not todo:
        return 0

    resolver.resolve_all()  # attempts VLM/cache for each
    resolver.save_cache()

    resolved = {k: v for k, v in resolver.cache.items()}
    missing = []
    for ev, imgs in todo:
        val = resolved.get(ev.event_id)
        if val is None:
            val = next((resolved.get(im.image_id) for im in imgs
                        if resolved.get(im.image_id) is not None), None)
        if val is None:
            missing.append((ev, imgs))
        elif args.verbose:
            print(f"  {ev.event_id} ({ev.category}) -> {val}")

    print(f"Resolved: {len(todo) - len(missing)} | Missing: {len(missing)}")
    if missing:
        print("\nImages still needing a value (fill code/amounts_cache.json "
              "keyed by event_id or image_id):")
        for ev, imgs in missing:
            ids = ",".join(im.image_id for im in imgs)
            files = ",".join(resolver._image_path(im) or f"<{im.image_id}:not found>"
                             for im in imgs)
            print(f"  event={ev.event_id} images=[{ids}] files=[{files}]")
        print(f"\nMedia dir: {config.MEDIA_DIR}")
        print(f"Cache file: {config.AMOUNTS_CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
