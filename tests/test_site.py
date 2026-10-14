"""Built-site checks (docs/TDD.md section 9, T-site-1..10; rules W1-W13, S11, C2).

The site is built once per session with `python3 scripts/build_site.py --out <tmp>` (and once
more with --upstream-only for T-site-3), unless SITE_PUBLIC (and SITE_UPSTREAM_PUBLIC) point at
existing builds. See tests/fixtures/sitebuild.py. A missing or failing build fails the tests; it
never skips them.

Each T-site-N is a `check_*` function over a public/ directory. The `test_t_site_N` tests run it
against the real build. The `test_selftest_*` tests run the same functions against a small
site generated from tests/fixtures/site/ (to show the checks pass on good output) and against
deliberately broken copies (to show each check can fail). Only the stdlib `html.parser` is used.
"""
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import sitebuild  # noqa: E402
from conftest import read_help  # noqa: E402

pytestmark = pytest.mark.site

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "site"
SNIPPET_FILES = {"node": "node/quickstart.mjs", "python": "python/quickstart.py"}
DOWNLOADS = {"quickstart.mjs": "node/quickstart.mjs", "quickstart.py": "python/quickstart.py",
             "requirements.txt": "python/requirements.txt"}
DOWNLOAD_ENV_EXAMPLE = ".env.example"

SUBMODULE_COMMIT = "a88e74cf9c"
THEME_COMMIT = "e817f53"
DEFAULT_BASE = "https://qrypt-quickstart.pages.dev/"
QUICKSTART = "quickstart/index.html"
TROUBLESHOOTING = "quickstart/troubleshooting/index.html"
OUR_PAGES = (QUICKSTART, TROUBLESHOOTING)

# S10/S11: the anchors a snippet failure can link to (TDD section 2). Unknown HTTP statuses
# (418 and the like) land on `other`; 302 is listed because the redirect case emits it.
REQUIRED_ANCHORS = ("no-key", "runtime", "network", "302", "400", "401", "403", "429", "500",
                    "503", "round-trip", "other")
FOOTER = ("Unofficial community quickstart. Docs shell © Qrypt, CC BY-NC-SA 4.0. "
          "Official docs: docs.qrypt.com")
BANNED_WORDS = ("unbreakable", "perfectly secure", "quantum-proof", "uncrackable",
                "military-grade", "quantum-safe key")
MIN_CONTRAST = 4.5
INLINE_SCRIPT_BUDGET = 2048  # W3

