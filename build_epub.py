#!/usr/bin/env python3
"""Build an EPUB 3 from a Markdown manuscript.

Usage: build_epub.py BOOK_DIR SOURCE.md COVER.jpg OUTPUT.epub

BOOK_DIR holds what is specific to one book:
  book.json        title, author, series, series_position, identifier, description
                   (author is optional; title defaults to the Markdown's # heading)
  frontmatter/     optional ready-made XHTML pages, copied verbatim between the
                   cover and the Table of Contents in filename order; each page's
                   <title> becomes its navigation entry
  backmatter/      optional pages placed after the last chapter, same rules

Without frontmatter/, the opening pages are generated from the Markdown
preamble (everything before the first "## Chapter"):
  # Title / ## Subtitle   title page
  *italic paragraphs*     "About This Book" page
  > quote                 epigraph page; the line after a bare ">" is the attribution
  ## Contents + list      entries reused as the printed Table of Contents text

The chapter dialect:
  ## Chapter ...     one XHTML file per chapter
  *line*             italic lines/paragraphs right after a heading = bylines
  * * *              scene break
  ---                chapter separator (ignored)
  *x* / **x**        italic / bold
Layout and CSS mirror the v5 Infinite Extent EPUB.
"""
import html
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

CSS = """@charset "utf-8";
body { font-family: Georgia, "Iowan Old Style", serif; line-height: 1.4; margin: 0 6%; }

h1, h2, h3, h4 { font-weight: normal; page-break-after: avoid; }
h1 { text-align: center; font-variant: small-caps; font-size: 2em; margin: 2em 0 0.5em; }
h2 { text-align: center; font-variant: small-caps; font-size: 1.6em; line-height: 1.25;
     margin: 2.2em 0 1.6em; }
h3 { text-align: center; font-style: italic; font-size: 1.1em; margin: 0.4em 0; }
h4 { text-align: left; text-decoration: underline; font-size: 1.05em; margin: 1.3em 0 0.2em; }

p { margin: 0; }
hr { border: 0; border-top: 1px solid #999; width: 30%; margin: 2em auto; }

/* ---- chapters: retail-ebook body typography ---- */
body.chapter p { text-align: justify; text-indent: 1.3em; }
body.chapter p.byline { text-indent: 0; text-align: left; font-style: italic; margin: 0; }
body.chapter p.noindent { text-indent: 0; }
body.chapter p.opener { text-indent: 0; margin-top: 1.6em; }
body.chapter p.opener::first-letter {
  float: left; font-size: 3.2em; line-height: 0.82;
  padding: 0.02em 0.07em 0 0;
}
body.chapter p.break { text-align: center; margin: 2.4em 0;
                       letter-spacing: 1.1em; text-indent: 1.1em; font-size: 0.9em; }
body.chapter p.contact { text-indent: 0; text-align: left; margin-top: 1.2em; line-height: 1.6; }

/* ---- front matter ---- */
body.frontmatter p { text-align: center; text-indent: 0; margin: 0 0 0.9em; }
body.frontmatter ol { list-style: none; padding: 0; }
body.frontmatter ul { list-style: none; margin: 0 0 0 2.2em; padding: 0; text-align: left; }
body.frontmatter li { margin: 0.1em 0; }
body.frontmatter blockquote { margin: 2em 8%; font-style: italic; text-align: center; }
body.frontmatter p.contact { line-height: 1.6; }
body.frontmatter p.about { font-style: italic; }
body.frontmatter p.about:first-child { margin-top: 30%; }
body.frontmatter blockquote.poem { margin-top: 30%; text-align: left; }
body.frontmatter blockquote.poem p { text-align: left; margin: 0; }
body.frontmatter blockquote.poem p.attribution { text-align: right; font-style: normal;
                                                  margin-top: 1.2em; }

body.contents p { text-indent: 0; margin: 0 0 0.35em; }
body.contents p.backmatter { margin-top: 1.4em; }
body.contents a { text-decoration: none; }

body.titlepage { text-align: center; }
body.titlepage h1 { font-size: 2.3em; margin-top: 22%; letter-spacing: 0.02em; }
body.titlepage h3 { font-style: normal; font-variant: small-caps; font-size: 1.35em;
                    margin-top: 2.5em; }

/* ---- cover ---- */
body.cover { margin: 0; text-align: center; }
body.cover img { max-width: 100%; max-height: 100vh; }
"""

COVER_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# The (?<![\w*]) guard keeps "Sagittarius A*" from opening an italic span.
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
ITALIC_LINE = re.compile(r"\*[^*]+\*")


