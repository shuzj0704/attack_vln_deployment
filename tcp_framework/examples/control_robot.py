#!/usr/bin/env python3
"""Send one primitive through the TCP fallback service."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from tcp_framework.config import CMD_PORT, ROBOT_IP, VALID_COMMANDS
from tcp_framework.host.cmd_client import send_command


ACTION_EFFECTS = {
    "forward": "move forward approximately 0.25 m",
    "left": "turn left approximately 15 degrees",
    "right": "turn right approximately 15 degrees",
    "stop": "issue StopMove",
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=VALID_COMMANDS)
    parser.add_argument("--robot-host", default=ROBOT_IP)
    parser.add_argument("--port", type=int, default=CMD_PORT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _print_safety_notice(action: str, robot_host: str, port: int) -> None:
    print(f"Target: {robot_host}:{port}")
    print(f"Effect: {ACTION_EFFECTS[action]}")
    print("Keep the remote emergency stop ready and send one action at a time.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    print(f"target={args.robot_host}:{args.port} action={args.action}")
    if args.dry_run:
        return 0
    if args.action != "stop":
        _print_safety_notice(args.action, args.robot_host, args.port)
    return 0 if send_command(args.action, args.robot_host, args.port) else 1


if __name__ == "__main__":
    raise SystemExit(main())
