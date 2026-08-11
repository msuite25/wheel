"""
upload_whl_to_gdrive.py

Download a .whl file from PyPI and upload it to Google Drive using a
Google service-account JSON key.

Usage:
  python scripts/upload_whl_to_gdrive.py --package PACKAGE_NAME [--version VERSION] \
      --service-account KEY.json [--folder-id FOLDER_ID]

Example:
  python scripts/upload_whl_to_gdrive.py --package requests --version 2.31.0 \
      --service-account /path/to/service-account.json --folder-id 0Bxx... 

Requirements:
  pip install requests google-api-python-client google-auth

Notes:
  - The service account JSON must have Drive API access. Use the scope
    https://www.googleapis.com/auth/drive.file for uploading to the service
    account's Drive. If you need to upload to a user's Drive, share a folder
    with the service account email and use that folder's ID as --folder-id.
  - The script picks a wheel (.whl) file for the requested version or latest
    if --version is omitted. If multiple wheel files exist, a pure Python
    wheel will be preferred; otherwise the first wheel for the release is used.
  - New options added: --wheel-filter (prefer filenames containing substring),
    --filename-regex (regex to select filename, overrides --wheel-filter),
    and --windows-amd64 (convenience flag to prefer 'win_amd64' wheels).
"""

import argparse
import io
import sys
import requests
import re
from typing import Optional, Tuple

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
except Exception:
    # Defer import error until runtime so script file can be added without
    # the dependencies being installed in the environment that creates the repo.
    Credentials = None
    build = None
    MediaIoBaseUpload = None

PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_wheel_info(package: str, version: Optional[str] = None,
                   wheel_substring: Optional[str] = None,
                   filename_regex: Optional[str] = None) -> Tuple[str, str]:
    """Return (download_url, filename) for a wheel from PyPI.

    Args:
      package: package name on PyPI
      version: optional version string. If None, use latest.
      wheel_substring: optional substring to prefer in the wheel filename (e.g. 'manylinux', 'win_amd64')
      filename_regex: optional regex to match wheel filename exactly (overrides wheel_substring)

    Raises:
      RuntimeError if no wheel is found or package not found.
    """

    url = PYPI_JSON_URL.format(package=package)
    r = requests.get(url, timeout=15)
    if r.status_code == 404:
        raise RuntimeError(f"Package not found on PyPI: {package}")
    r.raise_for_status()
    data = r.json()

    releases = data.get("releases", {})
    if version:
        if version not in releases:
            raise RuntimeError(f"Version {version} not found for package {package}")
        files = releases[version]
    else:
        # Use "info" -> "version" as latest by PyPI index
        latest = data.get("info", {}).get("version")
        if not latest:
            raise RuntimeError("Could not determine latest version")
        files = releases.get(latest, [])

    # Filter for wheel files
    wheels = [f for f in files if f.get("filename", "").endswith('.whl')]
    if not wheels:
        raise RuntimeError("No wheel (.whl) files found for the requested release")

    # If a filename regex is supplied, prefer matching files
    if filename_regex:
        try:
            pattern = re.compile(filename_regex)
            matched = [f for f in wheels if pattern.search(f.get("filename", ""))]
            if matched:
                wheels = matched
        except re.error as e:
            raise RuntimeError(f"Invalid filename regex: {e}")

    # Otherwise, if a substring filter is supplied, prefer those filenames
    if not filename_regex and wheel_substring:
        matched = [f for f in wheels if wheel_substring in f.get("filename", "")]
        if matched:
            wheels = matched

    # Prefer pure Python wheels (no platform tag) or manylinux? Choose heuristics.
    def score_wheel(f):
        name = f.get("filename", "")
        # pure Python wheel contains "none-any" or "py3-none-any" etc
        if "none-any" in name:
            return 100
        # universal or pure wheels often have 'py3' and 'none-any'
        if "py3" in name and "none-any" in name:
            return 90
        # otherwise prefer wheels with manylinux or platform-specific lower
        return 10

    wheels.sort(key=score_wheel, reverse=True)
    chosen = wheels[0]
    return chosen["url"], chosen["filename"]


def download_url_to_bytes(url: str) -> bytes:
    """Download the URL and return bytes. Uses streaming to avoid large memory spikes."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                buf.write(chunk)
        return buf.getvalue()


def upload_bytes_to_drive(data: bytes, filename: str, service_account_file: str, folder_id: Optional[str] = None) -> str:
    """Upload bytes to Google Drive and return the new file ID.

    Uses a service account JSON keyfile for auth.
    """
    if Credentials is None:
        raise RuntimeError("google-auth and google-api-python-client are required. Install via pip")

    creds = Credentials.from_service_account_file(service_account_file, scopes=DRIVE_SCOPES)
    service = build('drive', 'v3', credentials=creds)

    metadata = {'name': filename}
    if folder_id:
        metadata['parents'] = [folder_id]

    fh = io.BytesIO(data)
    media = MediaIoBaseUpload(fh, mimetype='application/octet-stream', resumable=True)

    request = service.files().create(body=metadata, media_body=media, fields='id')

    response = None
    # Simple resumable upload loop
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload {int(status.progress() * 100)}%...")
    file_id = response.get('id')
    if not file_id:
        raise RuntimeError('Upload failed, no file id returned')
    return file_id


def main(argv=None):
    p = argparse.ArgumentParser(description='Download a wheel from PyPI and upload it to Google Drive')
    p.add_argument('--package', '-p', required=True, help='PyPI package name')
    p.add_argument('--version', '-v', help='Specific version (defaults to latest)')
    p.add_argument('--service-account', '-s', required=True, help='Path to service account JSON key file')
    p.add_argument('--folder-id', '-f', help='Optional Drive folder ID to upload into')
    p.add_argument('--dry-run', action='store_true', help='Show which wheel would be downloaded but do not download/upload')
    p.add_argument('--wheel-filter', help="Prefer wheel filenames containing this substring (e.g. 'manylinux2014', 'win_amd64')")
    p.add_argument('--filename-regex', help='Regex to select the wheel filename (overrides --wheel-filter)')
    p.add_argument('--windows-amd64', action='store_true', help="Convenience flag: prefer 'win_amd64' wheels")

    args = p.parse_args(argv)

    try:
        print(f"Looking up wheel for {args.package} {args.version or '(latest)'} on PyPI...")

        # Determine wheel filter: --filename-regex takes precedence, then explicit --wheel-filter,
        # then convenience --windows-amd64 flag.
        wheel_filter = args.wheel_filter
        if args.windows_amd64 and not wheel_filter:
            wheel_filter = 'win_amd64'

        url, filename = get_wheel_info(args.package, args.version, wheel_filter, args.filename_regex)
        print(f"Found wheel: {filename}\nURL: {url}")

        if args.dry_run:
            print("Dry run enabled, exiting before download/upload")
            return 0

        print("Downloading wheel...")
        data = download_url_to_bytes(url)
        print(f"Downloaded {len(data)} bytes")

        print("Uploading to Google Drive...")
        file_id = upload_bytes_to_drive(data, filename, args.service_account, args.folder_id)
        print(f"Upload complete. Drive file ID: {file_id}")
        print(f"File URL: https://drive.google.com/file/d/{file_id}/view")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