# --- a tiny HTML tree (html.parser only) -------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
P_CLOSERS = {"address", "article", "aside", "blockquote", "div", "dl", "fieldset", "figure",
             "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav",
             "ol", "p", "pre", "section", "table", "ul"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
NO_TEXT = {"script", "style", "template"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self, skip=NO_TEXT):
        if self.tag in skip:
            return ""
        return "".join(c if isinstance(c, str) else c.text(skip) for c in self.children)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def __repr__(self):
        return f"<{self.tag} {self.attrs}>"


class _Tree(HTMLParser):
    """Builds a tree and tolerates the omitted end tags that minified HTML has (</li>, </p>)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#root", {}, None)

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            node = self.cur
            while node.parent is not None and node.tag not in ("ul", "ol"):
                if node.tag == "li":
                    self.cur = node.parent
                    break
                node = node.parent
        elif tag in P_CLOSERS and self.cur.tag == "p":
            self.cur = self.cur.parent
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.cur
        while node.parent is not None:
            if node.tag == tag:
                self.cur = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html):
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree.root


def load(public, rel):
    path = Path(public) / rel
    assert path.is_file(), f"{rel} is missing from the build ({public})"
    return parse(path.read_text(encoding="utf-8"))


def squash(text):
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def find(root, tag=None, **attrs):
    """Nodes with this tag whose attributes equal the given values (attr_name -> value)."""
    want = {k.replace("_", "-"): v for k, v in attrs.items()}
    return [n for n in root.walk()
            if (tag is None or n.tag == tag) and all(n.attrs.get(k) == v for k, v in want.items())]


def ids_of(root):
    found = {}
    for node in root.walk():
        if node.attrs.get("id"):
            found.setdefault(node.attrs["id"], []).append(node)
    return found


def is_pre(node):
    return node.tag == "pre"


# --- T-site-1 (C2) -----------------------------------------------------------------------


def git(path, *args):
    done = subprocess.run(["git", "-C", str(path), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, f"git {' '.join(args)} in {path}: {done.stderr.strip()}"
    return done.stdout.strip()


def check_submodule(repo=REPO):
    upstream = repo / "site" / "upstream"
    assert upstream.is_dir(), "site/upstream is missing: check out with --recurse-submodules"
    head = git(upstream, "rev-parse", "HEAD")
    assert head.startswith(SUBMODULE_COMMIT), \
        f"site/upstream is at {head}, expected {SUBMODULE_COMMIT}"
    recorded = git(repo, "ls-files", "-s", "--", "site/upstream").split()
    assert recorded[:1] == ["160000"], f"site/upstream is not a recorded submodule: {recorded}"
    assert recorded[1].startswith(SUBMODULE_COMMIT), \
        f"the repo records site/upstream at {recorded[1]}, expected {SUBMODULE_COMMIT}"
    theme = git(upstream / "themes" / "hugo-theme-learn", "rev-parse", "HEAD")
    assert theme.startswith(THEME_COMMIT), f"theme submodule is at {theme}, expected {THEME_COMMIT}"


# --- T-site-2 (W1, W13) ------------------------------------------------------------------


def check_not_official(public):
    public = Path(public)
    leaked = [str(p.relative_to(public)) for p in public.rglob("*") if p.name.upper() == "CNAME"]
    assert not leaked, f"CNAME must not be deployed: {leaked}"
    robots = public / "robots.txt"
    assert robots.is_file(), "robots.txt is missing"
    lines = [ln.split("#")[0].strip() for ln in robots.read_text().splitlines()]
    assert "Disallow: /" in lines, f"robots.txt must contain 'Disallow: /': {lines}"
    assert any(re.fullmatch(r"(?i)user-agent:\s*\*", ln) for ln in lines), \
        "robots.txt must address 'User-agent: *'"
    for rel in OUR_PAGES:
        page = load(public, rel)
        metas = [m for m in find(page, "meta") if m.attrs.get("name", "").lower() == "robots"]
        assert any("noindex" in m.attrs.get("content", "").lower() for m in metas), \
            f"{rel}: <meta name=robots content=noindex> is missing"
        footers = [squash(f.text()) for f in find(page, "footer")]
        assert any(FOOTER in text for text in footers), \
            f"{rel}: no <footer> contains {FOOTER!r}; footers: {footers}"


# --- T-site-3 (W1) -----------------------------------------------------------------------

CACHE_BUST = re.compile(r"\?\d+(?=[\"'\s>&)]|$)")
# Hugo leaks a site-wide shortcode counter into some upstream heading ids; our extra pages
# shift the counter without changing the page, so the number is normalized away.
HUGO_COUNTER = re.compile(r"hahahugoshortcode\d+s\d+hbhb")


class _Tokens(HTMLParser):
    """Start tags (with sorted attributes), end tags and text, with every character of the
    text kept exactly: inside <pre>, <code>, <script> and <style> whitespace is content, and
    elsewhere the two builds turned out to need no normalization either (the only allowed
    differences are the cache-busting ?digits, the Hugo shortcode counter and the added
    Quickstart menu item, all handled before or after tokenizing). Adjacent text chunks are
    merged so that the parser's chunking cannot matter."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        self.tokens.append(("s", tag, tuple(sorted((k, v or "") for k, v in attrs))))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.tokens.append(("e", tag))

    def handle_data(self, data):
        if self.tokens and self.tokens[-1][0] == "d":
            self.tokens[-1] = ("d", self.tokens[-1][1] + data)
        else:
            self.tokens.append(("d", data))

    def handle_comment(self, data):
        pass


def tokenize(html):
    tokens = _Tokens()
    tokens.feed(html)
    tokens.close()
    return tokens.tokens


def drop_quickstart_menu_items(tokens):
    """Remove the added Quickstart menu <li> (and anything nested in it). Returns the number
    removed. Tolerates a minified list whose </li> end tags are omitted."""
    removed, i = 0, 0
    while i < len(tokens):
        if tokens[i][:2] == ("s", "li"):
            depth, j, end = 0, i + 1, None
            while j < len(tokens):
                kind, tag = tokens[j][:2]
                if tag in ("ul", "ol"):
                    if kind == "s":
                        depth += 1
                    elif depth == 0:
                        end = j
                        break
                    else:
                        depth -= 1
                elif tag == "li" and depth == 0:
                    end = j + 1 if kind == "e" else j
                    break
                j += 1
            end = len(tokens) if end is None else end
            hrefs = [dict(t[2]).get("href", "") for t in tokens[i:end] if t[:2] == ("s", "a")]
            if hrefs and hrefs[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1] == "quickstart":
                del tokens[i:end]
                removed += 1
                continue
        i += 1
    return removed


def check_shell_unchanged(public, upstream):
    public, upstream = Path(public), Path(upstream)
    pages = sorted(p.relative_to(upstream) for p in upstream.rglob("*.html"))
    assert pages, f"the upstream-only build at {upstream} has no HTML pages"
    ours = {p.relative_to(public) for p in public.rglob("*.html")
            if p.relative_to(public).parts[0] != "quickstart"}
    extra = sorted(str(p) for p in ours - set(pages))
    missing = sorted(str(p) for p in set(pages) - ours)
    assert not missing, f"{len(missing)} upstream pages are missing from the build: {missing[:5]}"
    assert not extra, f"{len(extra)} non-quickstart pages are not in upstream: {extra[:5]}"
    differing = []
    for rel in pages:
        if rel.parts[0] == "quickstart":
            continue
        a = HUGO_COUNTER.sub("hahahugoshortcodeN", CACHE_BUST.sub("", (upstream / rel).read_text(
            encoding="utf-8", errors="replace")))
        b = HUGO_COUNTER.sub("hahahugoshortcodeN", CACHE_BUST.sub("", (public / rel).read_text(
            encoding="utf-8", errors="replace")))
        if a == b:
            continue
        ta, tb = tokenize(a), tokenize(b)
        removed = drop_quickstart_menu_items(tb)
        if removed > 1:
            differing.append(f"{rel}: {removed} Quickstart menu items (expected at most 1)")
        elif ta != tb:
            n = next((k for k, (x, y) in enumerate(zip(ta, tb)) if x != y), min(len(ta), len(tb)))
            differing.append(f"{rel}: first difference at token {n}: upstream "
                             f"{ta[n:n + 1]} vs ours {tb[n:n + 1]}")
    assert not differing, f"{len(differing)} of {len(pages)} pages differ from upstream:\n" + \
        "\n".join(differing[:10])


# --- T-site-4 (S11, W12) -----------------------------------------------------------------


def snippet_anchors():
    """String-literal anchors passed to fail(...) in either snippet."""
    found = set()
    for rel in SNIPPET_FILES.values():
        lines = (REPO / "quickstart" / rel).read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines):
            near = " ".join(lines[max(0, n - 1):n + 1])
            if "fail(" in near:
                found.update(re.findall(r"""["']([a-z]+(?:-[a-z]+)*)["']\s*\)""", line))
    return found


def check_help_and_anchors(public):
    helps = {read_help("node"), read_help("python")}
    assert len(helps) == 1, f"the two snippets disagree on HELP: {helps}"
    (help_url,) = helps
    assert help_url == DEFAULT_BASE + "quickstart/troubleshooting/", \
        f"HELP is {help_url}; the site is served at {DEFAULT_BASE}quickstart/troubleshooting/"
    page = load(public, TROUBLESHOOTING)
    for link in find(page, "link"):
        if link.attrs.get("rel") == "canonical":
            assert link.attrs.get("href") == help_url, \
                f"canonical URL {link.attrs.get('href')} differs from HELP {help_url}"
    found = ids_of(page)
    wanted = sorted(set(REQUIRED_ANCHORS) | snippet_anchors())
    missing = [a for a in wanted if a not in found]
    assert not missing, f"anchors missing from {TROUBLESHOOTING}: {missing}"
    not_headings = [a for a in wanted if not all(n.tag in HEADINGS for n in found[a])]
    assert not not_headings, f"these anchors are not on headings (W12): {not_headings}"
    duplicated = [a for a in wanted if len(found[a]) > 1]
    assert not duplicated, f"duplicate ids make the anchor ambiguous: {duplicated}"


