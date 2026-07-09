"""Phase 0 · step 00 — verify (and optionally download) the source h5ad.

The per-cytokine mRNA matrix Concord needs lives in a 16.8 GB public S3 object:
    s3://genome-scale-tcell-perturb-seq/marson2025_data/GWCD4i.DE_stats.h5ad
served over plain HTTPS with `Accept-Ranges: bytes`. Because it is range-capable, the
extractor (01_extract_cytokines.py) can slice only the cytokine columns WITHOUT a full
download. This script therefore has two jobs:

  --check   (default) HEAD the object, confirm it is reachable, the size matches, and
            the first bytes are the HDF5 magic signature. Pulls a few hundred bytes.
  --download  fetch the whole 16.8 GB to data/raw/ (only needed if you prefer to slice
            locally; the default remote-range extraction does not require it).

No AWS CLI needed — the bucket is public; stdlib urllib + Range headers suffice.
Run:  python pipeline/00_download.py            # verify only
      python pipeline/00_download.py --download  # full local copy (16.8 GB)
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

import yaml

HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_CONFIG = os.path.join(_APP, "config.yaml")


def _cfg() -> dict:
    with open(_CONFIG) as fh:
        return yaml.safe_load(fh)


def _head(url: str) -> tuple[int, dict]:
    """HEAD the object; return (status, headers-lowercased)."""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}


def _range(url: str, start: int, end: int) -> bytes:
    """Fetch bytes [start, end] inclusive via an HTTP Range request."""
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def check(url: str, expected_bytes: int | None) -> bool:
    """Verify the object is reachable, sized as expected, and is a real HDF5 file.

    Returns True on all checks passing. Prints an actionable line for each check so a
    failure says exactly what broke rather than a stack trace."""
    print(f"[check] HEAD {url}")
    try:
        status, headers = _head(url)
    except Exception as e:  # noqa: BLE001 — a network failure is the answer, not a crash
        print(f"  FAIL  could not reach the object: {e}")
        return False

    ok = True
    length = int(headers.get("content-length", "0"))
    ranges = headers.get("accept-ranges", "none")
    print(f"  status={status}  content-length={length:,}  accept-ranges={ranges}")

    if status != 200:
        print(f"  FAIL  expected HTTP 200, got {status}"); ok = False
    if expected_bytes and length != expected_bytes:
        print(f"  WARN  size {length:,} != config's {expected_bytes:,} "
              "(file may have been re-released — update config.yaml h5ad_bytes)")
    if ranges != "bytes":
        print("  FAIL  server does not advertise byte ranges — remote slicing won't work; "
              "use --download instead"); ok = False

    # Confirm it is genuinely an HDF5 container (first 8 bytes = the magic number).
    try:
        head8 = _range(url, 0, 7)
        if head8 == HDF5_MAGIC:
            print("  OK    first 8 bytes are the HDF5 magic signature")
        else:
            print(f"  FAIL  first bytes {head8!r} are not the HDF5 magic {HDF5_MAGIC!r}")
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  range request rejected: {e}"); ok = False

    print("[check] PASS — the h5ad is reachable and range-sliceable." if ok
          else "[check] FAIL — see above.")
    return ok


def download(url: str, dest: str) -> None:
    """Full 16.8 GB copy to `dest`. Only needed for local (non-remote) slicing."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"[download] {url}\n         -> {dest}  (16.8 GB; this takes a while)")

    def _hook(block, bsize, total):
        done = block * bsize
        pct = (done / total * 100) if total > 0 else 0
        sys.stdout.write(f"\r  {done / 1e9:6.2f} GB / {total / 1e9:.2f} GB ({pct:5.1f}%)")
        sys.stdout.flush()

    urllib.request.urlretrieve(url, dest, reporthook=_hook)
    print("\n[download] done.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify or download the Concord source h5ad.")
    ap.add_argument("--download", action="store_true",
                    help="fetch the full 16.8 GB file (default is verify-only)")
    args = ap.parse_args()

    cfg = _cfg()
    url = cfg["data"]["h5ad_url"]
    expected = cfg["data"].get("h5ad_bytes")

    if not check(url, expected):
        return 1
    if args.download:
        dest = os.path.join(_APP, "data", "raw", "GWCD4i.DE_stats.h5ad")
        download(url, dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
