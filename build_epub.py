#!/usr/bin/env python3
"""Build an EPUB 3 from the edited Markdown transcript.

Usage: build_epub.py SOURCE.md COVER.jpg OUTPUT.epub [FRONTMATTER_DIR]

The Markdown dialect is the small one the transcript uses:
  # Title            book title (the italic line under it is the subtitle)
  ## Chapter ...     one XHTML file per chapter
  *line*␠␠           consecutive italic lines right after a heading = bylines
  * * *              scene break
  *x* / **x**        italic / bold
Layout and CSS mirror the v5 EPUB.

FRONTMATTER_DIR (default: frontmatter/ next to this script) holds ready-made
XHTML pages copied verbatim between the cover and chapter one, in filename
order; each page's <title> becomes its table-of-contents entry. Without it, a
title page is generated from the Markdown instead. A printed Table of Contents
follows the front matter, and pages in backmatter/ (next to FRONTMATTER_DIR)
come after the last chapter.
"""
import html
import os
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone

AUTHOR = "Dennis E. Taylor"
SERIES, SERIES_POS = "Bobiverse", 6
DESCRIPTION = ("Personal, non-commercial transcript produced by the owner from the "
               "Audible narration, for private reading only. Edited; not the publisher's text.")

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

/* ---- front matter ---- */
body.frontmatter p { text-align: center; text-indent: 0; margin: 0 0 0.9em; }
body.frontmatter ol { list-style: none; padding: 0; }
body.frontmatter ul { list-style: none; margin: 0 0 0 2.2em; padding: 0; text-align: left; }
body.frontmatter li { margin: 0.1em 0; }
body.frontmatter blockquote { margin: 2em 8%; font-style: italic; text-align: center; }
body.frontmatter p.contact { line-height: 1.6; }

body.contents p { text-indent: 0; margin: 0 0 0.35em; }
body.contents p.backmatter { margin-top: 1.4em; }
body.contents a { text-decoration: none; }

body.chapter p.contact { text-indent: 0; text-align: left; margin-top: 1.2em; line-height: 1.6; }

body.titlepage { text-align: center; }
body.titlepage h1 { font-size: 2.3em; margin-top: 22%; letter-spacing: 0.02em; }
body.titlepage h3 { font-style: normal; font-variant: small-caps; font-size: 1.35em;
                    margin-top: 2.5em; }

/* ---- cover ---- */
body.cover { margin: 0; text-align: center; }
body.cover img { max-width: 100%; max-height: 100vh; }
"""

BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# The (?<![\w*]) guard keeps "Sagittarius A*" from opening an italic span.
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")


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
    """Return (title, subtitle, [(heading, [blocks])]) where blocks are paragraph strings."""
    blocks = re.split(r"\n\s*\n", md.strip())
    title = subtitle = None
    chapters = []
    for block in blocks:
        if block.startswith("# "):
            title = block[2:].strip()
        elif block.startswith("## "):
            chapters.append((block[3:].strip(), []))
        elif not chapters:
            subtitle = block.strip().strip("*")
        else:
            chapters[-1][1].append(block)
    return title, subtitle, chapters


def chapter_body(heading, blocks):
    # "Chapter One: Ruh-roh" -> two-line heading, as in v5.
    head = inline(heading).replace(": ", ":<br/>", 1)
    out = [f"<h2>{head}</h2>"]
    first = True
    after_break = False
    for i, block in enumerate(blocks):
        lines = [l.rstrip() for l in block.split("\n")]
        if block.strip() == "* * *":
            out.append('<p class="break">⚜ ⚜ ⚜</p>')
            after_break = True
            continue
        if i == 0 and all(re.fullmatch(r"\*[^*]+\*", l) for l in lines):
            out += [f'<p class="byline">{inline(l[1:-1])}</p>' for l in lines]
            continue
        text = inline(" ".join(l.strip() for l in lines))
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


def build(src, cover, dest, frontmatter=None):
    with open(src, encoding="utf-8") as f:
        title, subtitle, chapters = parse(f.read())
    if frontmatter is None:
        frontmatter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontmatter")

    files = {}  # href -> (id, content)
    files["cover.xhtml"] = ("cover-page", page("Cover", "cover",
                            '<img src="cover.jpg" alt="Cover"/>', "cover"))
    toc = []
    for href, id_, label, content in load_pages(frontmatter):
        files[href] = (id_, content)
        toc.append((href, label))
    if not toc:
        files["title.xhtml"] = ("titlepage", page(title, "titlepage",
                                f"<h1>{inline(title)}</h1>\n<h3>{AUTHOR}</h3>"))
        toc.append(("title.xhtml", title))

    # The printed contents page lists what follows it: chapters, then back matter.
    files["contents.xhtml"] = None  # placeholder keeps its spine position
    toc.append(("contents.xhtml", "Table of Contents"))
    listed = []
    for n, (heading, blocks) in enumerate(chapters, 1):
        href = f"ch{n:02d}.xhtml"
        files[href] = (f"ch{n:02d}", page(heading, "chapter", chapter_body(heading, blocks)))
        listed.append((href, heading, ""))
    backmatter = os.path.join(os.path.dirname(os.path.abspath(frontmatter)), "backmatter")
    for i, (href, id_, label, content) in enumerate(load_pages(backmatter)):
        files[href] = (id_, content)
        listed.append((href, label, ' class="backmatter"' if i == 0 else ""))
    files["contents.xhtml"] = ("contents", page("Table of Contents", "contents",
        "<h2>Table of Contents</h2>\n" + "\n".join(
            f'<p{cls}><a href="{h}">{html.escape(t)}</a></p>' for h, t, cls in listed)))
    toc += [(h, t) for h, t, _ in listed]

    nav_items = "\n".join(f'<li><a href="{h}">{html.escape(t)}</a></li>' for h, t in toc)
    nav = page("Contents", "frontmatter",
               f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n{nav_items}\n</ol></nav>\n'
               '<nav epub:type="landmarks" hidden=""><ol>\n'
               '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>\n'
               '<li><a epub:type="toc" href="contents.xhtml">Table of Contents</a></li>\n'
               '<li><a epub:type="bodymatter" href="ch01.xhtml">Start</a></li>\n</ol></nav>')

    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, title + ' v6')}"
    navpoints = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel>'
        f'<content src="{h}"/></navPoint>' for i, (h, t) in enumerate(toc, 1))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
           f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n'
           f'<docTitle><text>{html.escape(title)}</text></docTitle>\n'
           f'<navMap>\n{navpoints}\n</navMap>\n</ncx>\n')

    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = "\n".join(
        f'    <item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for h, (i, _) in files.items())
    spine = "\n".join(f'    <itemref idref="{i}"/>' for i, _ in files.values())
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{AUTHOR}</dc:creator>
    <dc:description>{html.escape(DESCRIPTION)}</dc:description>
    <dc:language>en</dc:language>
    <meta property="belongs-to-collection" id="series">{SERIES}</meta>
    <meta refines="#series" property="collection-type">series</meta>
    <meta refines="#series" property="group-position">{SERIES_POS}</meta>
    <meta name="cover" content="cover-image"/>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
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
        z.write(cover, "OEBPS/cover.jpg", zipfile.ZIP_STORED)
        for href, (_, content) in files.items():
            z.writestr(f"OEBPS/{href}", content, zipfile.ZIP_DEFLATED)
    print(f"{dest}: {len(files)} pages, {len(chapters)} chapters")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    build(*sys.argv[1:])
