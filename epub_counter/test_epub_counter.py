import zipfile
from pathlib import Path

from epub_counter import html_to_text, main, read_epub

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>We Are Legion</dc:title></metadata>
  <manifest>
    <item id="c2" href="Text/ch%202.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

CH1 = """<html><head><title>ignored</title><style>p{}</style></head>
<body>
    <h1>Chapter 1</h1>
    <p>I am   Bob.</p>
    <p>Caf&eacute; &amp; replicants.</p>
</body></html>"""

CH2 = "<html><body><p>Chapter two.</p></body></html>"


def make_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/Text/ch1.xhtml", CH1)
        zf.writestr("OEBPS/Text/ch 2.xhtml", CH2)
        zf.writestr("OEBPS/style.css", "p { color: red }")
    return path


def test_reads_spine_in_order_and_strips_markup(tmp_path):
    book = read_epub(make_epub(tmp_path / "b1.epub"))
    assert book.title == "We Are Legion"
    assert book.text == "Chapter 1\n\nI am Bob.\n\nCafé & replicants.\n\nChapter two."
    assert book.chars == len(book.text)
    assert book.words == 10


def test_html_to_text_skips_head_and_scripts():
    assert html_to_text("<head><title>x</title></head><script>y()</script><p>z</p>") == "z"


def test_small_books_fit(tmp_path, capsys):
    epub = make_epub(tmp_path / "b1.epub")
    assert main([str(epub), str(epub)]) == 0
    out = capsys.readouterr().out
    assert "TOTAL" in out and "FITS" in out


def test_verdict_does_not_fit(tmp_path, capsys):
    epub = make_epub(tmp_path / "b1.epub")
    assert main([str(epub), "--context", "10", "--output-reserve", "0"]) == 1
    assert "DOES NOT FIT" in capsys.readouterr().out


def test_dump_dir(tmp_path):
    epub = make_epub(tmp_path / "b1.epub")
    main([str(epub), "--dump-dir", str(tmp_path / "out")])
    assert (tmp_path / "out" / "b1.txt").read_text().startswith("Chapter 1")