# --- T-site-5 (W6) -----------------------------------------------------------------------


def pre_texts(root):
    return [n.text(skip=()) for n in root.walk() if is_pre(n)]


def check_snippets_served(public):
    public = Path(public)
    served = {**DOWNLOADS, DOWNLOAD_ENV_EXAMPLE: None}
    for name, source in served.items():
        got = public / "quickstart" / "files" / name
        assert got.is_file(), f"/quickstart/files/{name} is not served"
        want = (REPO / (source and f"quickstart/{source}" or DOWNLOAD_ENV_EXAMPLE)).read_bytes()
        assert got.read_bytes() == want, f"/quickstart/files/{name} differs from the repo file"
    blocks = [t.rstrip("\n").replace("\r\n", "\n") for t in pre_texts(load(public, QUICKSTART))]
    for lang, rel in SNIPPET_FILES.items():
        want = (REPO / "quickstart" / rel).read_text(encoding="utf-8").rstrip("\n")
        if want in blocks:
            continue
        near = max(blocks, key=lambda b: _shared_prefix(b, want), default="")
        k = _shared_prefix(near, want)
        raise AssertionError(f"no <pre> on {QUICKSTART} equals the {lang} snippet; closest block "
                             f"diverges at char {k}: page {near[k:k + 40]!r} vs file "
                             f"{want[k:k + 40]!r}")


def _shared_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# --- T-site-6 (W7) -----------------------------------------------------------------------


def inline_scripts(root):
    return [n.text(skip=()) for n in root.walk() if n.tag == "script" and not n.attrs.get("src")]


def check_tabs(public):
    page = load(public, QUICKSTART)
    ids = ids_of(page)
    tablists = find(page, role="tablist")
    assert tablists, f"{QUICKSTART} has no role=tablist"
    seen_panels = set()
    for tablist in tablists:
        tabs = find(tablist, role="tab")
        assert len(tabs) == 2, f"a tab group must hold the Node.js and Python tabs: {tabs}"
        assert sorted(squash(t.text()) for t in tabs) == ["Node.js", "Python"], \
            f"tab labels: {[squash(t.text()) for t in tabs]}"
        assert [t.attrs.get("aria-selected") for t in tabs].count("true") == 1, \
            "exactly one tab must have aria-selected=true"
        for tab in tabs:
            tab_id = tab.attrs.get("id")
            assert tab_id and len(ids.get(tab_id, [])) == 1, f"a tab needs a unique id: {tab}"
            selected = tab.attrs.get("aria-selected")
            assert selected in ("true", "false"), f"aria-selected={selected!r} on {tab}"
            assert tab.attrs.get("tabindex") == ("0" if selected == "true" else "-1"), \
                f"roving tabindex: {tab}"
            target = tab.attrs.get("aria-controls")
            assert target in ids, f"aria-controls={target!r} matches no element id"
            assert len(ids[target]) == 1, f"id {target!r} is not unique"
            panel = ids[target][0]
            assert panel.attrs.get("role") == "tabpanel", f"#{target} is not role=tabpanel"
            assert panel.attrs.get("aria-labelledby") == tab_id, \
                f"#{target} has aria-labelledby={panel.attrs.get('aria-labelledby')!r}, which " \
                f"does not point back to the tab that controls it (#{tab_id})"
            assert target not in seen_panels, f"#{target} is controlled by two tabs"
            seen_panels.add(target)
    panels = find(page, role="tabpanel")
    assert {p.attrs.get("id") for p in panels} == seen_panels and len(panels) == len(seen_panels), \
        "every role=tabpanel must be controlled by exactly one tab"
    for panel in panels:
        assert panel.attrs.get("tabindex") == "0", \
            f"#{panel.attrs['id']}: a tab panel must be keyboard focusable (tabindex=0)"
        # No-JS render (progressive enhancement): the served HTML hides nothing and each panel
        # carries a heading, so the two panels read as stacked sections.
        assert "hidden" not in panel.attrs, f"#{panel.attrs['id']} is hidden in the served HTML"
        label = ids.get(panel.attrs.get("aria-labelledby", ""), [None])[0]
        has_heading = any(n.tag in HEADINGS for n in panel.walk()) or \
            (label is not None and label.tag in HEADINGS)
        assert has_heading, f"#{panel.attrs['id']} has no heading for the no-JS stacked layout"
    # A panel may legitimately hold prose only (Node has nothing to install), but every tab
    # group must carry at least one code block.
    for tablist in find(page, role="tablist"):
        group = [ids[t.attrs["aria-controls"]][0] for t in find(tablist, role="tab")]
        assert any(is_pre(n) for panel in group for n in panel.walk()), \
            f"tab group {[p.attrs.get('id') for p in group]} holds no code block"
    script = "\n".join(inline_scripts(page))
    for needle in ("ArrowLeft", "ArrowRight", "Home", "End", "qs-lang", "localStorage", "try"):
        assert needle in script, f"the inline script never mentions {needle!r} (W7)"
    # Without JavaScript the panels must all be shown: the page carries a <noscript> rule that
    # sets display:block on the panels' class. (The rule is read, not emulated.)
    shown = {c for rules in noscript_rules(page) for sel, body in rules
             if re.search(r"display\s*:\s*block", body) for c in re.findall(r"\.([\w-]+)", sel)}
    classes = {c for p in panels for c in p.attrs.get("class", "").split()}
    assert classes & shown, \
        f"no <noscript><style> rule shows the tab panels (classes {sorted(classes)}) with " \
        f"display:block; noscript rules show {sorted(shown)}"


