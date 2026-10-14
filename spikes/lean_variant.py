#!/usr/bin/env python3
"""Spike A, variant V2: give the Quickstart section lean templates inside the Qrypt docs fork.

Their pages keep the theme's header/footer. Pages in content/quickstart/ get:
  - all theme CSS inlined into one <style> (no render-blocking requests), font-display: swap
  - no jQuery, Font Awesome, highlight.js, featherlight, perfect-scrollbar, modernizr or Mermaid
  - a preloaded body font, inline SVG for the menu toggle, accessible names on icon-only links
  - Hugo's build-time Chroma highlighting with a light, higher-contrast style

Usage: spikes/lean_variant.py PATH_TO_FORK
"""
import pathlib
import re
import sys

fork = pathlib.Path(sys.argv[1])
theme = fork / "themes/hugo-theme-learn/layouts"
site = fork / "layouts"

CSS_FILES = [
    "themes/hugo-theme-learn/static/css/nucleus.css",
    "themes/hugo-theme-learn/static/css/tags.css",
    "themes/hugo-theme-learn/static/css/theme.css",
    "themes/hugo-theme-learn/static/css/hugo-theme.css",
    "static/css/theme-mine.css",
]
HAMBURGER = ('<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path fill="currentColor" '
             'd="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z"/></svg>')

lean_head = (
    '<link rel="preload" href="{{ "fonts/ibm-plex-sans-v14-latin-regular.woff2" | relURL }}" '
    'as="font" type="font/woff2" crossorigin>\n'
    '    {{ $css := "" }}\n'
    + "".join(f'    {{{{ $css = print $css (readFile "{f}") }}}}\n' for f in CSS_FILES)
    + '    {{ $css = replace $css "@import \\"tags.css\\";" "" }}\n'
    '    {{ $css = replace $css "@font-face {" "@font-face { font-display: swap;" }}\n'
    '    <style>{{ $css | safeCSS }}</style>\n'
)

header = (site / "partials/header.html").read_text()
header = re.sub(r'\s*\{\{\s*\$assetBusting.*?<script src="\{\{"js/jquery-3\.3\.1\.min\.js".*?</script>',
                "\n    " + lean_head, header, flags=re.S)
header = header.replace('<a href="#" id="sidebar-toggle" data-sidebar-toggle="">',
                        '<a href="#" id="sidebar-toggle" data-sidebar-toggle="" aria-label="Toggle navigation">')
header = header.replace('<i class="fas fa-bars"></i>', HAMBURGER)
assert "jquery" not in header and "<style>" in header, "header transform failed"
(site / "partials/qs-header.html").write_text(header)

footer = (theme / "partials/footer.html").read_text()
footer = footer[: footer.index('<script src="{{"js/clipboard.min.js"')]
footer += """    <script>
      document.getElementById("sidebar-toggle").addEventListener("click", function (e) {
        e.preventDefault(); document.body.classList.toggle("sidebar-hidden");
      });
    </script>
  </body>
</html>
"""
(site / "partials/qs-footer.html").write_text(footer)

for kind in ("list", "single"):
    tpl = (theme / f"_default/{kind}.html").read_text()
    tpl = tpl.replace('partial "header.html"', 'partial "qs-header.html"').replace(
        'partial "footer.html"', 'partial "qs-footer.html"')
    (site / "quickstart").mkdir(parents=True, exist_ok=True)
    (site / f"quickstart/{kind}.html").write_text(tpl)

menu = (site / "partials/menu.html").read_text()
menu = menu.replace('<a href="/"> {{ partial "logo.html" . }}',
                    '<a href="/" aria-label="Qrypt documentation home"> {{ partial "logo.html" . }}')
(site / "partials/qs-menu.html").write_text(menu)
header = (site / "partials/qs-header.html").read_text().replace('partial "menu.html"', 'partial "qs-menu.html"')
(site / "partials/qs-header.html").write_text(header)

# Code highlighting for our section only; the rest of the site keeps its own config.
(site / "quickstart/_markup").mkdir(parents=True, exist_ok=True)
(site / "quickstart/_markup/render-codeblock.html").write_text(
    '{{ transform.Highlight .Inner .Type "style=github" }}\n')
print("lean variant applied (quickstart section only)")
