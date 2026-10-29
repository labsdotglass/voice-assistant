"""Post-build: fold each page's scripts and stylesheets into one self-contained file:
dist/index.html into dist/orb.html, dist/tour.html into dist/onboarding.html.

WebKit won't load module scripts from file:// URLs, so the helper loads this single file
as a string (with an https://localhost base, which also keeps WebGPU's secure context).
"""
import re
from pathlib import Path

dist = Path(__file__).parent / "dist"


def script(m):
    js = (dist / m.group(1)).read_text().replace("</script", "<\\/script")
    return f'<script type="module">{js}</script>'


def style(m):
    return f"<style>{(dist / m.group(1)).read_text()}</style>"


# Only a page just built is folded in, and its build file is removed after: a later build of one page
# (PAGE=tour) must never rewrite the other from an old build (that once put the orb's drag handle back
# at the bottom).
for page, out in [("index.html", "orb.html"), ("tour.html", "onboarding.html")]:
    if not (dist / page).exists():
        print(f"skipped {out}: {page} wasn't just built")
        continue
    html = (dist / page).read_text()
    html = re.sub(r'<script type="module" crossorigin src="\./([^"]+)"></script>', script, html)
    html = re.sub(r'<link rel="stylesheet" crossorigin href="\./([^"]+)">', style, html)
    assert "src=\"./assets" not in html and "href=\"./assets" not in html, f"unexpected asset reference left in {page}"
    (dist / out).write_text(html)
    (dist / page).unlink()
    print(f"wrote {dist / out} ({len(html) // 1024} KB)")