def noscript_rules(page):
    """[(selector, declarations), ...] for each <style> inside a <noscript>, as one list per
    <style>."""
    return [re.findall(r"([^{}]+)\{([^{}]*)\}", style.text(skip=()))
            for ns in find(page, "noscript") for style in ns.walk() if style.tag == "style"]


def check_menu_toggle(public):
    """F5: the mobile menu toggle is a native button that names and tracks the sidebar."""
    for rel in OUR_PAGES:
        page = load(public, rel)
        ids = ids_of(page)
        toggles = find(page, id="sidebar-toggle")
        assert len(toggles) == 1, f"{rel}: expected one #sidebar-toggle, found {len(toggles)}"
        (toggle,) = toggles
        assert toggle.tag == "button" and toggle.attrs.get("type") == "button", \
            f"{rel}: the menu toggle must be a <button type=button>, not {toggle}"
        assert toggle.attrs.get("aria-label", "").strip(), f"{rel}: the menu toggle has no aria-label"
        target = toggle.attrs.get("aria-controls")
        assert target and len(ids.get(target, [])) == 1, \
            f"{rel}: aria-controls={target!r} must name exactly one element"
        assert toggle.attrs.get("aria-expanded") == "false", \
            f"{rel}: the menu starts collapsed: aria-expanded=false, found " \
            f"{toggle.attrs.get('aria-expanded')!r}"
        script = "\n".join(inline_scripts(page))
        assert re.search(r"setAttribute\(\s*[\"']aria-expanded", script), \
            f"{rel}: the inline script never updates aria-expanded"


def check_script_budget(public):
    """W3 and F9, on both pages: exactly one inline script, under 2 KB; no <script src>; no
    external stylesheet; exactly one <style> (a <style> inside <noscript> is the no-JavaScript
    fallback of F7 and is not counted)."""
    for rel in OUR_PAGES:
        page = load(public, rel)
        scripts = find(page, "script")
        external = [s.attrs["src"] for s in scripts if s.attrs.get("src")]
        assert not external, f"{rel}: external scripts are not allowed: {external}"
        inline = inline_scripts(page)
        assert len(inline) == 1, f"{rel}: expected exactly one inline <script>, found {len(inline)}"
        size = len(inline[0].encode())
        assert size < INLINE_SCRIPT_BUDGET, f"{rel}: inline JS is {size} B, budget is < 2 KB (W3)"
        sheets = [lk.attrs.get("href") for lk in find(page, "link")
                  if "stylesheet" in lk.attrs.get("rel", "").lower().split()]
        assert not sheets, f"{rel}: external stylesheets are not allowed: {sheets}"
        styles = [n for n in find(page, "style")
                  if not any(a.tag == "noscript" for a in n.ancestors())]
        assert len(styles) == 1, f"{rel}: expected exactly one <style>, found {len(styles)}"


# --- T-site-7 (W8) -----------------------------------------------------------------------

COPY_LABEL = re.compile(r"Copy \S.* code")


def check_copy_buttons(public):
    for rel in OUR_PAGES:
        page = load(public, rel)
        pres = [n for n in page.walk() if is_pre(n)]
        copy = [b for b in find(page, "button")
                if "copy" in (b.text() + " ".join(b.attrs.values())).lower()]
        assert len(copy) >= len(pres), f"{rel}: {len(pres)} code blocks but {len(copy)} copy buttons"
        for button in copy:
            label = button.attrs.get("aria-label", "")
            assert COPY_LABEL.fullmatch(label), \
                f"{rel}: copy button needs aria-label='Copy <language> code', has {label!r}"
        live = [n for n in page.walk() if n.attrs.get("aria-live") == "polite"]
        assert live or not pres, f"{rel}: no aria-live=polite region for the 'Copied' announcement"


# --- T-site-8 (W9) -----------------------------------------------------------------------

NAMED = {"white": (255, 255, 255), "black": (0, 0, 0)}


def parse_color(value):
    value = value.strip().lower()
    hexed = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if hexed:
        h = hexed.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    rgb = re.fullmatch(r"rgb\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*\)", value)
    if rgb:
        return tuple(int(g) for g in rgb.groups())
    return NAMED.get(value)


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def style_of(node):
    decls = {}
    for part in node.attrs.get("style", "").split(";"):
        if ":" in part:
            key, _, val = part.partition(":")
            decls[key.strip().lower()] = val.strip()
    return decls


def own_background(node):
    """The color of this element's own inline background-color or background, if it has one."""
    decls = style_of(node)
    for key in ("background-color", "background"):
        for token in re.findall(r"#[0-9a-fA-F]{3,6}\b|rgb\([^)]*\)|\b[a-z]+\b", decls.get(key, "")):
            color = parse_color(token)
            if color:
                return color
    return None


def effective_background(node, pre, block_bg):
    """What a token is painted on: its own background, else the nearest wrapping span's inside
    the block, else the block's."""
    for item in [node, *node.ancestors()]:
        if item is pre:
            break
        color = own_background(item)
        if color:
            return color
    return block_bg


def effective_color(node, pre):
    """The inline foreground this element has or inherits inside the block (None: from CSS)."""
    for item in [node, *node.ancestors()]:
        value = style_of(item).get("color")
        if value is not None:
            return value
        if item is pre:
            break
    return None


def background_of(page, pre):
    """Inline background on the block or the nearest wrapper, else a plain stylesheet rule."""
    for node in [pre, *pre.ancestors()]:
        decls = style_of(node)
        for key in ("background-color", "background"):
            for token in re.findall(r"#[0-9a-fA-F]{3,6}\b|rgb\([^)]*\)|\b[a-z]+\b", decls.get(key, "")):
                color = parse_color(token)
                if color:
                    return color
    for sheet in (n.text(skip=()) for n in page.walk() if n.tag == "style"):
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", sheet):
            if any(re.search(r"(^|[\s.>])(pre|highlight|chroma)$", s.strip())
                   for s in selectors.split(",")):
                for color in re.findall(r"background(?:-color)?\s*:\s*([^;]+)", body):
                    if parse_color(color):
                        return parse_color(color)
    return None


