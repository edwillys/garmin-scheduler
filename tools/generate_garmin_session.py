#!/usr/bin/env python3
"""Helper script to generate the GARMIN_SESSION base64 string.

After logging in manually with garth (e.g. `python -c "import garth; garth.login('user','pass'); garth.save('.garth')"`)
run this script to produce the base64-encoded value to store as the
GARMIN_SESSION GitHub Actions secret.

Usage:
    python tools/generate_garmin_session.py [--garth-dir .garth]
"""

import argparse
import base64
import io
import os
import tarfile


def generate_session(garth_dir: str) -> str:
    """Pack *garth_dir* into a tar archive and return its base64 encoding."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for entry in os.listdir(garth_dir):
            full_path = os.path.join(garth_dir, entry)
            tar.add(full_path, arcname=entry)
    return base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--garth-dir",
        default=".garth",
        help="Path to the .garth directory (default: .garth)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.garth_dir):
        raise SystemExit(
            f"Error: directory '{args.garth_dir}' not found.\n"
            "Run `python -c \"import garth; garth.login('EMAIL','PASS'); garth.save('.garth')\"` first."
        )

    session_b64 = generate_session(args.garth_dir)
    print("Copy the following value and store it as the GARMIN_SESSION secret:\n")
    print(session_b64)


if __name__ == "__main__":
    main()
