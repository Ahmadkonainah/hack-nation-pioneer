#!/usr/bin/env python3
"""
Build the demo page: web/index.html (a single self-contained file, open it by double-click or host it on GitHub Pages)
and web/artifact.html (the same page without the <html> wrapper, for sites that add their own).

    python src\\build_site.py
Needs web/app.template.html, web/engine.js and web/data.js (python src\\build_web.py writes data.js).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as E  # noqa: E402

WEB = E.ROOT / "web"


def build() -> list[Path]:
    tpl = WEB / "app.template.html"
    if not tpl.exists():
        print("web/app.template.html not found: demo page not built.")
        return []
    html = tpl.read_text(encoding="utf-8")
    for name in ("engine.js", "data.js"):
        p = WEB / name
        if not p.exists():
            sys.exit(f"web/{name} not found. Run  python src\\build_web.py  first.")
        js = p.read_text(encoding="utf-8").replace("</", "<\\/")
        marker = f"<!--INLINE:{name}-->"
        if marker not in html:
            sys.exit(f"{marker} is missing from web/app.template.html")
        html = html.replace(marker, f"<script>\n{js}\n</script>")
    frag = WEB / "artifact.html"
    frag.write_text(html, encoding="utf-8")
    full = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
            + html + "\n</body>\n</html>\n")
    idx = WEB / "index.html"
    idx.write_text(full, encoding="utf-8")
    docs = E.ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(full, encoding="utf-8")       # GitHub Pages can serve the /docs folder
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote web/index.html and docs/index.html  ({idx.stat().st_size / 1024:.0f} KB, one file, no other files needed)")
    return [idx, frag]


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