def check_code_contrast(public):
    colored = 0
    for rel in OUR_PAGES:
        page = load(public, rel)
        for pre in (n for n in page.walk() if is_pre(n)):
            bg = background_of(page, pre)
            assert bg is not None, f"{rel}: cannot determine the code block background"
            for node in pre.walk():
                if "color" not in style_of(node) and own_background(node) is None:
                    continue
                value = effective_color(node, pre)
                if value is None:
                    continue
                fg = parse_color(value)
                assert fg is not None, f"{rel}: cannot evaluate color {value!r} in a code block"
                colored += 1
                token_bg = effective_background(node, pre, bg)
                ratio = contrast(fg, token_bg)
                assert ratio >= MIN_CONTRAST, \
                    f"{rel}: token color {value} on background {token_bg} is {ratio:.2f}:1 (< 4.5)"
    assert colored, "no inline token colors found in any code block: the check would be vacuous"


# --- T-site-9 (W11) ----------------------------------------------------------------------


def section_text(root, heading):
    """Text from a heading to the next heading of the same or a higher level."""
    level, out, on = int(heading.tag[1]), [], False

    def visit(node):
        nonlocal on
        for child in node.children:
            if isinstance(child, str):
                if on:
                    out.append(child)
            elif child is heading:
                on = True
            elif on and child.tag in HEADINGS and int(child.tag[1]) <= level:
                raise StopIteration
            elif child.tag not in NO_TEXT:
                visit(child)

    try:
        visit(root)
    except StopIteration:
        pass
    return squash("".join(out))


CALLOUT_CLASS = re.compile(r"note|callout|admonition|alert", re.I)


def titled_block_text(page, phrase):
    """Text of the block titled `phrase`. The title must be a heading (a screen reader moves by
    headings): a heading inside a callout gives the whole callout, any other heading gives its
    section. A callout whose title is a paragraph does not count."""
    for h in (n for n in page.walk() if n.tag in HEADINGS):
        if squash(h.text()).lower().startswith(phrase):
            parent = h.parent
            if parent is not None and CALLOUT_CLASS.search(parent.attrs.get("class", "")):
                return squash(parent.text())
            return section_text(page, h)
    raise AssertionError(f"no heading is titled {phrase!r} (a callout title must be a heading)")


def check_content_blocks(public):
    public = Path(public)
    page = load(public, QUICKSTART)
    gives = titled_block_text(page, "what this does and doesn't give you")
    assert re.search(r"\bTLS\b", gives) and re.search(r"\bSDK\b", gives), \
        f"the 'give you' section must mention TLS and the Qrypt SDK: {gives!r}"
    portal = titled_block_text(page, "the portal showed you different sample code")
    assert "eaas-aws" in portal and "api-eus" in portal, \
        f"the portal section must map eaas-aws onto api-eus: {portal!r}"
    body = squash(page.text()).lower()
    for label, pattern in (("print", r"\bprint\b"), ("log", r"\blog(?:s|ging)?\b"),
                           ("commit", r"\bcommit\b"),
                           ("reuse entropy bytes", r"\breus\w*\b[^.]{0,60}\bentropy\b|"
                                                   r"\bentropy\b[^.]{0,60}\breus\w*\b|"
                                                   r"\breus\w*\b[^.]{0,30}\bbytes\b")):
        assert re.search(pattern, body), f"key-handling warning is missing: {label}"
    # W10: stated once, near the top (before the first code block), with a link to the benchmark.
    time_line = "About 5 minutes once you have a key."
    assert body.count(time_line.lower()) == 1, f"{time_line!r} must appear exactly once"
    first_pre = next(n for n in page.walk() if is_pre(n))
    before = squash("".join(_text_before(page, first_pre))).lower()
    assert time_line.lower() in before, "the time target must come before the first code block"
    assert any("benchmarks.md" in a.attrs.get("href", "") for a in find(page, "a")), \
        "the time target must link to docs/evidence/benchmarks.md"
    texts = {rel: squash(load(public, rel).text()) for rel in OUR_PAGES}
    for rel in ("llms.txt", "quickstart/index.md"):
        if (public / rel).is_file():
            texts[rel] = squash((public / rel).read_text(encoding="utf-8"))
    for rel, text in texts.items():
        hits = [w for w in BANNED_WORDS if w in text.lower()]
        assert not hits, f"{rel} uses banned wording: {hits}"


def _text_before(root, stop):
    out = []

    def visit(node):
        for child in node.children:
            if child is stop:
                raise StopIteration
            if isinstance(child, str):
                out.append(child)
            elif child.tag not in NO_TEXT:
                visit(child)

    try:
        visit(root)
    except StopIteration:
        pass
    return out


# --- T-site-10 (W5) ----------------------------------------------------------------------


def check_llms_txt(public):
    public = Path(public)
    llms = public / "llms.txt"
    assert llms.is_file(), "llms.txt is missing"
    base = sitebuild.serve(public)
    with urlopen(base + "llms.txt", timeout=10) as resp:
        ctype = resp.headers.get_content_type()
    assert ctype == "text/plain", f"/llms.txt is served as {ctype}"
    for rel in ("llms.txt", "quickstart/index.md"):
        path = public / rel
        assert path.is_file(), f"{rel} is missing"
        text = path.read_text(encoding="utf-8")
        for lang, source in SNIPPET_FILES.items():
            code = (REPO / "quickstart" / source).read_text(encoding="utf-8").rstrip("\n")
            assert code in text, f"{rel} does not contain the {lang} snippet verbatim"


# --- the tests on the real build ---------------------------------------------------------


def test_t_site_1_submodule_commit():
    check_submodule()


def test_t_site_2_not_passing_as_official():
    check_not_official(sitebuild.public())


def test_t_site_3_shell_matches_upstream():
    check_shell_unchanged(sitebuild.public(), sitebuild.upstream_public())


def test_t_site_4_help_url_and_anchors():
    check_help_and_anchors(sitebuild.public())


def test_t_site_5_snippets_served_and_rendered_verbatim():
    check_snippets_served(sitebuild.public())


