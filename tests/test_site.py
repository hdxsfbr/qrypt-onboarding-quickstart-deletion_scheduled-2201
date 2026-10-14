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
        data = squash(data)
        if data:
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
            selected = tab.attrs.get("aria-selected")
            assert selected in ("true", "false"), f"aria-selected={selected!r} on {tab}"
            assert tab.attrs.get("tabindex") == ("0" if selected == "true" else "-1"), \
                f"roving tabindex: {tab}"
            target = tab.attrs.get("aria-controls")
            assert target in ids, f"aria-controls={target!r} matches no element id"
            panel = ids[target][0]
            assert panel.attrs.get("role") == "tabpanel", f"#{target} is not role=tabpanel"
            seen_panels.add(target)
    panels = find(page, role="tabpanel")
    assert {p.attrs.get("id") for p in panels} == seen_panels, \
        "every role=tabpanel must be controlled by exactly one tab"
    for panel in panels:
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


def check_script_budget(public):
    for rel in OUR_PAGES:
        size = sum(len(s.encode()) for s in inline_scripts(load(public, rel)))
        assert size < INLINE_SCRIPT_BUDGET, f"{rel}: inline JS is {size} B, budget is < 2 KB (W3)"


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
                value = style_of(node).get("color")
                if value is None:
                    continue
                fg = parse_color(value)
                assert fg is not None, f"{rel}: cannot evaluate color {value!r} in a code block"
                colored += 1
                ratio = contrast(fg, bg)
                assert ratio >= MIN_CONTRAST, \
                    f"{rel}: token color {value} on background {bg} is {ratio:.2f}:1 (< 4.5)"
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
    """Text of the block titled `phrase`: a heading's section, or a whole callout whose title
    (a heading, or any element whose text starts with the phrase) sits at its top."""
    for h in (n for n in page.walk() if n.tag in HEADINGS):
        if squash(h.text()).lower().startswith(phrase):
            return section_text(page, h)
    for node in page.walk():
        starts = squash(node.text()).lower().startswith(phrase)
        deeper = any(isinstance(c, Node) and squash(c.text()).lower().startswith(phrase)
                     for c in node.children)
        parent = node.parent
        if starts and not deeper and parent is not None and parent.tag != "#root" \
                and CALLOUT_CLASS.search(parent.attrs.get("class", "")):
            return squash(parent.text())
    raise AssertionError(f"no heading or callout is titled {phrase!r}")


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


def test_t_site_6_tabs_markup():
    check_tabs(sitebuild.public())


def test_t_site_6_inline_script_budget():
    check_script_budget(sitebuild.public())


def test_t_site_7_copy_buttons():
    check_copy_buttons(sitebuild.public())


def test_t_site_8_code_contrast():
    check_code_contrast(sitebuild.public())


def test_t_site_9_content_blocks_and_banned_words():
    check_content_blocks(sitebuild.public())


def test_t_site_10_llms_txt():
    check_llms_txt(sitebuild.public())


# --- self-tests: the checks against generated fixture output ----------------------------


def highlight(code, color="#0a3069", background="#f7f7f7"):
    from html import escape
    lines = "".join(f"<span style=display:flex><span><span style=color:{color}>"
                    f"{escape(ln, quote=True)}</span>\n</span></span>"
                    for ln in code.rstrip("\n").split("\n"))
    return (f"<pre tabindex=0 style=background-color:{background};tab-size:4>"
            f"<code>{lines}</code></pre>")


def build_fixture(dest, **break_it):
    """A small good site (tests/fixtures/site/ plus the real snippet files); keyword flags
    apply one defect each."""
    dest = Path(dest)
    (dest / "quickstart" / "troubleshooting").mkdir(parents=True)
    (dest / "quickstart" / "files").mkdir()
    code = {lang: (REPO / "quickstart" / rel).read_text(encoding="utf-8")
            for lang, rel in SNIPPET_FILES.items()}
    color = "#cccccc" if break_it.get("low_contrast") else "#0a3069"
    page = (FIXTURES / "quickstart.html").read_text(encoding="utf-8")
    page = page.replace("@@NODE@@", highlight(code["node"], color))
    page = page.replace("@@PYTHON@@", highlight(code["python"], color))
    if break_it.get("edited_snippet"):
        page = page.replace("fetch(", "fetch (")
    for needle, repl in {
        "no_aria_label": ('aria-label="Copy Node.js code"', ""),
        "no_live_region": (" aria-live=polite", ""),
        "two_selected": ("aria-selected=false", "aria-selected=true"),
        "no_controls": ("aria-controls=panel-python", ""),
        "hidden_panel": ("<div role=tabpanel id=panel-python", "<div hidden role=tabpanel id=panel-python"),
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
               check_script_budget, check_copy_buttons, check_code_contrast, check_content_blocks,
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
    (check_copy_buttons, "no_aria_label", "aria-label"),
    (check_copy_buttons, "no_live_region", "aria-live"),
    (check_code_contrast, "low_contrast", "1."),
    (check_content_blocks, "banned", "banned"),
    (check_content_blocks, "no_tls", "TLS"),
    (check_content_blocks, "no_benchmark_link", "benchmarks.md"),
    (check_llms_txt, "llms_edit", "verbatim"),
]


@pytest.mark.parametrize("check,flag,expect", BREAKS, ids=lambda v: v if isinstance(v, str)
                         else getattr(v, "__name__", str(v)))
def test_selftest_each_check_can_fail(check, flag, expect, tmp_path):
    site = build_fixture(tmp_path / "public", **{flag: True, "drop_anchor": "429"
                                                 if flag == "drop_anchor" else None})
    with pytest.raises(AssertionError, match=re.escape(expect)):
        check(site)


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


def test_selftest_shell_diff_detects_missing_and_extra_pages(tmp_path):
    ours, up = _write_pair(tmp_path, MENU_UPSTREAM, MENU_OURS)
    (ours / "stray.html").write_text("<p>x")
    with pytest.raises(AssertionError, match="not in upstream"):
        check_shell_unchanged(ours, up)
    (ours / "stray.html").unlink()
    (up / "gone.html").write_text("<p>y")
    with pytest.raises(AssertionError, match="missing from the build"):
        check_shell_unchanged(ours, up)


def test_selftest_content_blocks_accept_callout_titles(tmp_path):
    """The `qs-note` shortcode titles a callout with a <p class=t>, not a heading."""
    site = build_fixture(tmp_path / "public")
    page = site / QUICKSTART
    html = page.read_text(encoding="utf-8")
    for title in ("What this does and doesn&rsquo;t give you",
                  "The portal showed you different sample code?"):
        html = re.sub(rf"<h2 id=\w+>{re.escape(title)}</h2>\s*<p>",
                      f'<div class="qs-note info"><p class=t>{title}</p><p>', html)
    html = html.replace("to api-eus.\n", "to api-eus.</div>\n").replace(
        "Qrypt SDK.\n", "Qrypt SDK.</div>\n")
    assert 'class="qs-note info"' in html
    page.write_text(html, encoding="utf-8")
    check_content_blocks(site)