def inline(text):
    text = html.escape(text, quote=False)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    return ITALIC.sub(r"<em>\1</em>", text)


def page(title, body_class, body, epub_type=None):
    attr = f' epub:type="{epub_type}"' if epub_type else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
            ' lang="en" xml:lang="en">\n'
            f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body class="{body_class}"{attr}>\n{body}\n</body></html>\n')


def parse(md):
    """Return (preamble blocks, [(heading, [blocks])]); blocks are paragraph strings."""
    preamble, chapters = [], []
    for block in re.split(r"\n\s*\n", md.strip()):
        block = block.strip("\n")
        if block.strip() == "---":
            continue
        if block.startswith("## Chapter "):
            chapters.append((block[3:].strip(), []))
        elif chapters:
            chapters[-1][1].append(block)
        else:
            preamble.append(block)
    return preamble, chapters


def parse_preamble(blocks):
    """Split the preamble into title, subtitles, about paragraphs, epigraph, contents entries."""
    pre = {"title": None, "subtitles": [], "about": [], "epigraph": None, "contents": []}
    in_contents = False
    for block in blocks:
        if block.startswith("# "):
            pre["title"] = block[2:].strip()
        elif block.startswith("## "):
            in_contents = block[3:].strip().lower() == "contents"
            if not in_contents:
                pre["subtitles"].append(block[3:].strip())
        elif in_contents and block.lstrip().startswith("- "):
            pre["contents"] += [l.strip()[2:] for l in block.split("\n") if l.strip()]
        elif block.startswith(">"):
            lines = [re.sub(r"^>\s?", "", l).rstrip() for l in block.split("\n")]
            split = lines.index("") if "" in lines else len(lines)
            pre["epigraph"] = (lines[:split], [l for l in lines[split + 1:] if l])
        else:
            pre["about"].append(" ".join(block.split("\n")))
    return pre


def chapter_body(heading, blocks):
    # "Chapter One: Ruh-roh" -> two-line heading, as in v5.
    head = inline(heading).replace(": ", ":<br/>", 1)
    out = [f"<h2>{head}</h2>"]
    first = True
    after_break = False
    for block in blocks:
        lines = [l.strip() for l in block.split("\n")]
        if block.strip() == "* * *":
            out.append('<p class="break">⚜ ⚜ ⚜</p>')
            after_break = True
            continue
        # Bylines (POV, date, place) open each chapter, before any prose.
        if first and not after_break and all(ITALIC_LINE.fullmatch(l) for l in lines):
            out += [f'<p class="byline">{inline(l[1:-1])}</p>' for l in lines]
            continue
        text = inline(" ".join(lines))
        cls = ' class="opener"' if first else ' class="noindent"' if after_break else ""
        out.append(f"<p{cls}>{text}</p>")
        first = after_break = False
    return "\n".join(out)


def load_pages(directory):
    """Yield (href, id, toc label, content) for each XHTML page in directory, by filename."""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".xhtml"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            content = f.read()
        label = html.unescape(re.search(r"<title>(.*?)</title>", content).group(1))
        yield name, name[:-len(".xhtml")], label, content


def generated_frontmatter(pre, title, author):
    """Yield (href, id, toc label, content) for pages built from the Markdown preamble."""
    lines = [f"<h1>{html.escape(title)}</h1>"]
    lines += [f"<h3>{inline(s)}</h3>" for s in pre["subtitles"]]
    if author:
        lines.append(f"<h3>{html.escape(author)}</h3>")
    yield "title.xhtml", "titlepage", title, page(title, "titlepage", "\n".join(lines))
    if pre["about"]:
        body = "\n".join(f'<p class="about">{inline(p.strip())}</p>' for p in pre["about"])
        yield ("about.xhtml", "about", "About This Book",
               page("About This Book", "frontmatter", body))
    if pre["epigraph"]:
        verse, attribution = pre["epigraph"]
        body = "".join(f"<p>{inline(l)}</p>" for l in verse)
        body += "".join(f'<p class="attribution">{inline(l)}</p>' for l in attribution)
        yield ("epigraph.xhtml", "epigraph", "Epigraph",
               page("Epigraph", "frontmatter", f'<blockquote class="poem">{body}</blockquote>',
                    "epigraph"))


