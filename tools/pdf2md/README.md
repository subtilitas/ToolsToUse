# pdf2md

`pdf2md.py` converts text-based PDF (Portable Document Format) files to Markdown.
It is a single file of about 5,800 lines.
It uses the Python standard library only and opens no network connection.

- Requirements: Python 3.8 or later. No third-party packages.
- Input: unencrypted PDF files with a text layer.
- Output: one Markdown file, or one Markdown file per page in folders that follow the document contents.
  Figures are written as SVG (Scalable Vector Graphics), PNG, JPEG or JPEG 2000 files.

## Usage

```sh
python pdf2md.py input.pdf                        # writes input.md and input_assets/
python pdf2md.py input.pdf -o out.md --assets figs --pages 1-3,7 --page-markers
python pdf2md.py input.pdf --split-pages          # writes input/index.md and
                                                  # input/<contents folders>/page_NNNN.md
python pdf2md.py input.pdf --split-pages --toc-depth 2 -o outdir
python pdf2md.py input.pdf --split-pages --flat   # page files without folders
```

| Option | Effect |
| --- | --- |
| `pdf` | Input PDF file. |
| `-o`, `--output PATH` | Output Markdown file, or output directory with `--split-pages`. Default: input name with `.md`, or input name without extension. |
| `--assets DIR` | Directory for figure files. Default: `<output stem>_assets`, or `<output directory>/_assets` with `--split-pages`. |
| `--split-pages` | One Markdown file per page, in folders that follow the PDF outline, plus `index.md`. |
| `--toc-depth N` | With `--split-pages`: outline levels that become folders. Default 3. 0 means no folders. |
| `--flat` | With `--split-pages`: no folders. Same as `--toc-depth 0`. |
| `--pages SEL` | Page selection, 1-based, for example `1-3,7`. |
| `--page-markers` | Insert `<!-- page N -->` comments. Not valid with `--split-pages`. |
| `--no-figures` | Skip table, diagram and image export. |
| `-q`, `--quiet` | No progress output on stderr. |

`--flat` and `--toc-depth` with any value need `--split-pages`; without it they exit with code 1.

An output path that is the input file, or a symbolic or hard link to it, is rejected with exit code 1.
The Markdown file, page files and `index.md` are checked before any file is written; figure files when they are written.
Every output file is written to a temporary file in the same directory (`.~` plus 8 hex digits plus `.tmp`) and moved into place with `os.replace()`.
On Windows, `os.replace()` is retried up to 5 times within about 1.5 s when a virus scanner or indexer holds the file.
A failed run keeps an existing file unchanged, and a symbolic link at an output path is replaced, not written through.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success. |
| 1 | Usage error (unknown option, invalid `--pages`, no selected page in the document, output path that is or links to the input) or a file system error. |
| 2 | The file cannot be parsed or is encrypted, or the conversion failed. The error is reported in one line, without a traceback. |
| 3 | The output was written but is incomplete. Each problem is printed on stderr, also with `-q`. |

A problem line names the page, the kind and the cause:

```text
pdf2md: page 1: unsupported: image 6 0 R: JBIG2Decode not exported
pdf2md: page 4: limit: page reached MAX_PAGE_CHARS (1000000 characters)
pdf2md: the output is incomplete
```

| Kind | Meaning |
| --- | --- |
| `unsupported` | A feature that is not handled, for example JBIG2 images, inline images, an unknown predefined CMap. |
| `malformed` | Damaged input, for example an unreadable cross-reference chain, a stream that cannot be decoded, a missing XObject. |
| `limit` | A resource limit (see below) cut the output. |
| `internal` | An unexpected exception in a content operator, Form XObject, font, annotation, image or page. The rest of the page is kept; a failing page is kept empty. |

Identical problems are listed once with a repeat count.
At most 1000 distinct problems (`MAX_PROBLEMS`) are listed.

## Output

- Paragraphs, headings (`#` to `####`, ranked by font size), bullet and numbered list items, bold and italic spans, and fenced code blocks for monospace text.
- Reading order follows text columns.
  A gutter of at least 0.9 em between columns of at least 3 rows each splits the page into columns.
  At most 25 % of the text fragments may cross the gutter.
  Titles, full-width figures and footers that cross the gutter separate the columns vertically.
- A paragraph that continues in the next column or on the next page is joined when the next line starts lower-case and the previous line has no closing punctuation.
- Right-to-left text (Arabic, Hebrew) is written in logical order.
  Ligatures and Arabic presentation forms are replaced by their base characters (Unicode normalization form NFKC).
