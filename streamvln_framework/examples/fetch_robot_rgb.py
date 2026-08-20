#!/usr/bin/env python3
"""Fetch and save exactly one RGB JPEG from the Robot example service."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Optional, Sequence

import requests
from PIL import Image


DEFAULT_SERVER_URL = "http://192.168.1.50:5803"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--output", type=Path, default=Path("robot_rgb.jpg"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def fetch_rgb(server_url: str, timeout: float) -> bytes:
    response = requests.get(f"{server_url.rstrip('/')}/rgb", timeout=timeout)
    response.raise_for_status()
    if response.headers.get("Content-Type", "").split(";", 1)[0] != "image/jpeg":
        raise RuntimeError(
            f"Robot returned unexpected Content-Type: "
            f"{response.headers.get('Content-Type')!r}"
        )
    image_bytes = response.content
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.verify()
    return image_bytes


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.output.exists() and not args.overwrite:
        print(
            f"output already exists: {args.output}; use --overwrite to replace it",
            file=sys.stderr,
        )
        return 2
    try:
        image_bytes = fetch_rgb(args.server_url, args.timeout)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image_bytes)
    except (OSError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"StreamVLN RGB request failed: {exc}", file=sys.stderr)
        return 1
    print(f"saved one Robot RGB frame to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