def test_t_site_5_build_copies_all_four_files_to_both_locations(tmp_path):
    """F10: scripts/build_site.py puts quickstart.mjs, quickstart.py, requirements.txt and
    .env.example, byte for byte, in both site/assets/snippets/ and site/static/quickstart/files/
    (the second location is what check_snippets_served then finds in the built site)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site", REPO / "scripts" / "build_site.py")
    build_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_site)
    site = tmp_path / "site"
    build_site.copy_snippets(site)
    for folder in (site / "assets" / "snippets", site / "static" / "quickstart" / "files"):
        assert sorted(p.name for p in folder.iterdir()) == sorted(build_site.SNIPPETS.values()), folder
        for src, name in build_site.SNIPPETS.items():
            assert (folder / name).read_bytes() == (REPO / src).read_bytes(), f"{folder}/{name}"


def test_t_site_6_tabs_markup():
    check_tabs(sitebuild.public())


def test_t_site_6_inline_script_budget():
    check_script_budget(sitebuild.public())


def test_t_site_6_menu_toggle_is_a_button():
    check_menu_toggle(sitebuild.public())


def test_t_site_7_copy_buttons():
    check_copy_buttons(sitebuild.public())


def test_t_site_8_code_contrast():
    check_code_contrast(sitebuild.public())


def test_t_site_9_content_blocks_and_banned_words():
    check_content_blocks(sitebuild.public())


def test_t_site_10_llms_txt():
    check_llms_txt(sitebuild.public())


# --- self-tests: the checks against generated fixture output ----------------------------


def highlight(code, color="#0a3069", background="#f7f7f7", token_bg=None):
    from html import escape
    token_bg = f";background-color:{token_bg}" if token_bg else ""
    lines = "".join(f"<span style=display:flex><span><span style=color:{color}{token_bg}>"
                    f"{escape(ln, quote=True)}</span>\n</span></span>"
                    for ln in code.rstrip("\n").split("\n"))
    return (f"<pre tabindex=0 style=background-color:{background};tab-size:4>"
            f"<code>{lines}</code></pre>")


PYTHON_PANEL = "<div role=tabpanel class=qs-panel tabindex=0 id=panel-python"
NOSCRIPT = ("<noscript><style>.qs-tab-heading{display:block}"
            ".qs-tabs .qs-panel{display:block!important}</style></noscript>")
TOGGLE = ('<button type=button id=sidebar-toggle aria-label="Toggle navigation" '
          'aria-controls=sidebar aria-expanded=false>Menu</button>')
INLINE_SCRIPT = re.search(r"<script>.*?</script>", (FIXTURES / "quickstart.html").read_text(
    encoding="utf-8"), re.S).group(0)


def build_fixture(dest, **break_it):
    """A small good site (tests/fixtures/site/ plus the real snippet files); keyword flags
    apply one defect each."""
    dest = Path(dest)
    (dest / "quickstart" / "troubleshooting").mkdir(parents=True)
    (dest / "quickstart" / "files").mkdir()
    code = {lang: (REPO / "quickstart" / rel).read_text(encoding="utf-8")
            for lang, rel in SNIPPET_FILES.items()}
    color = "#cccccc" if break_it.get("low_contrast") else "#0a3069"
    color = break_it.get("token_color") or color
    page = (FIXTURES / "quickstart.html").read_text(encoding="utf-8")
    page = page.replace("@@NODE@@", highlight(code["node"], color, token_bg=break_it.get("token_bg")))
    page = page.replace("@@PYTHON@@", highlight(code["python"], color,
                                                 token_bg=break_it.get("token_bg")))
    if break_it.get("edited_snippet"):
        page = page.replace("fetch(", "fetch (")
    for needle, repl in {
        "no_aria_label": ('aria-label="Copy Node.js code"', ""),
        "no_live_region": (" aria-live=polite", ""),
        "two_selected": ("aria-selected=false", "aria-selected=true"),
        "no_controls": ("aria-controls=panel-python", ""),
        "hidden_panel": (PYTHON_PANEL, "<div hidden " + PYTHON_PANEL[1:]),
        "panel_not_focusable": (" tabindex=0 id=panel-python", " id=panel-python"),
        "broken_reciprocal": ("aria-labelledby=tab-python", "aria-labelledby=tab-node"),
        "duplicate_tab_id": ("<button role=tab id=tab-python", "<button role=tab id=tab-node"),
        "no_noscript": (NOSCRIPT, ""),
        "noscript_other_rule": (".qs-tabs .qs-panel{display:block!important}",
                                ".qs-tabs .qs-pane{display:block!important}"),
        "noscript_hides": ("{display:block!important}", "{display:none!important}"),
        "toggle_is_a_link": (TOGGLE, TOGGLE.replace("<button type=button", "<a href=# role=button")
                             .replace("</button>", "</a>")),
        "toggle_no_expanded": (" aria-expanded=false>Menu", ">Menu"),
        "toggle_no_controls": (" aria-controls=sidebar", ""),
        "toggle_no_label": (' aria-label="Toggle navigation"', ""),
        "toggle_script_no_sync": ('nav.setAttribute("aria-expanded",true)', 'nav.setAttribute("x",true)'),
        "external_script": ("</head>", "<script src=/js/theme.js></script></head>"),
        "second_script": ("</head>", "<script>var a=1</script></head>"),
        "no_script": (INLINE_SCRIPT, ""),
        "big_script": ('var K="qs-lang"', "/*" + "x" * 2100 + '*/var K="qs-lang"'),
        "external_stylesheet": ("</head>", "<link rel=stylesheet href=/css/theme.css?1></head>"),
        "second_style": ("</head>", "<style>b{color:#000}</style></head>"),
        "no_noindex": ("<meta name=robots content=noindex>", ""),
        "no_footer": ("Unofficial community quickstart.", "Community quickstart."),
        "banned": ("Keep the key safe", "An unbreakable key"),
        "no_tls": ("TLS", "HTTPS"),
        "no_benchmark_link": ("benchmarks.md", "results.html"),
    }.items():
        if break_it.get(needle):
            assert repl[0] in page, f"fixture template lacks {repl[0]!r}"
            page = page.replace(repl[0], repl[1])
    (dest / "quickstart" / "index.html").write_text(page, encoding="utf-8")
    sections = "".join(f"<h2 id={a}>HTTP {a}</h2><p>Cause and fix.\n" for a in REQUIRED_ANCHORS
                       if a != break_it.get("drop_anchor"))
    (dest / "quickstart" / "troubleshooting" / "index.html").write_text(
        (FIXTURES / "troubleshooting.html").read_text().replace("@@SECTIONS@@", sections))
    for name, source in {**DOWNLOADS, DOWNLOAD_ENV_EXAMPLE: None}.items():
        src = REPO / (source and f"quickstart/{source}" or DOWNLOAD_ENV_EXAMPLE)
        data = src.read_bytes() + (b"#\n" if break_it.get("edited_download") else b"")
        (dest / "quickstart" / "files" / name).write_bytes(data)
    (dest / "robots.txt").write_text((FIXTURES / "robots.txt").read_text())
    if break_it.get("cname"):
        (dest / "CNAME").write_text("docs.qrypt.com\n")
    llms = f"# Quickstart\n\n{code['node']}\n{code['python']}"
    (dest / "llms.txt").write_text(llms.replace("fetch(", "fetch (") if break_it.get("llms_edit")
                                   else llms)
    (dest / "quickstart" / "index.md").write_text(llms)
    (dest / "index.html").write_text("<!doctype html><title>home</title>")
    return dest


GOOD_CHECKS = [check_not_official, check_help_and_anchors, check_snippets_served, check_tabs,
               check_script_budget, check_menu_toggle, check_copy_buttons, check_code_contrast, check_content_blocks,
               check_llms_txt]


@pytest.fixture
def good_site(tmp_path):
    return build_fixture(tmp_path / "public")


@pytest.mark.parametrize("check", GOOD_CHECKS, ids=lambda c: c.__name__)
def test_selftest_good_fixture_passes(check, good_site):
    check(good_site)


BREAKS = [
    (check_not_official, "cname", "CNAME"),
    (check_not_official, "no_noindex", "noindex"),
    (check_not_official, "no_footer", "footer"),
    (check_help_and_anchors, "drop_anchor", "anchors missing"),
    (check_snippets_served, "edited_snippet", "no <pre>"),
    (check_snippets_served, "edited_download", "differs"),
    (check_tabs, "two_selected", "aria-selected"),
    (check_tabs, "no_controls", "aria-controls"),
    (check_tabs, "hidden_panel", "hidden"),
    (check_tabs, "panel_not_focusable", "focusable"),
    (check_tabs, "broken_reciprocal", "point back"),
    (check_tabs, "duplicate_tab_id", "unique id"),
    (check_tabs, "no_noscript", "noscript"),
    (check_tabs, "noscript_other_rule", "noscript"),
    (check_tabs, "noscript_hides", "noscript"),
    (check_menu_toggle, "toggle_is_a_link", "<button"),
    (check_menu_toggle, "toggle_no_expanded", "aria-expanded"),
    (check_menu_toggle, "toggle_no_controls", "aria-controls"),
    (check_menu_toggle, "toggle_no_label", "aria-label"),
    (check_menu_toggle, "toggle_script_no_sync", "never updates aria-expanded"),
    (check_script_budget, "external_script", "external scripts"),
    (check_script_budget, "second_script", "exactly one inline"),
    (check_script_budget, "no_script", "exactly one inline"),
    (check_script_budget, "big_script", "budget"),
    (check_script_budget, "external_stylesheet", "external stylesheets"),
    (check_script_budget, "second_style", "exactly one <style>"),
    (check_copy_buttons, "no_aria_label", "aria-label"),
    (check_copy_buttons, "no_live_region", "aria-live"),
    (check_code_contrast, "low_contrast", "1."),
    (check_code_contrast, "black_on_black_token", "background (0, 0, 0)"),
    (check_content_blocks, "banned", "banned"),
    (check_content_blocks, "no_tls", "TLS"),
    (check_content_blocks, "no_benchmark_link", "benchmarks.md"),
    (check_llms_txt, "llms_edit", "verbatim"),
]


@pytest.mark.parametrize("check,flag,expect", BREAKS, ids=lambda v: v if isinstance(v, str)
                         else getattr(v, "__name__", str(v)))
def test_selftest_each_check_can_fail(check, flag, expect, tmp_path):
    options = {"black_on_black_token": {"token_color": "#000000", "token_bg": "#000000"}}
    site = build_fixture(tmp_path / "public", **{flag: True, "drop_anchor": "429"
                                                 if flag == "drop_anchor" else None},
                         **options.get(flag, {}))
    with pytest.raises(AssertionError, match=re.escape(expect)):
        check(site)


def test_selftest_contrast_uses_the_token_background(tmp_path):
    """F8: light text on a dark token background passes even though the block is light, and the
    same text on the block's own background fails; dark on dark fails (BREAKS)."""
    ok = build_fixture(tmp_path / "ok", token_color="#ffffff", token_bg="#000000")
    check_code_contrast(ok)
    bad = build_fixture(tmp_path / "bad", token_color="#ffffff")
    with pytest.raises(AssertionError, match=r"1\.07:1"):
        check_code_contrast(bad)


