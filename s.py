#!/usr/bin/env python3
"""
download_wheels.py

Finds the latest PyPI release for each package that provides a cp314 (Python 3.14) win_amd64 wheel,
downloads the wheel files into the `wheels/` directory, and aborts if any wheel exceeds 100 MB.

Usage: python download_wheels.py
"""

import os
import sys
import requests
from packaging.version import parse as version_parse

PACKAGES = ["numpy", "scipy", "scikit-learn", "matplotlib"]
PY_TAG = "cp314"
PLATFORM_TAG = "win_amd64"
MAX_BYTES = 100 * 1024 * 1024  # 100 MB

WHEELS_DIR = "wheels"

os.makedirs(WHEELS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({"User-Agent": "msuite25-wheel-downloader/1.0"})


def find_wheel(pkg):
    url = f"https://pypi.org/pypi/{pkg}/json"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    releases = data.get("releases", {})
    # sort versions descending
    versions = sorted(releases.keys(), key=lambda v: version_parse(v), reverse=True)
    for ver in versions:
        files = releases.get(ver) or []
        for f in files:
            filename = f.get("filename", "")
            if filename.endswith('.whl') and PY_TAG in filename and PLATFORM_TAG in filename:
                return ver, f.get("url"), f.get("size"), filename
    return None, None, None, None


def download_file(url, dest_path, expected_size=None):
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = 0
        with open(dest_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
    if expected_size and total != expected_size:
        print(f"Warning: downloaded size {total} != expected {expected_size} for {dest_path}")
    return total


def main():
    to_download = []
    for pkg in PACKAGES:
        print(f"Checking {pkg} on PyPI for {PY_TAG} / {PLATFORM_TAG} wheels...")
        ver, url, size, filename = find_wheel(pkg)
        if not url:
            print(f"ERROR: No cp314 win_amd64 wheel found for {pkg}. Aborting.")
            sys.exit(3)
        print(f"Found {pkg} version {ver}: {filename} ({size} bytes)")
        if size and size > MAX_BYTES:
            print(f"ERROR: {filename} exceeds 100 MB ({size} bytes). Will not download per configured policy.")
            sys.exit(4)
        to_download.append((pkg, ver, url, size, filename))

    for pkg, ver, url, size, filename in to_download:
        dest = os.path.join(WHEELS_DIR, filename)
        if os.path.exists(dest):
            print(f"Already have {dest}, skipping download.")
            continue
        print(f"Downloading {filename} ...")
        downloaded = download_file(url, dest, expected_size=size)
        print(f"Saved {dest} ({downloaded} bytes)")

    print("All requested wheels downloaded successfully into the 'wheels/' directory.")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(2)