- Vector graphics (ruled tables, diagrams, charts) become SVG files that also contain the text inside their area.
  Each figure is linked from the Markdown at its position in reading order.
  Frames and fills that span a whole text box (boxed notes, form fields) stay text.
- A figure that is a single raster image placed upright (positive x and y scale, no shear) is written as PNG, JPEG or JPEG 2000.
  An image that is rotated, mirrored or sheared, also by the page `/Rotate`, is written as an SVG that embeds the PNG or JPEG data and applies the placement matrix.
  A JPEG 2000 image is always written as its own file; a rotation, mirroring or shear of it is reported as a problem.
- Image `/Decode` arrays are applied per component.
  A 16-bit RGB image with a `/Decode` other than `[0 1]` or `[1 0]` per component is written with 8 bits per component.
- Text of visible annotation appearances (filled-in form fields, free text, stamps) is included.
  Their borders and backgrounds are not.
- Pages with `/Rotate` are read as displayed.
  When most text on a rotated page is not upright as displayed, the page is read unrotated.
- Lines in the top or bottom 10 % of the page box that repeat (digits ignored) on at least 50 % of the pages, minimum 3 pages, are page headers or footers and are dropped.
- A figure is dropped only when it lies wholly inside one of these bands and the same figure (same rounded position, same shapes and image data, same text with digits ignored) occurs there on that many pages.
  Figures elsewhere, including full-page scans, are kept.
- Objects are read through the cross-reference chain from the last `startxref` (tables, cross-reference streams, `/XRefStm`, `/Prev`).
  The newest revision wins, free entries are ignored, and a reference with a different generation number is null.
  In a hybrid file, an `/XRefStm` entry replaces a free entry of the same section's table.
  When the chain is unreadable, objects are recovered by scanning the file; this is reported as a problem unless only the byte offsets have shifted.

### One file per page (`--split-pages`)

- Each page becomes `page_NNNN.md`. `index.md` lists the contents with links.
  Figures go to the folder `_assets` inside the output directory.
- Each contents entry down to `--toc-depth` levels (default 3) is a folder named `NN Title`.
- The contents come from the document outline (bookmarks).
  Without an outline, a printed table of contents in the first 20 to 60 pages is used.
- A page goes into the folder of the first entry that starts on it, descending into children that start on the same page.
  Otherwise it goes into the folder of the entry still running.
- Page file paths relative to the output directory stay within 160 characters.
- Existing files in the output directory are overwritten. Other files are left in place.

### Markdown escaping

Text that Markdown would read as syntax is escaped:

- `*`, backtick, backslash, and underscores not between letters or digits.
- `](`, so no inline links or images appear.
- Entity references, and `<` before a letter, `/`, `!` or `?`.
- At the start of a paragraph or list item: a heading, quote, bullet, numbered marker, thematic break, `~~~` fence or `[label]:` definition.

Code blocks use a fence longer than any backtick run inside.

## Resource limits

Hostile or damaged files are bounded by these limits.
Output of a file that hits one is incomplete.

| Limit | Value |
| --- | --- |
| One decoded stream (`MAX_DECODED`) | 256 MiB, checked after every filter stage and inside the Flate, LZW, ASCII85, ASCIIHex, RunLength and predictor decoders. Peak memory for such a stream is about twice that. |
| Decoded-stream cache per document (`MAX_DOC_DECODED`) | 1 GiB. Later streams are decoded again on each use. |
| LZW dictionary (`MAX_LZW_TABLE`) | 4096 entries |
| Image side (`MAX_IMAGE_SIDE`) | 65,536 pixels, checked on the declared size before decoding, also for JPEG and JPEG 2000 |
| Image size (`MAX_IMAGE_PIXELS`) | 2^26 pixels. The declared size may be at most twice the stored data plus 64 KiB. Other images are not exported. |
| Lab color cache per image (`MAX_LAB_MEMO`) | 65,536 entries |
| Content stream operators per page (`MAX_PAGE_OPS`) | 5,000,000 |
| Form XObject runs per page (`MAX_FORM_RUNS`) | 2000 |
| Form XObject nesting | 12 levels |
| Characters per page (`MAX_PAGE_CHARS`) | 1,000,000 |
| Entries per cross-reference stream (`MAX_XREF_ENTRIES`) | 8,388,607, and at most `/Size` and the file length in bytes |
| Character codes shown per page (`MAX_PAGE_CODES`) | 2,000,000, including codes that produce no character |
| Grid cells per box in figure clustering (`MAX_GRID_CELLS`, `GRID_LEVELS`) | 1024 cells on the lowest of 8 grid levels (cells of 64 pt x 32^k) where the box fits |
| Entries per font table (`MAX_FONT_ENTRIES`) | 2^20, for ToUnicode and encoding CMap codes, `W` and `Widths`, `Differences`, `CIDToGIDMap` |
| ToUnicode text per font (`MAX_FONT_TEXT`) | 2^22 characters, counted over every insertion |
| One ToUnicode destination (`MAX_CMAP_DEST`) | 64 characters |
| Character codes per shown string (`MAX_SHOW_CODES`) | 2^20 |
| Nesting of arrays and dictionaries (`MAX_NESTING`) | 256 levels. Deeper objects are skipped. |
| Text outside the page box | Dropped beyond 10 % of the page size. |
| Page file path with `--split-pages` (`MAX_REL_PATH`) | 160 characters |