def test_selftest_contrast_math():
    assert contrast((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert contrast((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)
    assert parse_color("#fff") == (255, 255, 255) and parse_color("rgb(1, 2, 3)") == (1, 2, 3)
    assert parse_color("var(--x)") is None


MENU_UPSTREAM = ("<!doctype html><link rel=stylesheet href=/css/a.css?100><nav><ul>"
                 "<li data-nav-id=/sdk/><a href=/sdk/>SDK</a><li data-nav-id=/eaas/>"
                 "<a href=/eaas/>EaaS</a></ul></nav><main><p>Hello</main>")
MENU_OURS = MENU_UPSTREAM.replace("href=/css/a.css?100", "href=/css/a.css?200").replace(
    "<li data-nav-id=/eaas/>", "<li data-nav-id=/quickstart/><a href=/quickstart/>Quickstart"
    "</a><ul><li><a href=/quickstart/troubleshooting/>Fix</a></ul><li data-nav-id=/eaas/>")


def _write_pair(tmp_path, upstream_html, ours_html):
    up, ours = tmp_path / "up", tmp_path / "ours"
    for root, html in ((up, upstream_html), (ours, ours_html)):
        (root / "quickstart").mkdir(parents=True)
        (root / "index.html").write_text(html)
        (root / "quickstart" / "index.html").write_text("<p>ours only</p>" if root == ours else "")
    (up / "quickstart" / "index.html").unlink()
    return ours, up


def test_selftest_shell_diff_ignores_menu_item_and_cache_busting(tmp_path):
    ours, up = _write_pair(tmp_path, MENU_UPSTREAM, MENU_OURS)
    check_shell_unchanged(ours, up)


@pytest.mark.parametrize("edit,expect", [
    (lambda h: h.replace("Hello", "Hello!"), "differ from upstream"),
    (lambda h: h.replace("</ul></nav>", "<li><a href=/quickstart/>Again</a></ul></nav>"),
     "at most 1"),
    (lambda h: h.replace("?200", "?200&x=1"), "differ from upstream"),
])
def test_selftest_shell_diff_detects_changes(tmp_path, edit, expect):
    ours, up = _write_pair(tmp_path, MENU_UPSTREAM, edit(MENU_OURS))
    with pytest.raises(AssertionError, match=expect):
        check_shell_unchanged(ours, up)


TEXTY = ("<main><p>Hello  there, it\u2019s fine<p>Use <code>a  b</code> here"
         "<pre><code>def f():\n    return 1\n</code></pre>"
         "<script>if (a) {\n  b();\n}</script><style>p {\n  margin: 0 }</style></main>")
TEXTY_UPSTREAM = MENU_UPSTREAM.replace("<main><p>Hello</main>", TEXTY)
TEXTY_OURS = MENU_OURS.replace("<main><p>Hello</main>", TEXTY)


def test_selftest_shell_diff_keeps_text_exactly(tmp_path):
    """F2: with identical text, the allowed differences (menu item, cache busting) still pass."""
    ours, up = _write_pair(tmp_path, TEXTY_UPSTREAM, TEXTY_OURS)
    check_shell_unchanged(ours, up)


@pytest.mark.parametrize("old,new", [
    ("    return 1", "  return 1"),                    # indentation inside <pre>
    ("    return 1", "\treturn 1"),                    # tab for spaces inside <pre>
    ("return 1\n</code>", "return 1</code>"),          # trailing newline inside <pre>
    ("def f():\n", "def f():\n\n"),                    # blank line inside <pre>
    ("<code>a  b</code>", "<code>a b</code>"),         # whitespace inside inline <code>
    ("  b();", "b();"),                                 # indentation inside <script>
    ("  margin: 0", "margin: 0"),                       # indentation inside <style>
    ("Hello  there", "Hello there"),                    # whitespace outside code is content too
    ("it\u2019s", "it's"),                              # and so is the apostrophe
], ids=["pre-indent", "pre-tab", "pre-newline", "pre-blank-line", "inline-code", "script",
        "style", "text-whitespace", "text-apostrophe"])
def test_selftest_shell_diff_rejects_whitespace_and_text_changes(tmp_path, old, new):
    """F2: a changed character anywhere in the text, above all indentation inside <pre>, <code>,
    <script> and <style>, is a difference from upstream."""
    assert old in TEXTY_OURS
    ours, up = _write_pair(tmp_path, TEXTY_UPSTREAM, TEXTY_OURS.replace(old, new, 1))
    with pytest.raises(AssertionError, match="differ from upstream"):
        check_shell_unchanged(ours, up)


def test_selftest_shell_diff_detects_missing_and_extra_pages(tmp_path):
    ours, up = _write_pair(tmp_path, MENU_UPSTREAM, MENU_OURS)
    (ours / "stray.html").write_text("<p>x")
    with pytest.raises(AssertionError, match="not in upstream"):
        check_shell_unchanged(ours, up)
    (ours / "stray.html").unlink()
    (up / "gone.html").write_text("<p>y")
    with pytest.raises(AssertionError, match="missing from the build"):
        check_shell_unchanged(ours, up)


def _with_callouts(tmp_path, title_tag):
    """The fixture page with its two titled sections turned into qs-note callouts whose title
    is a `title_tag` element (h3 as the real shortcode emits, or p)."""
    site = build_fixture(tmp_path / "public")
    page = site / QUICKSTART
    html = page.read_text(encoding="utf-8")
    for title in ("What this does and doesn&rsquo;t give you",
                  "The portal showed you different sample code?"):
        html = re.sub(rf"<h2 id=\w+>{re.escape(title)}</h2>\s*<p>",
                      f'<div class="qs-note qs-note-info"><{title_tag} class=qs-note-title>{title}'
                      f"</{title_tag}><p>", html)
    html = html.replace("to api-eus.\n", "to api-eus.</div>\n").replace(
        "Qrypt SDK.\n", "Qrypt SDK.</div>\n")
    assert 'class="qs-note qs-note-info"' in html
    page.write_text(html, encoding="utf-8")
    return site


def test_selftest_content_blocks_accept_callout_headings(tmp_path):
    """F4: the `qs-note` shortcode titles a callout with a heading, and T-site-9 reads the
    whole callout from it."""
    check_content_blocks(_with_callouts(tmp_path, "h3"))


def test_selftest_content_blocks_reject_a_paragraph_title(tmp_path):
    """F4: a callout titled with a <p> is not accepted any more."""
    with pytest.raises(AssertionError, match="must be a heading"):
        check_content_blocks(_with_callouts(tmp_path, "p"))


def test_t_site_9_real_callouts_use_headings():
    """F4, on the real build: every qs-note on both pages has an h3 title."""
    for rel in OUR_PAGES:
        notes = [n for n in load(sitebuild.public(), rel).walk()
                 if "qs-note" in n.attrs.get("class", "").split()]
        for note in notes:
            titles = [c for c in note.children if isinstance(c, Node) and c.tag in HEADINGS]
            assert titles and titles[0].tag == "h3", f"{rel}: a callout has no h3 title: {note}"
