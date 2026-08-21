#!/usr/bin/env python3
"""Display D435i RGB frames fetched from the Robot HTTP service."""

from __future__ import annotations

import argparse
import time
from typing import Optional, Sequence

import cv2
import numpy as np
import requests


DEFAULT_SERVER_URL = "http://192.168.1.50:5802"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def fetch_rgb_frame(
    session: requests.Session,
    server_url: str,
    timeout: float,
):
    response = session.get(f"{server_url}/rgb", timeout=timeout)
    response.raise_for_status()
    image = cv2.imdecode(
        np.frombuffer(response.content, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if image is None:
        raise RuntimeError("Robot returned an invalid JPEG image")
    return image


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    server_url = args.server_url.rstrip("/")
    session = requests.Session()
    frame_count = 0
    start_time = time.monotonic()
    try:
        health = session.get(f"{server_url}/health", timeout=args.timeout)
        health.raise_for_status()
        health_payload = health.json()
        if not isinstance(health_payload, dict) or health_payload.get("status") != "ready":
            raise RuntimeError(f"Robot service is not ready: {health_payload}")
        while True:
            image = fetch_rgb_frame(session, server_url, args.timeout)
            frame_count += 1
            elapsed = time.monotonic() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0.0
            cv2.putText(
                image,
                f"FPS: {fps:.1f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
            )
            cv2.imshow("Go2-W D435i HTTP", image)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                return 0
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"HTTP RGB request failed: {exc}")
        return 1
    finally:
        session.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