def build(book_dir, src, cover, dest):
    with open(os.path.join(book_dir, "book.json"), encoding="utf-8") as f:
        meta = json.load(f)
    with open(src, encoding="utf-8") as f:
        preamble, chapters = parse(f.read())
    pre = parse_preamble(preamble)
    title = meta.get("title") or pre["title"]
    author = meta.get("author")
    cover_ext = os.path.splitext(cover)[1].lower()
    if cover_ext not in COVER_TYPES:
        sys.exit(f"cover must be JPEG or PNG, got {cover_ext}")
    cover_href = "cover" + cover_ext

    files = {}  # href -> (id, content), in spine order
    files["cover.xhtml"] = ("cover-page", page("Cover", "cover",
                            f'<img src="{cover_href}" alt="Cover"/>', "cover"))
    toc = []
    front = list(load_pages(os.path.join(book_dir, "frontmatter")))
    if not front:
        front = list(generated_frontmatter(pre, title, author))
    for href, id_, label, content in front:
        files[href] = (id_, content)
        toc.append((href, label))

    # The printed contents page lists what follows it: chapters, then back matter.
    # A Contents list in the Markdown supplies richer entry text when it matches.
    files["contents.xhtml"] = None  # placeholder keeps its spine position
    toc.append(("contents.xhtml", "Table of Contents"))
    entries = pre["contents"] if len(pre["contents"]) == len(chapters) else None
    listed = []
    for n, (heading, blocks) in enumerate(chapters, 1):
        href = f"ch{n:02d}.xhtml"
        files[href] = (f"ch{n:02d}", page(heading, "chapter", chapter_body(heading, blocks)))
        listed.append((href, heading, inline(entries[n - 1] if entries else heading), ""))
    for i, (href, id_, label, content) in enumerate(
            load_pages(os.path.join(book_dir, "backmatter"))):
        files[href] = (id_, content)
        listed.append((href, label, html.escape(label), ' class="backmatter"' if i == 0 else ""))
    files["contents.xhtml"] = ("contents", page("Table of Contents", "contents",
        "<h2>Table of Contents</h2>\n" + "\n".join(
            f'<p{cls}><a href="{h}">{text}</a></p>' for h, _, text, cls in listed)))
    toc += [(h, label) for h, label, _, _ in listed]

    nav_items = "\n".join(f'<li><a href="{h}">{html.escape(t)}</a></li>' for h, t in toc)
    nav = page("Contents", "frontmatter",
               f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n{nav_items}\n</ol></nav>\n'
               '<nav epub:type="landmarks" hidden=""><ol>\n'
               '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>\n'
               '<li><a epub:type="toc" href="contents.xhtml">Table of Contents</a></li>\n'
               '<li><a epub:type="bodymatter" href="ch01.xhtml">Start</a></li>\n</ol></nav>')

    book_id = meta["identifier"]
    navpoints = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel>'
        f'<content src="{h}"/></navPoint>' for i, (h, t) in enumerate(toc, 1))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
           f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n'
           f'<docTitle><text>{html.escape(title)}</text></docTitle>\n'
           f'<navMap>\n{navpoints}\n</navMap>\n</ncx>\n')

    optional = ""
    if author:
        optional += f"\n    <dc:creator>{html.escape(author)}</dc:creator>"
    if meta.get("description"):
        optional += f"\n    <dc:description>{html.escape(meta['description'])}</dc:description>"
    if meta.get("series"):
        optional += (f'\n    <meta property="belongs-to-collection" id="series">'
                     f'{html.escape(meta["series"])}</meta>'
                     '\n    <meta refines="#series" property="collection-type">series</meta>')
        if meta.get("series_position"):
            optional += (f'\n    <meta refines="#series" property="group-position">'
                         f'{meta["series_position"]}</meta>')
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = "\n".join(
        f'    <item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for h, (i, _) in files.items())
    spine = "\n".join(f'    <itemref idref="{i}"/>' for i, _ in files.values())
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>{optional}
    <dc:language>en</dc:language>
    <meta name="cover" content="cover-image"/>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="cover-image" href="{cover_href}" media-type="{COVER_TYPES[cover_ext]}" properties="cover-image"/>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>
"""
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')

    with zipfile.ZipFile(dest, "w") as z:
        # mimetype must be first and stored uncompressed.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, zipfile.ZIP_DEFLATED)
        z.write(cover, f"OEBPS/{cover_href}", zipfile.ZIP_STORED)
        for href, (_, content) in files.items():
            z.writestr(f"OEBPS/{href}", content, zipfile.ZIP_DEFLATED)
    print(f"{dest}: {len(files)} pages, {len(chapters)} chapters")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    build(*sys.argv[1:])
