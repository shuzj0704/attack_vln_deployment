#!/usr/bin/env python3
"""Display D435i RGB frames from the TCP fallback video service."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from tcp_framework.config import ROBOT_IP, VIDEO_PORT
from tcp_framework.host.video_client import start_video_client


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-host", default=ROBOT_IP)
    parser.add_argument("--port", type=int, default=VIDEO_PORT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    start_video_client(robot_ip=args.robot_host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