## Limitations

- Encrypted PDF files are rejected.
- No optical character recognition (OCR). A page without a text layer yields only its page image.
- Column detection needs at least 3 rows per column.
  Layouts that change the number of columns within one page section, and columns shorter than 3 rows, are read top to bottom across the columns.
- Tables without ruled lines or shading are output as text lines.
- A printed table of contents is found only on the pages being converted (`--pages`).
  Its entries need a page number after a dot leader or after a gap of at least 1.5 em.
  Contents titles are shortened to 48 characters for folder names.
  Paragraphs that span pages are split between page files.
- Text in fonts without a ToUnicode map is decoded from the font encoding, the built-in encoding of an embedded Type 1 program, the Symbol, ZapfDingbats or Wingdings tables, or the cmap table of an embedded TrueType program (character identifier (CID) fonts).
  Fonts that name glyphs only by number (`g18`, `g152`) and have none of these decode to wrong characters.
  Unmappable CID glyphs are written as U+FFFD.
- Predefined CMaps of composite fonts are read with the code space of their family (Identity, ISO-2022 including 78 and GBT, UCS2, UTF16, UTF32, RKSJ, EUC, GBK, GBK2K, Big Five, UHC, Johab, one-byte CMaps).
  The code-space tables are family approximations, not the Adobe CMap files; Johab uses the UHC ranges.
  The predefined CID-to-Unicode mappings are not included, so text comes from ToUnicode or the embedded TrueType cmap.
  UTF8 and other unknown CMap names use the ToUnicode code space, or two-byte codes with an `unsupported` problem when there is none.
- Vertical writing mode (Identity-V) is read as horizontal text.
- Type 3 glyph size is taken from the font size. The glyph space em is unknown.
- Fonts without a `/Widths` array use built-in widths for codes 32-126 of Helvetica, Arial, Times and Courier.
  Other such fonts use 0.5 em per glyph.
  Word gaps in that text can be misdetected, and its SVG text is not width-fitted.
- Rotated text outside figures is dropped.
- Images with CCITTFax or JBIG2 encoding, inline images (`BI`/`ID`/`EI`), and Indexed images with a `/Decode` other than the default or `[2^bits-1 0]` are not exported and are reported.
  Inline images are reported only when figures are exported (no `--no-figures`), outside annotation appearances, and when placed at 1 pt or more.
  Inline images in a header or footer band are reported too.
- Soft-mask transparency (`/SMask`) is ignored without a report. The image is exported opaque.
- Lab colors and images are converted to sRGB without chromatic adaptation. Colors with a D50 white point are slightly shifted.
- SVG text uses generic font families, stretched to the original run width.
- When the cross-reference chain has one bad offset, the whole file is read by scanning.
  The scan does not apply free entries, so an object freed in a later revision can still be read; this case is reported as a problem.
- Output files are not flushed with `fsync`. Their state after a power loss is untested.

## Tests

`test_pdf2md.py` holds 90 regression tests.
Each test builds the PDF it needs in a temporary directory.
They need no network access.
Tests that render Markdown with pandoc are skipped when pandoc is not installed.

```sh
python test_pdf2md.py                          # tests pdf2md.py in this folder
python test_pdf2md.py -v
PDF2MD=/path/to/pdf2md.py python test_pdf2md.py
```

## API documentation

The source is documented with Doxygen comments.
The module docstring of `pdf2md.py` is the reference for the behavior listed here.

```sh
doxygen Doxyfile    # writes doxygen/html/index.html and doxygen_warnings.txt
```

The Docs workflow fails when `doxygen_warnings.txt` is not empty.
The HTML is attached to each release as `pdf2md-<version>-docs.zip`.
