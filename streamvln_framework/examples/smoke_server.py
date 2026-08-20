#!/usr/bin/env python3
"""Safe /eval_vln server that always returns stop without loading StreamVLN."""

import argparse

from streamvln_framework.host.server import create_app


class StopBackend:
    def reset(self) -> None:
        pass

    def step(self, _image_bgr, _instruction):
        return (0,)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5801)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    create_app(StopBackend(), instruction="safe communication smoke test").run(
        host=args.host,
        port=args.port,
        threaded=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
