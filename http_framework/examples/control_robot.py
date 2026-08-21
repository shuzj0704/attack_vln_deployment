#!/usr/bin/env python3
"""Send one primitive to the Robot HTTP service."""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Optional, Sequence

import requests

from http_framework.protocol import VALID_ACTIONS


DEFAULT_SERVER_URL = "http://192.168.1.50:5802"


ACTION_EFFECTS = {
    "forward": "move forward approximately 0.25 m",
    "left": "turn left approximately 15 degrees",
    "right": "turn right approximately 15 degrees",
    "stop": "issue StopMove",
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=VALID_ACTIONS)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the target and action without sending it",
    )
    return parser.parse_args(argv)


def _print_safety_notice(action: str, server_url: str) -> None:
    print(f"Target: {server_url}")
    print(f"Effect: {ACTION_EFFECTS[action]}")
    print("Keep the remote emergency stop ready and send only one action at a time.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    server_url = args.server_url.rstrip("/")
    print(f"target={server_url} action={args.action}")
    if args.dry_run:
        return 0
    if args.action != "stop":
        _print_safety_notice(args.action, server_url)
    request_id = str(uuid.uuid4())
    try:
        response = requests.post(
            f"{server_url}/action",
            json={"action": args.action, "request_id": request_id},
            timeout=args.timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Robot HTTP action failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(f"invalid Robot response: {payload}", file=sys.stderr)
        return 1
    if payload.get("action") != args.action or payload.get("stopped") is not True:
        print(f"invalid Robot response: {payload}", file=sys.stderr)
        return 1
    print(
        f"completed action={payload['action']} stopped={payload['stopped']} "
        f"request_id={payload.get('request_id')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
