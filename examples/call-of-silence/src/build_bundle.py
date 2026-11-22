"""Package sanitized fingerstyle guitar TAB artifacts into a portable .zip bundle."""

from __future__ import annotations

import json
import re
import zipfile
import zlib
from pathlib import Path
from typing import List, Tuple

from build_sync_manifest import build_manifest

PROJECT_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_DIR / "sync_manifest.json"
MP3_PATH = PROJECT_DIR / "Call_of_Silence_指弹吉他试听.mp3"
PDF_PATH = PROJECT_DIR / "Call_of_Silence_指弹吉他六线谱.pdf"
PREVIEWS_DIR = PROJECT_DIR / "previews"
BUNDLE_PATH = PROJECT_DIR / "Call_of_Silence_bundle.zip"


def _page_sort_key(path: Path) -> Tuple[int, str]:
    m = re.search(r"(\d+)", path.stem)
    return (int(m.group(1)) if m else 0, path.name)


def assert_pdf_sanitized(pdf_path: Path) -> None:
    pdf_bytes = pdf_path.read_bytes()
    leak_pat = re.compile(
        b"(textedit://|" + b"/" + b"home/|" + b"/" + b"Users/|C:\\\\" + b"Users\\\\)"
    )
    leaks = leak_pat.findall(pdf_bytes)
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        try:
            leaks.extend(leak_pat.findall(zlib.decompress(m.group(1))))
        except zlib.error:
            pass
    if leaks:
        raise AssertionError(
            f"Refusing to package unsanitized PDF ({len(leaks)} path leak hits): {leaks[:3]}"
        )


def build_bundle() -> Path:
    if not MANIFEST_PATH.is_file():
        build_manifest()

    for req in (MANIFEST_PATH, MP3_PATH, PDF_PATH):
        if not req.is_file():
            raise FileNotFoundError(f"Missing required artifact: {req}")

    png_files: List[Path] = sorted(PREVIEWS_DIR.glob("page-*.png"), key=_page_sort_key)
    if not png_files:
        raise FileNotFoundError(f"No previews/page-*.png found in {PREVIEWS_DIR}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not manifest.get("systems") or not manifest.get("bars"):
        raise ValueError(f"Invalid manifest structure in {MANIFEST_PATH}")

    assert_pdf_sanitized(PDF_PATH)

    score_src = PROJECT_DIR / "src" / "score_data.py"
    if score_src.is_file():
        leak_pat = re.compile(
            b"(textedit://|" + b"/" + b"home/|" + b"/" + b"Users/|C:\\\\" + b"Users\\\\)"
        )
        src_leaks = leak_pat.findall(score_src.read_bytes())
        if src_leaks:
            raise AssertionError(f"Refusing to package unsanitized {score_src.name}: {src_leaks[:3]}")

    with zipfile.ZipFile(BUNDLE_PATH, "w") as zf:
        zf.write(MANIFEST_PATH, arcname="sync_manifest.json", compress_type=zipfile.ZIP_DEFLATED)
        zf.write(MP3_PATH, arcname=MP3_PATH.name, compress_type=zipfile.ZIP_STORED)
        zf.write(PDF_PATH, arcname=PDF_PATH.name, compress_type=zipfile.ZIP_STORED)
        for png in png_files:
            zf.write(png, arcname=f"previews/{png.name}", compress_type=zipfile.ZIP_STORED)
        if score_src.is_file():
            zf.write(score_src, arcname="src/score_data.py", compress_type=zipfile.ZIP_DEFLATED)

    with zipfile.ZipFile(BUNDLE_PATH, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP integrity test failed on member: {bad}")
        names = zf.namelist()

    print(
        f"Generated bundle: {BUNDLE_PATH.name} ({len(names)} entries, "
        f"{BUNDLE_PATH.stat().st_size:,} bytes): {names}"
    )
    return BUNDLE_PATH


if __name__ == "__main__":
    build_bundle()
