#!/usr/bin/env python3
"""Run stop/forward/left/right in sequence to verify StreamVLN action execution."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Optional, Sequence

import requests

DEFAULT_SERVER_URL = "http://192.168.1.50:5803"


def send_action(server_url: str, action: str, timeout: float) -> None:
    response = requests.post(
        f"{server_url}/action",
        json={"action": action, "request_id": str(uuid.uuid4())},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("action") != action
        or payload.get("stopped") is not True
    ):
        raise RuntimeError(f"invalid Robot response: {payload}")
    print(
        f"  completed action={payload['action']} stopped={payload['stopped']} "
        f"request_id={payload.get('request_id')}"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument(
        "--wait",
        type=float,
        default=5.0,
        help="seconds to wait between actions",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip interactive safety confirmation",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    server_url = args.server_url.rstrip("/")

    print("Target:", server_url)
    print("Action sequence: stop -> forward -> left -> right")
    print("Each action moves approximately: forward=0.25m, left/right=15deg")

    if not args.yes:
        print(
            "\nSafety prerequisite: robot is stable, area is clear, "
            "remote E-stop is ready."
        )
        print("Run with --yes to proceed.")
        return 0

    actions = ["stop", "forward", "left", "right"]
    for action in actions:
        print(f"\nSending {action} ...")
        try:
            send_action(server_url, action, args.timeout)
        except Exception as exc:
            print(f"FAILED {action}: {exc}", file=sys.stderr)
            return 1
        if action != actions[-1]:
            print(f"Waiting {args.wait}s ...")
            time.sleep(args.wait)

    print("\nAll actions completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
