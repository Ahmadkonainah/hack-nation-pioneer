#!/usr/bin/env python3
"""
Build the demo page: web/index.html (a single self-contained file, open it by double-click or host it on GitHub Pages)
and web/artifact.html (the same page without the <html> wrapper, for sites that add their own).
docs/index.html is a copy of index.html for GitHub Pages and Vercel.

Everything is inlined (the engine, the data, the fonts) so the page is one file with no other requests. It opens
from disk, works offline, cannot break because a sibling file is missing, and makes no call to any outside site.

    python src\\build_site.py
Needs web/app.template.html, web/engine.js and web/data.js (python src\\build_web.py writes data.js).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

WEB = ROOT / "web"


# Fonts are embedded in the page so a visitor's browser makes no request to any third party (no Google Fonts).
# They go in as base64 data URLs: a font file next to the page would be a second request, and would fail the day the
# single HTML file is moved. The cost is about a third more bytes. Only Latin subsets are used, to keep the page small.
# Each tuple is (family, weight, style, file in web/fonts/).
FONT_FILES = [
    ("Schibsted Grotesk", "400 700", "normal", "schibsted-grotesk-latin-wght-normal.woff2"),
    ("Source Serif 4", "400", "normal", "source-serif-4-latin-400-normal.woff2"),
    ("Source Serif 4", "600", "normal", "source-serif-4-latin-600-normal.woff2"),
    ("Source Serif 4", "400", "italic", "source-serif-4-latin-400-italic.woff2"),
    ("IBM Plex Mono", "400", "normal", "ibm-plex-mono-latin-400-normal.woff2"),
    ("IBM Plex Mono", "500", "normal", "ibm-plex-mono-latin-500-normal.woff2"),
]
# One strict policy for the stand-alone page: nothing is loaded from, or sent to, any other site.
#   default-src 'none'          block everything that is not allowed below
#   script-src / style-src      'unsafe-inline' only, because the page inlines all its code and styles (no files to name)
#   font-src data: / img-src data:   embedded data URLs only, never a remote font or image
#   connect-src 'none'          no fetch, XHR or WebSocket, so a fact a person types can never be sent anywhere
#   form-action 'none'          no form can post data out
#   base-uri 'none'             a <base> tag cannot redirect relative links
# 'unsafe-inline' means this policy does not stop an injected inline script, so safety also depends on the app
# escaping every string from the data. docs/vercel.json sends the same policy as a header; keep the two in sync.
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; img-src data:; "
       "connect-src 'none'; form-action 'none'; base-uri 'none'")


# Small things a finished page has: a description for search results and link previews, the browser-bar colour on phones,
# and a favicon. The favicon is a data URL (the CSP allows img-src data: only), so it costs no extra request.
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 28 28'%3E%3Crect x='3' y='3' width='22' height='6' rx='2' fill='%230B6A78'/%3E"
           "%3Crect x='3' y='11' width='22' height='6' rx='2' fill='%230B6A78' opacity='.62'/%3E%3Crect x='3' y='19' width='22' height='6' rx='2' fill='%230B6A78' opacity='.32'/%3E%3C/svg%3E")
HEAD_EXTRA = ('<meta name="description" content="Which rental housing laws apply at an address on a given date? Stackwise stacks state and city rules, shows the law behind every answer and says unknown when the data cannot tell. Not legal advice.">\n'
              '<meta name="theme-color" content="#0B6A78">\n'
              f'<link rel="icon" href="{FAVICON}">\n')


def font_css() -> str:
    """A <style> block with every font as a base64 data URL, or "" if no font file was found.
    A missing file is skipped with a message and the page falls back to the system font for that family."""
    d = WEB / "fonts"
    out = []
    for fam, weight, style, fn in FONT_FILES:
        p = d / fn
        if not p.exists():
            print(f"web/fonts/{fn} not found: the page will use the system font for {fam}.")
            continue
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        out.append(f'@font-face{{font-family:"{fam}";font-style:{style};font-weight:{weight};font-display:swap;src:url(data:font/woff2;base64,{b64}) format("woff2")}}')
    return "<style>\n" + "\n".join(out) + "\n</style>" if out else ""


def build() -> list[Path]:
    """Inline engine.js, data.js and the fonts into app.template.html and write the page files.
    Returns [index.html, artifact.html], or [] when the template is missing. Exits with a message if engine.js or
    data.js is missing or the template lacks a marker, so a broken page is never written."""
    tpl = WEB / "app.template.html"
    if not tpl.exists():
        print("web/app.template.html not found: demo page not built.")
        return []
    html = tpl.read_text(encoding="utf-8")
    for name in ("engine.js", "data.js"):
        p = WEB / name
        if not p.exists():
            sys.exit(f"web/{name} not found. Run  python src\\build_web.py  first.")
        # "</" inside an inline script could close it early (a rule's text might contain "</script>"). "<\/" means
        # the same thing inside a JavaScript string.
        js = p.read_text(encoding="utf-8").replace("</", "<\\/")
        # The template marks where each file goes. A missing marker is a hard error: the page would ship without its engine or data.
        marker = f"<!--INLINE:{name}-->"
        if marker not in html:
            sys.exit(f"{marker} is missing from web/app.template.html")
        html = html.replace(marker, f"<script>\n{js}\n</script>")
    html = html.replace("<!--INLINE:fonts-->", font_css())
    frag = WEB / "artifact.html"
    frag.write_text(html, encoding="utf-8")
    # The CSP is a <meta> tag so it also holds when the file is opened from disk, where no HTTP headers exist.
    # no-referrer: a click on a source link does not tell that site where the visitor came from.
    full = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n<meta name="referrer" content="no-referrer">\n'
            f'{HEAD_EXTRA}</head>\n<body>\n'
            + html + "\n</body>\n</html>\n")
    idx = WEB / "index.html"
    idx.write_text(full, encoding="utf-8")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(full, encoding="utf-8")       # GitHub Pages can serve the /docs folder
    (docs / ".nojekyll").write_text("", encoding="utf-8")         # tells GitHub Pages to serve the files as they are
    print(f"Wrote web/index.html and docs/index.html  ({idx.stat().st_size / 1024:.0f} KB, one file, no other files needed)")
    return [idx, frag]


def main() -> int:
    """CLI entry point: build the page. Returns 0."""
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
