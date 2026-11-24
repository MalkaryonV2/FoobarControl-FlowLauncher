"""Build the release zip: python scripts/build_zip.py  ->  dist/FoobarControl-<version>.zip

The zip holds only what Flow Launcher needs, with plugin.json at its root.
Runtime files (config.json, index_cache.json, error.log...) are never included.
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["plugin.json", "main.py", "icon.png", "icons", "lib", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"]
SKIP_NAMES = {"__pycache__", "config.json", "index_cache.json", "now_playing_cache.json", "queue_state.json", "playlist_debug.txt", "index.lock", "error.log"}


def files():
    for item in INCLUDE:
        p = ROOT / item
        if p.is_file():
            yield p
        else:
            for f in sorted(p.rglob("*")):
                if f.is_file() and not (SKIP_NAMES & set(f.relative_to(ROOT).parts)) and f.suffix != ".pyc":
                    yield f


def main():
    version = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["Version"]
    out = ROOT / "dist" / ("FoobarControl-%s.zip" % version)
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            z.write(f, f.relative_to(ROOT).as_posix())
    print(out)


if __name__ == "__main__":
    sys.exit(main())
