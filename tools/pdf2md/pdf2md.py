#!/usr/bin/env python3
"""!
@file pdf2md.py
@brief Convert text-based PDF files to Markdown. Python standard library only.

@par Usage
@code{.sh}
python pdf2md.py input.pdf                       # writes input.md and input_assets/
python pdf2md.py input.pdf -o out.md --assets figs --pages 1-3,7 --page-markers
python pdf2md.py input.pdf --split-pages         # writes input/index.md and
                                                 # input/<contents folders>/page_NNNN.md
python pdf2md.py input.pdf --split-pages --toc-depth 2 -o outdir
python pdf2md.py input.pdf --split-pages --flat  # page files without folders
@endcode

@par Output
- Paragraphs, headings (# to ####, ranked by font size), bullet and numbered
  list items, bold and italic spans, and fenced code blocks for monospace text.
- Reading order follows text columns. A gutter of at least 0.9 em between
  columns with at least 3 rows each, crossed by at most 25 % of the text
  fragments, splits the page into columns. Titles, full-width figures and
  footers that cross the gutter separate the columns vertically. A paragraph
  that continues in the next column or on the next page is joined when the
  next line starts lower-case and the previous line has no closing punctuation.
- Right-to-left text (Arabic, Hebrew) is written in logical order. Ligatures
  and Arabic presentation forms are replaced by their base characters
  (Unicode normalization form NFKC).
- Vector graphics (ruled tables, diagrams, charts) become SVG files that also
  contain the text inside their area. Raster images become PNG, JPEG or
  JPEG 2000 files. Each figure is linked from the Markdown at its position in
  reading order. Frames and fills that span a whole text box (boxed notes,
  form fields) stay text.
- Text of visible annotation appearances (filled-in form fields, free text,
  stamps) is included. Their borders and backgrounds are not.
- Pages with /Rotate are read as displayed. When most text on a rotated page
  is not upright as displayed, the page is read unrotated.
- Lines and figures that repeat at the same place on at least 50 % of the
  pages (minimum 3 pages) are treated as page headers or footers and dropped.

@par One file per page (--split-pages)
- Each page becomes page_NNNN.md. index.md lists the contents with links.
  Figures go to the folder _assets inside the output directory.
- Folder structure: each contents entry down to --toc-depth levels
  (default 3) is a folder named "NN Title". The contents come from the
  document outline (bookmarks). Without an outline, a printed table of
  contents in the first 20 to 60 pages is used (see printed_toc()).
- A page goes into the folder of the first entry that starts on it,
  descending into children that start on the same page, else into the folder
  of the entry still running.
- Existing files in the output directory are overwritten. Other files are
  left in place.

@par Requirements
Python 3.8 or later. No third-party packages. No network access.

@par Exit codes
- 0: success.
- 1: usage error (unknown option, invalid --pages, no selected page in the
  document, output path equal to the input) or a file system error.
- 2: the file cannot be parsed or is encrypted, or the conversion failed
  (reported in one line, without a traceback).
- 3: the output was written but is incomplete. Each problem is printed on stderr
  as one line with page number, kind and cause, also with -q (see problem()).

@par Resource limits
Hostile or damaged files are bounded by these limits; output of a file that
hits one is incomplete.
- One decoded stream: MAX_DECODED = 256 MiB. Peak memory for such a stream is
  about twice that.
- Images: at most MAX_IMAGE_PIXELS = 2^26 pixels, and a declared size of at most
  twice the stored data plus 64 KiB; others are not exported.
- One page: MAX_PAGE_OPS = 5,000,000 content stream operators and
  MAX_FORM_RUNS = 2000 Form XObject runs.
- One font: MAX_FONT_ENTRIES = 2^20 code, width and glyph table entries.
- Arrays and dictionaries nested deeper than MAX_NESTING = 256 levels are skipped.
- Text more than 10 % of the page size outside the page box is dropped.
- With --split-pages, page file paths relative to the output directory stay
  within MAX_REL_PATH = 160 characters.

@par Markdown escaping
Text that Markdown would read as syntax is escaped: *, backtick, backslash and underscores not
between letters or digits, "](" (no inline links or images), entity references,
"<" before a letter, / ! or ?, and at the start of a paragraph or list item a
heading, quote, bullet, numbered marker, thematic break, ~~~ fence or "[label]:"
definition. Code blocks use a fence longer than any backtick run inside.

@par Limitations
- Encrypted PDF files are rejected.
- No optical character recognition (OCR). A page without a text layer yields
  only its page image.
- Column detection needs at least 3 rows per column. Layouts that change the
  number of columns within one page section, and columns shorter than 3 rows,
  are read top to bottom across the columns.
- Tables without ruled lines or shading are output as text lines.
- A printed table of contents is found only on the pages being converted
  (--pages). Its entries need a page number after a dot leader or after a gap
  of at least 1.5 em. Contents titles are shortened to 48 characters for
  folder names. Paragraphs that span pages are split between page files.
- Text in fonts without a ToUnicode map is decoded from the font encoding, the
  built-in encoding of an embedded Type 1 program, the Symbol, ZapfDingbats or
  Wingdings tables, or the cmap table of an embedded TrueType program
  (character identifier (CID) fonts). Fonts that name glyphs only by number
  (g18, g152) and have none of these decode to wrong characters. Unmappable
  CID glyphs are written as U+FFFD.
- Vertical writing mode (Identity-V) is read as horizontal text.
- Type 3 glyph size is taken from the font size. The glyph space em is unknown.
- Fonts without a /Widths array use built-in widths for codes 32-126 of
  Helvetica, Arial, Times and Courier. Other such fonts use 0.5 em per glyph.
  Word gaps in that text can be misdetected and its SVG text is not
  width-fitted.
- Rotated text outside figures is dropped.
- Images with CCITTFax or JBIG2 encoding, inline images (BI/ID/EI) and
  soft-mask transparency are not exported. Lab colors and images are converted
  to sRGB without chromatic adaptation, so colors with a D50 white point are
  slightly shifted.
- SVG text uses generic font families, stretched to the original run width.
"""

import argparse
import base64
import math
import os
import re
import struct
import sys
import unicodedata
import zlib
from collections import Counter

# ---------------------------------------------------------------------------
# Lexer and object model
# ---------------------------------------------------------------------------

## @brief Pattern for a \#xx hexadecimal escape inside a PDF name.
_HEXNAME_RE = re.compile(rb'#([0-9A-Fa-f]{2})')
## @brief Pattern that a whole token must match to be a PDF number.
##
## Optional sign, then digits with an optional fraction, or a leading-dot fraction.
_NUM_RE = re.compile(rb'[+-]?(?:\d+\.?\d*|\.\d+)$')
## @brief Pattern that matches one PDF token after leading whitespace and comments.
##
## Group 1: name text after /. Group 2: regular token (number or keyword).
## Group 3: the delimiters << >> [ ] { } ) >. Group 4: ( or < that opens a string.
## No group matches at the end of the data.
_TOK_RE = re.compile(
    rb'(?:[ \t\r\n\x0c\x00]+|%[^\r\n]*)*'
    rb'(?:/([^ \t\r\n\x0c\x00()<>\[\]{}/%]*)'
    rb'|([^ \t\r\n\x0c\x00()<>\[\]{}/%]+)'
    rb'|(<<|>>|[\[\]{})>])'
    rb'|([(<]))?'
)
## @brief Cache of Op objects keyed by keyword bytes, filled by Lexer.token.
##
## Only keywords of at most 9 bytes are stored, up to 4096 entries.
_OPS = {}
## @brief Upper limit for the length of a page file path relative to the output
## directory with --split-pages; deeper folder names are shortened or not created.
MAX_REL_PATH = 160
## @brief Arrays and dictionaries nested deeper than this are skipped (parsed as None).
MAX_NESTING = 256
## @brief Upper limit in bytes for one decoded stream. Longer output is cut off.
MAX_DECODED = 1 << 28
## @brief Upper limit for code, width and glyph table entries built for one font.
MAX_FONT_ENTRIES = 1 << 20
## @brief Upper limit for the characters stored in the ToUnicode values of one font
## (4194304 characters, counted over every insertion).
MAX_FONT_TEXT = 1 << 22
## @brief Upper limit in characters for one ToUnicode destination string (64); longer
## destinations are truncated.
MAX_CMAP_DEST = 64
## @brief Upper limit for the character codes taken from one shown string operand
## (1048576 codes); further codes are dropped.
MAX_SHOW_CODES = 1 << 20
## @brief Upper limit in pixels for the declared width and for the height of one image
## (65,536); larger images are not exported.
MAX_IMAGE_SIDE = 1 << 16
## @brief Upper limit for distinct colors cached while converting one Lab image
## (65,536 entries); the cache is emptied when it is full.
MAX_LAB_MEMO = 1 << 16
## @brief Upper limit for Form XObject runs on one page, including nested forms.
MAX_FORM_RUNS = 2000
## @brief Upper limit for content stream operators executed for one page.
MAX_PAGE_OPS = 5000000
## @brief Upper limit for distinct entries kept in PROBLEMS; further ones are only counted.
MAX_PROBLEMS = 1000
## @brief Problems that made the output of the running conversion incomplete.
##
## Maps (page number or None, kind, text) to the number of occurrences, in insertion
## order. kind is 'unsupported', 'malformed', 'limit' or 'internal'. analyze() clears
## it; main() prints it and returns exit code 3 when it is not empty.
PROBLEMS = {}
## @brief One-element list with the page number that problem() attaches; None outside
## a page.
_CURRENT_PAGE = [None]


def problem(kind, text):
    """!
    @brief Record a problem that makes the output incomplete.

    Identical entries are counted once with a repeat count. After MAX_PROBLEMS
    distinct entries, new ones go to a single overflow entry.
    @param kind 'unsupported' (feature not handled), 'malformed' (damaged input),
           'limit' (a MAX_* limit cut the output) or 'internal' (unexpected exception)
    @param text one line that names the object and the cause, e.g. 'image 12 0 R: JBIG2Decode'
    """
    key = (_CURRENT_PAGE[0], kind, text)
    if key not in PROBLEMS and len(PROBLEMS) >= MAX_PROBLEMS:
        key = (None, 'limit', 'more problems not listed')
    PROBLEMS[key] = PROBLEMS.get(key, 0) + 1


def problem_lines():
    """!
    @brief Format PROBLEMS for stderr.
    @return list of lines such as 'page 3: unsupported: image 12 0 R: JBIG2Decode (x2)'
    """
    out = []
    for (page, kind, text), n in PROBLEMS.items():
        where = 'page %d: ' % page if page is not None else ''
        out.append('%s%s: %s%s' % (where, kind, text, ' (x%d)' % n if n > 1 else ''))
    return out


## @brief Pattern for the EI keyword that ends inline image data.
##
## EI must follow a whitespace byte and be followed by whitespace or the end of data.
_EI_RE = re.compile(rb'[ \t\r\n\x0c\x00]EI(?=[ \t\r\n\x0c\x00]|$)')


class Name(str):
    """!
    @brief PDF name object.

    A str subclass holding the name without the leading /.
    """


class Op(str):
    """!
    @brief Keyword or content-stream operator.

    A str subclass. Delimiters such as [ and << are also returned as Op.
    """


class Ref:
    """!
    @brief Indirect object reference "num gen R".

    @par Attributes
    - num: object number.
    - gen: generation number.
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = ('num', 'gen')

    def __init__(self, num, gen):
        """!
        @brief Store the object and generation numbers.
        @param num object number.
        @param gen generation number.
        """
        ## object number.
        self.num = num
        ## generation number.
        self.gen = gen


class Stream:
    """!
    @brief PDF stream object: its dictionary and the undecoded data.

    @par Attributes
    - dict: the stream dictionary.
    - raw: the stream bytes as stored in the file, still encoded.
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = ('dict', 'raw')

    def __init__(self, dct, raw):
        """!
        @brief Store the stream dictionary and its raw bytes.
        @param dct stream dictionary.
        @param raw undecoded stream bytes.
        """
        ## the stream dictionary.
        self.dict = dct
        ## the stream bytes as stored in the file, still encoded.
        self.raw = raw

    def get(self, key, default=None):
        """!
        @brief Look up a key in the stream dictionary.
        @param key dictionary key.
        @param default value returned when the key is missing.
        @return the stored value or default.
        """
        return self.dict.get(key, default)


class UsageError(Exception):
    """!
    @brief Raised for an invalid request, such as a page selection outside the document.
    main() reports it with exit code 1.
    """


class PDFError(Exception):
    """!
    @brief Error raised for a file or stream that cannot be parsed.
    """

    pass


class Lexer:
    """!
    @brief Tokenizer and object parser for PDF bytes.

    @par Attributes
    - d: the bytes being read.
    - p: current read position.
    - n: length of d.
    """

    def __init__(self, data, pos=0):
        """!
        @brief Start reading data at a position.
        @param data bytes to tokenize.
        @param pos start offset in data.
        """
        ## the bytes being read.
        self.d = data
        ## current read position.
        self.p = pos
        ## length of d.
        self.n = len(data)

    def token(self):
        """!
        @brief Read the next token and advance the position.

        Numbers become int, or float when they contain a dot.
        A token of only sign, digit and dot characters that is not a valid number
        (for example --5 or 1.2.3) becomes 0.0.
        Names have @#xx escapes decoded and become Name. Other regular tokens and
        delimiters become Op. Literal strings and hex strings become bytes.
        A hex string with an odd digit count is padded with 0; non-hex bytes are ignored.
        @return int, float, Name, Op or bytes, or None at the end of the data.
        """
        d = self.d
        m = _TOK_RE.match(d, self.p)
        g = m.lastindex
        if g is None:
            self.p = m.end()
            return None
        if g == 2:  # number or keyword
            tok = m.group(2)
            self.p = m.end()
            c = tok[0]
            if 0x30 <= c <= 0x39 or c in b'+-.':
                if _NUM_RE.match(tok):
                    return float(tok) if b'.' in tok else int(tok)
                if re.fullmatch(rb'[+\-.\d]+', tok):
                    return 0.0  # malformed number such as "--5" or "1.2.3"
            op = _OPS.get(tok)
            if op is None:
                op = Op(tok.decode('latin-1'))
                if len(tok) <= 9 and len(_OPS) < 4096:
                    _OPS[tok] = op
            return op
        if g == 1:  # /Name
            raw = m.group(1)
            self.p = m.end()
            if b'#' in raw:
                raw = _HEXNAME_RE.sub(lambda mm: bytes([int(mm.group(1), 16)]), raw)
            return Name(raw.decode('latin-1'))
        if g == 3:
            self.p = m.end()
            return Op(m.group(3).decode('latin-1'))
        p = m.start(4)
        self.p = p
        if d[p] == 0x28:  # (
            return self._literal()
        q = d.find(b'>', p)  # <hex string>
        if q < 0:
            q = self.n
        hx = re.sub(rb'[^0-9A-Fa-f]', b'', d[p + 1 : q])
        if len(hx) % 2:
            hx += b'0'
        self.p = q + 1
        return bytes.fromhex(hx.decode('ascii'))

    def _literal(self):
        """!
        @brief Read a literal string starting at the opening parenthesis.

        Handles nested balanced parentheses, the escapes \\n \\r \\t \\b \\f,
        octal escapes of 1 to 3 digits, and backslash line continuations.
        Any other escaped byte stands for itself. An unterminated string ends at the end
        of the data.
        @return the string bytes.
        """
        d, n = self.d, self.n
        p = self.p + 1
        depth = 1
        out = bytearray()
        while p < n:
            c = d[p]
            if c == 0x5C:  # backslash
                p += 1
                if p >= n:
                    break
                e = d[p]
                if e in b'nrtbf':
                    out.append(b'\n\r\t\b\f'[b'nrtbf'.index(e)])
                    p += 1
                elif 0x30 <= e <= 0x37:
                    q = p
                    while q < n and q < p + 3 and 0x30 <= d[q] <= 0x37:
                        q += 1
                    out.append(int(d[p:q], 8) & 0xFF)
                    p = q
                elif e == 0x0D:
                    p += 2 if d[p + 1 : p + 2] == b'\n' else 1
                elif e == 0x0A:
                    p += 1
                else:
                    out.append(e)
                    p += 1
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    p += 1
                    break
            out.append(c)
            p += 1
        self.p = p
        return bytes(out)

    @staticmethod
    def _refs(items):
        """!
        @brief Replace each "int int R" triple in a token list with a Ref.
        @param items list of parsed objects.
        @return a new list with references collapsed.
        """
        out = []
        i = 0
        while i < len(items):
            if (
                i + 2 < len(items)
                and type(items[i]) is int
                and type(items[i + 1]) is int
                and isinstance(items[i + 2], Op)
                and items[i + 2] == 'R'
            ):
                out.append(Ref(items[i], items[i + 1]))
                i += 3
            else:
                out.append(items[i])
                i += 1
        return out

    def build(self, t, depth=0):
        """!
        @brief Turn a token into an object, reading further tokens for arrays and dictionaries.

        Arrays become lists and dictionaries become dicts with str keys; both have
        references collapsed to Ref. Dictionary entries whose key is not a Name are dropped.
        true, false and null become True, False and None. Missing closing delimiters end
        the container at the end of the data.
        Containers nested deeper than MAX_NESTING levels are skipped and become None.
        @param t token returned by token().
        @param depth nesting level of t.
        @return the parsed object, or t itself for other tokens.
        """
        if isinstance(t, Op):
            if (t == '[' or t == '<<') and depth >= MAX_NESTING:
                self._skip_container()
                return None
            if t == '[':
                arr = []
                while True:
                    t2 = self.token()
                    if t2 is None or (isinstance(t2, Op) and t2 == ']'):
                        break
                    arr.append(self.build(t2, depth + 1))
                return self._refs(arr)
            if t == '<<':
                items = []
                while True:
                    t2 = self.token()
                    if t2 is None or (isinstance(t2, Op) and t2 == '>>'):
                        break
                    items.append(self.build(t2, depth + 1))
                items = self._refs(items)
                dct = {}
                for i in range(0, len(items) - 1, 2):
                    if isinstance(items[i], Name):
                        dct[str(items[i])] = items[i + 1]
                return dct
            if t == 'true':
                return True
            if t == 'false':
                return False
            if t == 'null':
                return None
        return t

    def _skip_container(self):
        """!
        @brief Skip tokens up to the delimiter that closes an array or dictionary.

        The opening delimiter has already been read. Runs without recursion, so any
        nesting depth is handled.
        """
        level = 1
        while level:
            t = self.token()
            if t is None:
                return
            if isinstance(t, Op):
                if t == '[' or t == '<<':
                    level += 1
                elif t == ']' or t == '>>':
                    level -= 1

    def value(self):
        """!
        @brief Parse one object, including a top-level "num gen R" reference.
        @return the parsed object, or a Ref for a reference.
        """
        v = self.build(self.token())
        if type(v) is int:
            save = self.p
            t2, t3 = self.token(), self.token()
            if type(t2) is int and isinstance(t3, Op) and t3 == 'R':
                return Ref(v, t2)
            self.p = save
        return v


# ---------------------------------------------------------------------------
# Stream filters
# ---------------------------------------------------------------------------


def _flate(data):
    """!
    @brief Decompress FlateDecode (zlib) data, tolerating damaged input.

    When one-shot decompression fails, data is fed in 1024-byte chunks and the output
    decoded before the error is kept. When nothing decodes, raw deflate is tried from
    byte 2 on, skipping the zlib header. Output stops at MAX_DECODED bytes.
    @param data compressed bytes.
    @return the decompressed bytes, possibly partial or empty.
    """
    try:
        return zlib.decompressobj().decompress(data, MAX_DECODED)
    except zlib.error:
        pass
    out = bytearray()
    d = zlib.decompressobj()
    try:
        for i in range(0, len(data), 1024):
            room = MAX_DECODED - len(out)
            if room <= 0:
                break
            out += d.decompress(data[i : i + 1024], room)
    except zlib.error:
        if not out:
            try:
                return zlib.decompressobj(-15).decompress(data[2:], MAX_DECODED)
            except zlib.error:
                pass
    return bytes(out)


def _predict(data, parms):
    """!
    @brief Undo a TIFF or PNG predictor given in /DecodeParms.

    Predictor 1, a missing value or non-dict parms return the data unchanged, and so do
    Colors, BitsPerComponent or Columns that are not positive integers, or a row
    length above the data length.
    Predictor 2 (TIFF) is undone only for 8 bits per component, else data is unchanged.
    Any predictor above 2 is read as PNG rows: one filter-type byte, then the row.
    PNG filter types 1 (Sub), 2 (Up), 3 (Average) and 4 (Paeth) are undone; other
    types copy the row. A short last row is padded with zero bytes.
    @param data decoded filter output.
    @param parms decode parameter dictionary (Predictor, Colors, BitsPerComponent, Columns).
    @return the reconstructed bytes.
    """
    if not isinstance(parms, dict):
        return data
    pred = parms.get('Predictor', 1)
    if not isinstance(pred, int) or pred < 2:
        return data
    colors = parms.get('Colors', 1)
    bpc = parms.get('BitsPerComponent', 8)
    cols = parms.get('Columns', 1)
    if not all(type(v) is int and v > 0 for v in (colors, bpc, cols)):
        return data
    bpp = max(1, (colors * bpc + 7) // 8)
    rowlen = (colors * bpc * cols + 7) // 8
    if rowlen > len(data):
        return data  # one row longer than the whole stream: parameters are wrong
    if pred == 2:
        if bpc != 8:
            return data
        out = bytearray(data)
        for r in range(0, len(out), rowlen):
            for i in range(r + bpp, min(r + rowlen, len(out))):
                out[i] = (out[i] + out[i - bpp]) & 0xFF
        return bytes(out)
    out = bytearray()
    prev = bytearray(rowlen)
    i = 0
    while i < len(data):
        ft = data[i]
        row = bytearray(data[i + 1 : i + 1 + rowlen])
        i += 1 + rowlen
        if len(row) < rowlen:
            row.extend(bytes(rowlen - len(row)))
        if ft == 1:
            for j in range(bpp, rowlen):
                row[j] = (row[j] + row[j - bpp]) & 0xFF
        elif ft == 2:
            for j in range(rowlen):
                row[j] = (row[j] + prev[j]) & 0xFF
        elif ft == 3:
            for j in range(rowlen):
                left = row[j - bpp] if j >= bpp else 0
                row[j] = (row[j] + ((left + prev[j]) >> 1)) & 0xFF
        elif ft == 4:
            for j in range(rowlen):
                a = row[j - bpp] if j >= bpp else 0
                b = prev[j]
                c = prev[j - bpp] if j >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[j] = (row[j] + pr) & 0xFF
        out += row
        prev = row
    return bytes(out)


def _lzw(data, parms):
    """!
    @brief Decode LZWDecode data.

    Codes start at 9 bits and grow to at most 12 bits. Code 256 clears the table and
    code 257 ends the data. /EarlyChange (default 1) sets when the code width grows.
    An invalid code ends decoding and returns the output so far.
    @param data compressed bytes.
    @param parms decode parameter dictionary, or another value for defaults.
    @return the decoded bytes.
    """
    early = parms.get('EarlyChange', 1) if isinstance(parms, dict) else 1
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b'', b'']
    bits, buf, nb, prev = 9, 0, 0, None
    for byte in data:
        buf = (buf << 8) | byte
        nb += 8
        while nb >= bits:
            nb -= bits
            code = (buf >> nb) & ((1 << bits) - 1)
            buf &= (1 << nb) - 1
            if code == 256:
                table = table[:258]
                bits, prev = 9, None
                continue
            if code == 257:
                return bytes(out)
            if prev is None:
                if code >= len(table):
                    return bytes(out)
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            elif code == len(table):
                entry = prev + prev[:1]
                table.append(entry)
            else:
                return bytes(out)
            out += entry
            if len(out) >= MAX_DECODED:
                return bytes(out[:MAX_DECODED])
            prev = entry
            if len(table) + early >= (1 << bits) and bits < 12:
                bits += 1
    return bytes(out)


def _a85(data):
    """!
    @brief Decode ASCII85Decode data.

    Whitespace is removed, a leading <~ is skipped and the data ends at ~>.
    @param data encoded bytes.
    @return the decoded bytes.
    """
    data = re.sub(rb'\s', b'', data)
    if data.startswith(b'<~'):
        data = data[2:]
    end = data.find(b'~>')
    if end >= 0:
        data = data[:end]
    return base64.a85decode(data)


def _ahx(data):
    """!
    @brief Decode ASCIIHexDecode data.

    The data ends at the first >. Non-hex bytes are ignored and an odd digit count is
    padded with 0.
    @param data encoded bytes.
    @return the decoded bytes.
    """
    end = data.find(b'>')
    if end >= 0:
        data = data[:end]
    hx = re.sub(rb'[^0-9A-Fa-f]', b'', data)
    if len(hx) % 2:
        hx += b'0'
    return bytes.fromhex(hx.decode('ascii'))


def _rld(data):
    """!
    @brief Decode RunLengthDecode data.

    A length byte below 128 copies the next length+1 bytes. A byte above 128 repeats
    the next byte 257-length times. 128 ends the data.
    @param data encoded bytes.
    @return the decoded bytes.
    """
    out = bytearray()
    i = 0
    while i < len(data):
        n = data[i]
        i += 1
        if n == 128:
            break
        if n < 128:
            out += data[i : i + n + 1]
            i += n + 1
        elif i < len(data):
            out += bytes([data[i]]) * (257 - n)
            i += 1
        if len(out) >= MAX_DECODED:
            return bytes(out[:MAX_DECODED])
    return bytes(out)


## @brief Image filter names that are not decoded; the data is kept in its native format.
##
## DCT (JPEG), JPX (JPEG 2000), JBIG2 and CCITTFax, with their abbreviations.
IMAGE_FILTERS = {'DCTDecode', 'DCT', 'JPXDecode', 'JBIG2Decode', 'CCITTFaxDecode', 'CCF'}


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

## @brief Pattern for an "num gen obj" object header not preceded by a digit.
_OBJ_RE = re.compile(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b')
## @brief Pattern for the start of a trailer dictionary.
_TRAILER_RE = re.compile(rb'trailer\s*<<')


class Document:
    """!
    @brief Parsed PDF file: object table, trailer and access to pages, labels and outline.

    Objects are found by scanning the whole file for object headers, not by reading
    the cross-reference table.

    @par Attributes
    - data: the file bytes.
    - objects: object number to parsed object (dict, list, Stream, number, ...).
    - objpos: object number to byte offset of its definition; objects from an object
      stream get the offset of that stream.
    - _dec: cache of decoded stream bytes keyed by id() of the Stream.
    - trailer: merged trailer dictionary.
    - encrypted: True when the trailer has an /Encrypt entry.
    """

    def __init__(self, data):
        """!
        @brief Parse a PDF file held in memory.

        Scans objects, expands object streams, fixes stream lengths given as references
        and builds the trailer.
        @param data the whole file as bytes.
        @exception PDFError when no %PDF header is in the first 1024 bytes.
        """
        if b'%PDF' not in data[:1024]:
            raise PDFError('no %PDF header in the first 1024 bytes')
        ## the file bytes.
        self.data = data
        ## object number to parsed object (dict, list, Stream, number, ...).
        self.objects = {}
        ## object number to byte offset of its definition; objects from an object
        ## stream get the offset of that stream.
        self.objpos = {}
        ## cache of decoded stream bytes keyed by id() of the Stream.
        self._dec = {}
        pending = self._scan()
        self._fix_lengths(pending)
        self._expand_object_streams()
        self._fix_lengths(pending)
        ## merged trailer dictionary.
        self.trailer = self._trailer()
        ## True when the trailer has an /Encrypt entry.
        self.encrypted = 'Encrypt' in self.trailer

    def _valid_end(self, p):
        """!
        @brief Check whether the endstream keyword follows at a position.

        Leading whitespace in the 32 bytes from p is skipped.
        @param p byte offset in the file.
        @return True when endstream starts there.
        """
        return self.data[p : p + 32].lstrip().startswith(b'endstream')

    def _scan(self):
        """!
        @brief Find every "num gen obj" header in the file and parse its object.

        A later definition of the same number replaces an earlier one. Matches inside a
        parsed object or stream are skipped. A stream uses /Length when that is a direct
        integer followed by endstream; otherwise its data runs to the next endstream,
        without the end-of-line before it.
        @return list of (object number, data start offset) for streams whose /Length is
        an indirect reference.
        """
        d = self.data
        pending = []
        skip = 0
        for m in _OBJ_RE.finditer(d):
            if m.start() < skip:
                continue
            num = int(m.group(1))
            lx = Lexer(d, m.end())
            try:
                val = lx.value()
            except Exception:
                continue
            end = lx.p
            if isinstance(val, dict):
                save = lx.p
                t = lx.token()
                if isinstance(t, Op) and t == 'stream':
                    start = lx.p
                    if d[start : start + 2] == b'\r\n':
                        start += 2
                    elif d[start : start + 1] in (b'\n', b'\r'):
                        start += 1
                    length = val.get('Length')
                    if type(length) is int and length >= 0 and self._valid_end(start + length):
                        raw = d[start : start + length]
                        end = start + length
                    else:
                        e = d.find(b'endstream', start)
                        if e < 0:
                            e = len(d)
                        stop = e
                        if d[stop - 2 : stop] == b'\r\n':
                            stop -= 2
                        elif d[stop - 1 : stop] in (b'\n', b'\r'):
                            stop -= 1
                        raw = d[start : max(start, stop)]
                        end = e + 9
                        if isinstance(length, Ref):
                            pending.append((num, start))
                    val = Stream(val, raw)
                else:
                    lx.p = save
            self.objects[num] = val
            self.objpos[num] = m.start()
            skip = end
        return pending

    def _fix_lengths(self, pending):
        """!
        @brief Re-cut stream data using indirect /Length values once they can be resolved.

        The new length is used only when endstream follows it.
        @param pending list of (object number, data start offset) from _scan().
        """
        for num, start in pending:
            s = self.objects.get(num)
            if not isinstance(s, Stream):
                continue
            n = self.resolve(s.get('Length'))
            if type(n) is int and n >= 0 and self._valid_end(start + n):
                s.raw = self.data[start : start + n]

    def _expand_object_streams(self):
        """!
        @brief Add the objects stored in /Type /ObjStm object streams to the object table.

        An object already defined later in the file than the object stream is kept.
        Object streams that fail to parse are skipped.
        """
        for snum, s in list(self.objects.items()):
            if not isinstance(s, Stream) or s.get('Type') != 'ObjStm':
                continue
            try:
                data = self.decoded(s)
                n = min(int(self.resolve(s.get('N'))), len(data) // 4)
                first = int(self.resolve(s.get('First')))
                lx = Lexer(data)
                header = []
                while len(header) < 2 * n and lx.p < first:
                    t = lx.token()
                    if t is None:
                        break
                    header.append(t)
                for i in range(0, len(header) - 1, 2):
                    onum, off = header[i], header[i + 1]
                    if type(onum) is not int or type(off) is not int:
                        continue
                    spos = self.objpos.get(snum, 0)
                    if onum in self.objects and self.objpos.get(onum, -1) > spos:
                        continue
                    self.objects[onum] = Lexer(data, first + off).value()
                    self.objpos[onum] = spos
            except Exception:
                continue

    def _trailer(self):
        """!
        @brief Build the trailer dictionary.

        Merges all trailer dictionaries and cross-reference stream dictionaries in file
        order, later entries overriding earlier ones. When /Root does not resolve to a
        dictionary, the first /Type /Catalog object is used as root.
        @return the merged trailer dictionary.
        """
        found = []
        for m in _TRAILER_RE.finditer(self.data):
            try:
                t = Lexer(self.data, m.start() + 7).value()
                if isinstance(t, dict):
                    found.append((m.start(), t))
            except Exception:
                pass
        for num, o in self.objects.items():
            if isinstance(o, Stream) and o.get('Type') == 'XRef':
                found.append((self.objpos.get(num, 0), o.dict))
        found.sort(key=lambda x: x[0])
        merged = {}
        for _, t in found:
            merged.update(t)
        if not isinstance(self.resolve(merged.get('Root')), dict):
            for num, o in self.objects.items():
                if isinstance(o, dict) and o.get('Type') == 'Catalog':
                    merged['Root'] = Ref(num, 0)
                    break
        return merged

    def resolve(self, x):
        """!
        @brief Follow indirect references to the object they name.
        @param x any object, possibly a Ref.
        @return the referenced object, x itself when it is not a Ref, or None for a missing
        object or a chain longer than 32 references.
        """
        for _ in range(32):
            if not isinstance(x, Ref):
                return x
            x = self.objects.get(x.num)
        return None

    def decode(self, s):
        """!
        @brief Apply the stream's filters to its raw data.

        Supports FlateDecode and LZWDecode (with predictors), ASCII85Decode,
        ASCIIHexDecode and RunLengthDecode, also by their abbreviated names.
        At the first image filter (IMAGE_FILTERS) decoding stops.
        @param s the Stream.
        @return tuple (bytes, native image filter name or None). With an image filter the
        bytes are the data still in that filter's encoding.
        @exception PDFError when a filter is not supported.
        """
        data = s.raw
        filters = self.resolve(s.get('Filter'))  # /F in a stream dict is a file spec, not a filter
        parms = self.resolve(s.get('DecodeParms'))
        if filters is None:
            filters = []
        elif not isinstance(filters, list):
            filters = [filters]
        if not isinstance(parms, list):
            parms = [parms]
        for i, f in enumerate(filters):
            f = self.resolve(f)
            p = self.resolve(parms[i]) if i < len(parms) else None
            if f in ('FlateDecode', 'Fl'):
                data = _predict(_flate(data), p)
            elif f in ('LZWDecode', 'LZW'):
                data = _predict(_lzw(data, p), p)
            elif f in ('ASCII85Decode', 'A85'):
                data = _a85(data)
            elif f in ('ASCIIHexDecode', 'AHx'):
                data = _ahx(data)
            elif f in ('RunLengthDecode', 'RL'):
                data = _rld(data)
            elif f in IMAGE_FILTERS:
                return data, f
            else:
                raise PDFError('unsupported filter %s' % f)
        return data, None

    def decoded(self, s):
        """!
        @brief Return the decoded bytes of a stream, cached per stream.
        @param s the Stream.
        @return the decoded bytes, or b'' when decoding fails or the stream ends in an image
        filter.
        """
        key = id(s)
        if key not in self._dec:
            try:
                data, native = self.decode(s)
                self._dec[key] = b'' if native else data
            except Exception:
                self._dec[key] = b''
        return self._dec[key]

    def pages(self):
        """!
        @brief List the pages in page tree order.

        Inherits /Resources, /MediaBox, /CropBox and /Rotate down the tree. Stops at
        depth 64 and skips nodes already visited. When the tree yields no page, all
        /Type /Page objects are used in object number order.
        @return list of (page dictionary, dictionary of inherited and own attributes).
        """
        out = []
        root = self.resolve(self.trailer.get('Root')) or {}
        seen = set()

        def walk(node, inherited, depth):
            """!
            @brief Visit one page tree node and append its leaf pages to the result.
            @param node page tree node or reference to it.
            @param inherited inheritable attributes from the ancestors.
            @param depth nesting depth of node.
            """
            node = self.resolve(node)
            if not isinstance(node, dict) or id(node) in seen or depth > 64:
                return
            seen.add(id(node))
            inh = dict(inherited)
            for k in ('Resources', 'MediaBox', 'CropBox', 'Rotate'):
                if k in node:
                    inh[k] = node[k]
            kids = self.resolve(node.get('Kids'))
            if node.get('Type') == 'Pages' or isinstance(kids, list):
                for k in kids if isinstance(kids, list) else []:
                    walk(k, inh, depth + 1)
            else:
                out.append((node, inh))

        if isinstance(root, dict):
            walk(root.get('Pages'), {}, 0)
        if not out:
            for num in sorted(self.objects):
                o = self.objects[num]
                if isinstance(o, dict) and o.get('Type') == 'Page':
                    out.append((o, o))
        return out

    def page_labels(self, count):
        """!
        @brief Return the page labels from the /PageLabels number tree.

        Styles: D decimal, R and r upper and lower Roman, A and a letters (27 is AA).
        Any other style gives only the prefix /P. /St sets the first number (default 1).
        Pages before the first range get their plain 1-based page number. /St is capped
        at 10^9, the prefix at 200 characters, and letter labels above 100 letters
        (n > 2600) use decimal digits.
        At most 10000 tree nodes are read.
        @param count number of pages.
        @return a list of count label strings, or None when the document defines none.
        """
        r = self.resolve
        root = r(self.trailer.get('Root')) or {}
        tree = r(root.get('PageLabels')) if isinstance(root, dict) else None
        if not isinstance(tree, dict):
            return None
        ranges = []
        stack, seen = [tree], set()
        while stack and len(seen) < 10000:
            node = r(stack.pop())
            if not isinstance(node, dict) or id(node) in seen:
                continue
            seen.add(id(node))
            nums = r(node.get('Nums'))
            if isinstance(nums, list):
                for i in range(0, len(nums) - 1, 2):
                    start, spec = r(nums[i]), r(nums[i + 1])
                    if type(start) is int and isinstance(spec, dict):
                        ranges.append((start, spec))
            kids = r(node.get('Kids'))
            if isinstance(kids, list):
                stack += kids
        if not ranges:
            return None
        ranges.sort(key=lambda t: t[0])
        labels = []
        k = -1
        for i in range(count):
            while k + 1 < len(ranges) and ranges[k + 1][0] <= i:
                k += 1
            if k < 0:
                labels.append(str(i + 1))  # before the first range: plain page number
                continue
            start, spec = ranges[k]
            first = r(spec.get('St'))
            first = min(first, 10**9) if type(first) is int and first > 0 else 1
            n = i - start + first
            style = r(spec.get('S'))
            prefix = _text_string(r(spec.get('P')))[:200]
            if style == 'D':
                num = str(n)
            elif style in ('R', 'r'):
                num = _roman(n) if style == 'R' else _roman(n).lower()
            elif style in ('A', 'a') and n <= 26 * 100:
                letter = chr(ord('A') + (n - 1) % 26) * ((n - 1) // 26 + 1)
                num = letter if style == 'A' else letter.lower()
            elif style in ('A', 'a'):
                num = str(n)  # letter labels longer than 100 characters
            else:
                num = ''
            labels.append(prefix + num)
        return labels

    def _named_dests(self):
        """!
        @brief Collect named destinations.

        Reads the catalog /Dests dictionary and the /Names /Dests name tree; name tree
        entries override. At most 100000 tree nodes are read.
        @return dict of destination name to destination object.
        """
        r = self.resolve
        root = r(self.trailer.get('Root')) or {}
        out = {}
        old = r(root.get('Dests')) if isinstance(root, dict) else None
        if isinstance(old, dict):
            for k, v in old.items():
                out[k] = v
        names = r(root.get('Names')) if isinstance(root, dict) else None
        tree = r(names.get('Dests')) if isinstance(names, dict) else None
        stack, seen = [tree], set()
        while stack and len(seen) < 100000:
            node = r(stack.pop())
            if not isinstance(node, dict) or id(node) in seen:
                continue
            seen.add(id(node))
            arr = r(node.get('Names'))
            if isinstance(arr, list):
                for i in range(0, len(arr) - 1, 2):
                    key = r(arr[i])
                    if isinstance(key, bytes):
                        key = _text_string(key)
                    if isinstance(key, str):
                        out[key] = arr[i + 1]
            kids = r(node.get('Kids'))
            if isinstance(kids, list):
                stack += kids
        return out

    def outline(self, page_numbers):
        """!
        @brief Return the document outline (bookmarks) as nested [title, page, children] lists.

        Uses /Dest, or the /D of a /GoTo action. At most 20000 entries and 32 levels.
        @param page_numbers dict mapping id(page dictionary) to the 1-based page number.
        @return list of [title, page, children]. page is 1-based, or None when the
        destination does not resolve.
        """
        r = self.resolve
        root = r(self.trailer.get('Root')) or {}
        top = r(root.get('Outlines')) if isinstance(root, dict) else None
        if not isinstance(top, dict):
            return []
        named = None
        seen = set()

        def dest_page(d):
            """!
            @brief Resolve an outline destination to a page number.

            Follows named destinations and dictionaries with /D, up to 4 steps. The first
            array element is a page reference, or an integer taken as a 0-based page index.
            @param d destination: array, name, string or dictionary.
            @return the 1-based page number, or None.
            """
            nonlocal named
            for _ in range(4):
                d = r(d)
                if isinstance(d, (bytes, str)):
                    if named is None:
                        named = self._named_dests()
                    d = named.get(_text_string(d) if isinstance(d, bytes) else d)
                elif isinstance(d, dict):
                    d = d.get('D')
                else:
                    break
            if isinstance(d, list) and d:
                target = d[0]
                if isinstance(target, Ref):
                    return page_numbers.get(id(r(target)))
                if type(target) is int and 0 <= target < len(page_numbers):
                    return target + 1
            return None

        def items(first, depth):
            """!
            @brief Read a chain of sibling outline items and their children.
            @param first first outline item of the chain.
            @param depth nesting level of the chain.
            @return list of [title, page, children].
            """
            out = []
            node = r(first)
            while (
                isinstance(node, dict) and id(node) not in seen and len(seen) < 20000 and depth < 32
            ):
                seen.add(id(node))
                title = _text_string(r(node.get('Title'))).strip()
                act = r(node.get('A'))
                dest = node.get('Dest')
                if dest is None and isinstance(act, dict) and r(act.get('S')) == 'GoTo':
                    dest = act.get('D')
                out.append([title, dest_page(dest), items(node.get('First'), depth + 1)])
                node = r(node.get('Next'))
            return out

        return items(top.get('First'), 0)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

## @brief Map of Adobe glyph names to their Unicode text.
##
## Covers ASCII punctuation, digits, quotes, dashes, ligatures (fi, ffl, ...),
## currency signs and common symbols. Ligature names map to the letter sequence.
GLYPHS = {
    'space': ' ',
    'exclam': '!',
    'quotedbl': '"',
    'numbersign': '#',
    'dollar': '$',
    'percent': '%',
    'ampersand': '&',
    'quotesingle': "'",
    'quoteright': '\u2019',
    'quoteleft': '\u2018',
    'parenleft': '(',
    'parenright': ')',
    'asterisk': '*',
    'plus': '+',
    'comma': ',',
    'hyphen': '-',
    'period': '.',
    'slash': '/',
    'colon': ':',
    'semicolon': ';',
    'less': '<',
    'equal': '=',
    'greater': '>',
    'question': '?',
    'at': '@',
    'bracketleft': '[',
    'backslash': '\\',
    'bracketright': ']',
    'asciicircum': '^',
    'underscore': '_',
    'grave': '`',
    'braceleft': '{',
    'bar': '|',
    'braceright': '}',
    'asciitilde': '~',
    'zero': '0',
    'one': '1',
    'two': '2',
    'three': '3',
    'four': '4',
    'five': '5',
    'six': '6',
    'seven': '7',
    'eight': '8',
    'nine': '9',
    'bullet': '\u2022',
    'endash': '\u2013',
    'emdash': '\u2014',
    'quotedblleft': '\u201c',
    'quotedblright': '\u201d',
    'quotesinglbase': '\u201a',
    'quotedblbase': '\u201e',
    'guillemotleft': '\u00ab',
    'guillemotright': '\u00bb',
    'guilsinglleft': '\u2039',
    'guilsinglright': '\u203a',
    'ellipsis': '\u2026',
    'fi': 'fi',
    'fl': 'fl',
    'ff': 'ff',
    'ffi': 'ffi',
    'ffl': 'ffl',
    'dagger': '\u2020',
    'daggerdbl': '\u2021',
    'degree': '\u00b0',
    'copyright': '\u00a9',
    'registered': '\u00ae',
    'trademark': '\u2122',
    'section': '\u00a7',
    'paragraph': '\u00b6',
    'periodcentered': '\u00b7',
    'multiply': '\u00d7',
    'divide': '\u00f7',
    'minus': '\u2212',
    'plusminus': '\u00b1',
    'Euro': '\u20ac',
    'sterling': '\u00a3',
    'yen': '\u00a5',
    'cent': '\u00a2',
    'germandbls': '\u00df',
    'dotlessi': '\u0131',
    'ae': '\u00e6',
    'AE': '\u00c6',
    'oe': '\u0153',
    'OE': '\u0152',
    'exclamdown': '\u00a1',
    'questiondown': '\u00bf',
    'nbspace': ' ',
    'nonbreakingspace': ' ',
    'sfthyphen': '-',
    'softhyphen': '-',
    'mu': '\u00b5',
    'perthousand': '\u2030',
    'onehalf': '\u00bd',
    'onequarter': '\u00bc',
    'threequarters': '\u00be',
    'onesuperior': '\u00b9',
    'twosuperior': '\u00b2',
    'threesuperior': '\u00b3',
    'ordfeminine': '\u00aa',
    'ordmasculine': '\u00ba',
    'logicalnot': '\u00ac',
    'brokenbar': '\u00a6',
    'currency': '\u00a4',
    'arrowright': '\u2192',
    'arrowleft': '\u2190',
    'arrowup': '\u2191',
    'arrowdown': '\u2193',
    'checkmark': '\u2713',
    'lessequal': '\u2264',
    'greaterequal': '\u2265',
    'notequal': '\u2260',
    'infinity': '\u221e',
    'approxequal': '\u2248',
    'florin': '\u0192',
    'eth': '\u00f0',
    'Eth': '\u00d0',
    'thorn': '\u00fe',
    'Thorn': '\u00de',
}
## @brief Map of glyph-name accent suffixes to Unicode character-name words.
##
## Used to build names such as LATIN SMALL LETTER E WITH ACUTE.
_ACCENTS = {
    'acute': 'ACUTE',
    'grave': 'GRAVE',
    'circumflex': 'CIRCUMFLEX',
    'dieresis': 'DIAERESIS',
    'tilde': 'TILDE',
    'ring': 'RING ABOVE',
    'cedilla': 'CEDILLA',
    'caron': 'CARON',
    'macron': 'MACRON',
    'breve': 'BREVE',
    'ogonek': 'OGONEK',
    'dotaccent': 'DOT ABOVE',
    'hungarumlaut': 'DOUBLE ACUTE',
    'slash': 'STROKE',
}
## @brief Pattern for a glyph name of one Latin letter plus an accent suffix from _ACCENTS.
_ACCENT_RE = re.compile(r'([A-Za-z])(%s)$' % '|'.join(_ACCENTS))


def glyph_char(name):
    """!
    @brief Convert a glyph name to Unicode text.

    Tries in order: GLYPHS; the part before a dot suffix; parts joined by _ (ligatures);
    uniXXXX sequences; uXXXX to uXXXXXX; a single-character name; letter plus accent;
    the Unicode character name, then a Greek letter of that name.
    @param name glyph name.
    @return the text, or None when the name is empty or not recognised.
    """
    if not name:
        return None
    if name in GLYPHS:
        return GLYPHS[name]
    base = name.split('.')[0]
    if base != name:
        return glyph_char(base) if base else None
    if '_' in name:
        parts = [glyph_char(p) for p in name.split('_')]
        return ''.join(p or '' for p in parts) or None
    m = re.fullmatch(r'uni((?:[0-9A-Fa-f]{4})+)', name)
    if m:
        h = m.group(1)
        return ''.join(chr(int(h[i : i + 4], 16)) for i in range(0, len(h), 4))
    m = re.fullmatch(r'u([0-9A-Fa-f]{4,6})', name)
    if m:
        v = int(m.group(1), 16)
        return chr(v) if v <= 0x10FFFF else None
    if len(name) == 1:
        return name
    m = _ACCENT_RE.match(name)
    if m:
        letter, acc = m.groups()
        case = 'CAPITAL' if letter.isupper() else 'SMALL'
        try:
            return unicodedata.lookup(
                'LATIN %s LETTER %s WITH %s' % (case, letter.upper(), _ACCENTS[acc])
            )
        except KeyError:
            pass
    case = 'CAPITAL' if name[0].isupper() else 'SMALL'
    for cand in (name.upper(), 'GREEK %s LETTER %s' % (case, name.upper())):
        try:
            return unicodedata.lookup(cand)
        except KeyError:
            pass
    return None


def _w(spec):
    """!
    @brief Expand a compact width list into 95 integers.

    Items are separated by spaces; "NxR" repeats N R times.
    An assert requires exactly 95 values.
    @param spec width list string.
    @return list of 95 widths.
    """
    out = []
    for part in spec.split():
        n, _, rep = part.partition('x')
        out += [int(n)] * int(rep or 1)
    assert len(out) == 95
    return out


## @brief Advance widths in 1/1000 em for codes 32-126 of Helvetica, Times and bold faces.
##
## Oblique and italic faces use the upright widths. Courier is 600 throughout and
## is not listed.
STD_WIDTHS = {
    'Helvetica': _w(
        '278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556x10 278 278 584 584 '
        '584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 '
        '667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 '
        '222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584'
    ),
    'Helvetica-Bold': _w(
        '278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556x10 333 333 584 '
        '584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 '
        '722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 '
        '611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 '
        '280 389 584'
    ),
    'Times-Roman': _w(
        '250 333 408 500 500 833 778 180 333 333 500 564 250 333 250 278 500x10 278 278 564 564 '
        '564 444 921 722 667 667 722 611 556 722 722 333 389 722 611 889 722 722 556 722 667 556 '
        '611 722 722 944 722 722 611 333 278 333 469 500 333 444 500 444 500 444 333 500 500 278 '
        '278 500 278 778 500 500 500 500 333 389 278 500 500 722 500 500 444 480 200 480 541'
    ),
    'Times-Bold': _w(
        '250 333 555 500 500 1000 833 278 333 333 500 570 250 333 250 278 500x10 333 333 570 570 '
        '570 500 930 722 667 722 722 667 611 778 778 389 500 778 667 944 722 778 611 778 722 556 '
        '667 722 722 1000 722 722 667 333 278 333 581 500 333 500 556 444 556 444 333 500 556 278 '
        '333 556 278 833 556 500 556 556 444 389 333 556 500 722 500 500 444 394 220 394 520'
    ),
}


def _std_widths(base):
    """!
    @brief Pick built-in widths for a font that has no /Widths array.

    The subset prefix before + is ignored and matching ignores case. Courier gives
    600 for every code. Times, Helvetica and Arial use STD_WIDTHS, the bold table when
    the name contains bold.
    @param base the /BaseFont name.
    @return list of 95 widths in 1/1000 em for codes 32-126, or None for other fonts.
    """
    name = base.split('+')[-1].lower()
    if 'courier' in name:
        return [600] * 95
    family = (
        'Times'
        if 'times' in name
        else ('Helvetica' if re.search(r'helvetica|arial', name) else None)
    )
    if family is None:
        return None
    if 'bold' in name:
        return STD_WIDTHS[family + '-Bold']
    return STD_WIDTHS['Times-Roman' if family == 'Times' else 'Helvetica']


## @brief Unicode text of the Symbol font encoding as two strings.
##
## The first string covers codes 0x20-0x7E, the second 0xA0-0xFE.
## U+FFFD marks an unused code.
_SYMBOL = (
    ' !\u2200#\u2203%&\u220b()\u2217+,\u2212./0123456789:;<=>?\u2245'
    '\u0391\u0392\u03a7\u0394\u0395\u03a6\u0393\u0397\u0399\u03d1\u039a\u039b\u039c\u039d\u039f'
    '\u03a0\u0398\u03a1\u03a3\u03a4\u03a5\u03c2\u03a9\u039e\u03a8\u0396[\u2234]\u22a5_\u203e'
    '\u03b1\u03b2\u03c7\u03b4\u03b5\u03c6\u03b3\u03b7\u03b9\u03d5\u03ba\u03bb\u03bc\u03bd\u03bf'
    '\u03c0\u03b8\u03c1\u03c3\u03c4\u03c5\u03d6\u03c9\u03be\u03c8\u03b6{|}\u223c',
    '\u20ac\u03d2\u2032\u2264\u2044\u221e\u0192\u2663\u2666\u2665\u2660\u2194\u2190\u2191\u2192\u2193'
    '\u00b0\u00b1\u2033\u2265\u00d7\u221d\u2202\u2022\u00f7\u2260\u2261\u2248\u2026\u23d0\u23af\u21b5'
    '\u2135\u2111\u211c\u2118\u2297\u2295\u2205\u2229\u222a\u2283\u2287\u2284\u2282\u2286\u2208\u2209'
    '\u2220\u2207\u00ae\u00a9\u2122\u220f\u221a\u22c5\u00ac\u2227\u2228\u21d4\u21d0\u21d1\u21d2\u21d3'
    '\u25ca\u2329\u00ae\u00a9\u2122\u2211\u239b\u239c\u239d\u23a1\u23a2\u23a3\u23a7\u23a8\u23a9\u23aa'
    '\ufffd\u232a\u222b\u2320\u23ae\u2321\u239e\u239f\u23a0\u23a4\u23a5\u23a6\u23ab\u23ac\u23ad',
)
## @brief Unicode text of the Adobe StandardEncoding codes above 0xA0.
_STANDARD_HIGH = dict(
    zip(
        [
            0xA1,
            0xA2,
            0xA3,
            0xA4,
            0xA5,
            0xA6,
            0xA7,
            0xA8,
            0xA9,
            0xAA,
            0xAB,
            0xAC,
            0xAD,
            0xAE,
            0xAF,
            0xB1,
            0xB2,
            0xB3,
            0xB4,
            0xB6,
            0xB7,
            0xB8,
            0xB9,
            0xBA,
            0xBB,
            0xBC,
            0xBD,
            0xBF,
            0xC1,
            0xC2,
            0xC3,
            0xC4,
            0xC5,
            0xC6,
            0xC7,
            0xC8,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
            0xD0,
            0xE1,
            0xE3,
            0xE8,
            0xE9,
            0xEA,
            0xEB,
            0xF1,
            0xF5,
            0xF8,
            0xF9,
            0xFA,
            0xFB,
        ],
        '\u00a1\u00a2\u00a3\u2044\u00a5\u0192\u00a7\u00a4\'\u201c\u00ab\u2039\u203a\ufb01\ufb02\u2013\u2020\u2021'
        '\u00b7\u00b6\u2022\u201a\u201e\u201d\u00bb\u2026\u2030\u00bf`\u00b4\u02c6\u02dc\u00af\u02d8\u02d9\u00a8'
        '\u02da\u00b8\u02dd\u02db\u02c7\u2014\u00c6\u00aa\u0141\u00d8\u0152\u00ba\u00e6\u0131\u0142\u00f8\u0153\u00df',
    )
)
## @brief Unicode text of the Wingdings codes used for list bullets and check boxes.
_WINGDINGS = {
    0x6C: '\u25cf',
    0x6D: '\u274d',
    0x6E: '\u25a0',
    0x6F: '\u25a1',
    0x70: '\u25fb',
    0x71: '\u2751',
    0x72: '\u2752',
    0x75: '\u25c6',
    0x76: '\u2756',
    0x9F: '\u2022',
    0xA7: '\u25aa',
    0xA8: '\u25fb',
    0xD8: '\u27a2',
    0xE8: '\u2794',
    0xFB: '\u2717',
    0xFC: '\u2713',
    0xFD: '\u2612',
    0xFE: '\u2611',
}


def _symbol_table(kind):
    """!
    @brief Built-in encoding of the Symbol, ZapfDingbats or Wingdings font.

    ZapfDingbats codes map mostly to the Dingbats block U+2700, with fixed ranges for
    circled numbers, arrows and card suits.
    @param kind 'symbol', 'wingdings', or any other value for ZapfDingbats.
    @return dict of character code to Unicode text.
    """
    if kind == 'symbol':
        low, high = _SYMBOL
        t = {0x20 + i: ch for i, ch in enumerate(low)}
        t.update({0xA0 + i: ch for i, ch in enumerate(high) if ch != '\ufffd'})
        return t
    if kind == 'wingdings':
        return {**_WINGDINGS, 0x20: ' '}
    t = {0x20: ' '}
    special = {
        0x25: 0x260E,
        0x2A: 0x261B,
        0x2B: 0x261E,
        0x48: 0x2605,
        0x6C: 0x25CF,
        0x6E: 0x25A0,
        0x73: 0x25B2,
        0x74: 0x25BC,
        0x75: 0x25C6,
        0x77: 0x25D7,
    }
    for c in range(0x21, 0x7F):
        t[c] = chr(special.get(c, 0x2700 + c - 0x20))
    for lo, hi, start in (
        (0xA1, 0xA7, 0x2761),
        (0xAC, 0xB5, 0x2460),
        (0xB6, 0xD4, 0x2776),
        (0xD8, 0xEF, 0x2798),
        (0xF1, 0xFE, 0x27B1),
    ):
        for c in range(lo, hi + 1):
            t[c] = chr(start + c - lo)
    t.update(
        {
            0xA8: '\u2663',
            0xA9: '\u2666',
            0xAA: '\u2665',
            0xAB: '\u2660',
            0xD5: '\u2192',
            0xD6: '\u2194',
            0xD7: '\u2195',
        }
    )
    return t


def _symbol_kind(base):
    """!
    @brief Classify a font name as a symbol font.

    The subset prefix before + is ignored and matching ignores case.
    @param base the /BaseFont name.
    @return 'wingdings', 'zapf' or 'symbol' (name starts with symbol), or None.
    """
    name = base.split('+')[-1].lower()
    if 'wingding' in name:
        return 'wingdings'
    if 'dingbat' in name:
        return 'zapf'
    if name.startswith('symbol'):
        return 'symbol'
    return None


def _type1_encoding(data):
    """!
    @brief Built-in encoding from the cleartext part of an embedded Type 1 font program.

    Reads the bytes before eexec, or the first 65536 bytes when eexec is missing or at
    offset 0. "/Encoding StandardEncoding" gives the standard table. Otherwise
    "dup code /name put" entries with code below 256 are read.
    @param data the decoded font program.
    @return dict of character code to Unicode text, or None when no entry is found.
    """
    end = data.find(b'eexec')
    head = data[: end if end > 0 else 65536]
    if re.search(rb'/Encoding\s+StandardEncoding', head):
        return _base_encoding('StandardEncoding')
    table = {}
    for m in re.finditer(rb'dup\s+(\d+)\s*/([^\s/\[\]{}()<>]+)\s+put', head):
        ch = glyph_char(m.group(2).decode('latin-1'))
        if ch is not None and int(m.group(1)) < 256:
            table[int(m.group(1))] = ch
    return table or None


def _truetype_cmap(data):
    """!
    @brief Return {glyph id: text} from the cmap table of a TrueType font program.

    Subtables are tried in the order (3,10), (0,4), (3,1), (0,3), (0,1), (0,0), (3,0);
    the first that yields entries is used. Formats 4 and 12 are read. For a (3,0)
    symbol subtable, codes U+F020-U+F0FF map to code minus 0xF000. A glyph id keeps
    the first code found for it. Codes already covered by an earlier format 4 segment
    or format 12 group are skipped. Format 12 reads at most 100000 groups and
    MAX_FONT_ENTRIES glyphs.
    @param data the decoded font program.
    @return dict of glyph id to one-character text; empty when no cmap is readable.
    """
    if len(data) < 12:
        return {}
    num = struct.unpack('>H', data[4:6])[0]
    cmap = None
    for i in range(num):
        rec = data[12 + 16 * i : 28 + 16 * i]
        if len(rec) == 16 and rec[:4] == b'cmap':
            off, ln = struct.unpack('>II', rec[8:16])
            cmap = data[off : off + ln]
    if not cmap or len(cmap) < 4:
        return {}
    subs = {}
    for i in range(struct.unpack('>H', cmap[2:4])[0]):
        rec = cmap[4 + 8 * i : 12 + 8 * i]
        if len(rec) == 8:
            pid, eid, off = struct.unpack('>HHI', rec)
            subs[(pid, eid)] = off
    out = {}
    for key in ((3, 10), (0, 4), (3, 1), (0, 3), (0, 1), (0, 0), (3, 0)):
        off = subs.get(key)
        if off is None or off + 4 > len(cmap):
            continue
        fmt = struct.unpack('>H', cmap[off : off + 2])[0]
        sym = key == (3, 0)  # symbol fonts map U+F0xx
        if fmt == 4:
            seg = struct.unpack('>H', cmap[off + 6 : off + 8])[0] // 2
            base = off + 14
            if base + 8 * seg + 2 > len(cmap):
                continue
            ends = struct.unpack('>%dH' % seg, cmap[base : base + 2 * seg])
            starts = struct.unpack('>%dH' % seg, cmap[base + 2 * seg + 2 : base + 4 * seg + 2])
            deltas = struct.unpack('>%dH' % seg, cmap[base + 4 * seg + 2 : base + 6 * seg + 2])
            roff = base + 6 * seg + 2
            ranges = struct.unpack('>%dH' % seg, cmap[roff : roff + 2 * seg])
            done = -1  # segments are sorted; codes below done are already mapped
            for j in range(seg):
                lo = max(starts[j], done + 1)
                done = max(done, min(ends[j], 0xFFFE))
                for c in range(lo, min(ends[j], 0xFFFE) + 1):
                    if ranges[j]:
                        p = roff + 2 * j + ranges[j] + 2 * (c - starts[j])
                        if p + 2 > len(cmap):
                            break
                        g = struct.unpack('>H', cmap[p : p + 2])[0]
                        g = (g + deltas[j]) & 0xFFFF if g else 0
                    else:
                        g = (c + deltas[j]) & 0xFFFF
                    if g and g not in out:
                        out[g] = chr(c - 0xF000) if sym and 0xF020 <= c <= 0xF0FF else chr(c)
        elif fmt == 12:
            n = struct.unpack('>I', cmap[off + 12 : off + 16])[0]
            done = -1  # groups are sorted; characters below done are already mapped
            for j in range(min(n, 100000)):
                rec = cmap[off + 16 + 12 * j : off + 28 + 12 * j]
                if len(rec) < 12 or len(out) >= MAX_FONT_ENTRIES:
                    break
                a, b, g = struct.unpack('>III', rec)
                lo = max(a, done + 1)
                done = max(done, min(b, a + 0xFFFF))
                for k in range(lo - a, min(b - a, 0xFFFF) + 1):
                    if a + k <= 0x10FFFF and g + k not in out:
                        out[g + k] = chr(a + k)
        if out:
            break
    return out


def _base_encoding(name):
    """!
    @brief Build a code -> character table for a named simple-font base encoding.

    StandardEncoding: ASCII 0x20-0x7E with right and left single quotes at 0x27 and
    0x60, plus the _STANDARD_HIGH upper half.
    MacRomanEncoding: the mac_roman codec for all 256 codes.
    Any other name, including WinAnsiEncoding and None: the Windows-1252 codec, with
    U+2022 (bullet) at the undefined codes 0x7F, 0x81, 0x8D, 0x8F, 0x90 and 0x9D.
    Names other than WinAnsiEncoding also get the quotes at 0x27 and 0x60.
    @param name encoding name, or None for the default table
    @return dict mapping byte code (int) to a one-character string
    """
    if name == 'StandardEncoding':
        table = {c: chr(c) for c in range(0x20, 0x7F)}
        table[0x27] = '\u2019'
        table[0x60] = '\u2018'
        table.update(_STANDARD_HIGH)
        return table
    codec = 'mac_roman' if name == 'MacRomanEncoding' else 'cp1252'
    table = {}
    for c in range(256):
        try:
            table[c] = bytes([c]).decode(codec)
        except UnicodeDecodeError:
            pass
    if name == 'MacRomanEncoding':
        return table
    for c in (0x7F, 0x81, 0x8D, 0x8F, 0x90, 0x9D):
        table[c] = '\u2022'  # WinAnsiEncoding shows a bullet at undefined codes
    if name != 'WinAnsiEncoding':
        table[0x27] = '\u2019'
        table[0x60] = '\u2018'
    return table


def _roman(n):
    """!
    @brief Convert an integer to an upper-case Roman numeral.

    Values outside 1 <= n < 4000 are returned as decimal digits.
    @param n integer to convert
    @return Roman numeral string, or str(n) when n is out of range
    """
    if not 0 < n < 4000:
        return str(n)
    out = ''
    for v, sym in (
        (1000, 'M'),
        (900, 'CM'),
        (500, 'D'),
        (400, 'CD'),
        (100, 'C'),
        (90, 'XC'),
        (50, 'L'),
        (40, 'XL'),
        (10, 'X'),
        (9, 'IX'),
        (5, 'V'),
        (4, 'IV'),
        (1, 'I'),
    ):
        while n >= v:
            out += sym
            n -= v
    return out


def _text_string(b):
    """!
    @brief Decode a PDF text string to a Python string.

    Bytes starting with the UTF-16BE byte order mark FE FF decode as UTF-16BE.
    Bytes starting with the UTF-8 byte order mark EF BB BF decode as UTF-8.
    Other bytes decode as PDFDocEncoding, approximated by Windows-1252.
    Undecodable bytes become U+FFFD.
    @param b bytes, or an already decoded str (returned unchanged)
    @return decoded string; empty string when b is neither bytes nor str
    """
    if isinstance(b, str):
        return b
    if not isinstance(b, bytes):
        return ''
    if b[:2] == b'\xfe\xff':
        return b[2:].decode('utf-16-be', 'replace')
    if b[:3] == b'\xef\xbb\xbf':
        return b[3:].decode('utf-8', 'replace')
    return b.decode('cp1252', 'replace')


def _clean(t):
    """!
    @brief Normalise decoded glyph text for output.

    No-break space, tab, en space, em space and thin space become a plain space.
    Soft hyphen U+00AD becomes a hyphen-minus.
    Control, format, private-use and surrogate characters (categories Cc, Cf, Co,
    Cs) are removed.
    Characters in U+FB00-U+FDFF and U+FE70-U+FEFE (ligatures and Arabic
    presentation forms) are replaced by their NFKC form.
    @param t input text
    @return cleaned text
    """
    out = []
    for ch in t:
        if ch in '\xa0\t\u2002\u2003\u2009':
            out.append(' ')
        elif ch == '\xad':
            out.append('-')
        elif unicodedata.category(ch) in ('Cc', 'Cf', 'Co', 'Cs'):
            continue
        elif '\ufb00' <= ch <= '\ufdff' or '\ufe70' <= ch <= '\ufefe':
            out.append(unicodedata.normalize('NFKC', ch))  # ligatures, Arabic presentation forms
        else:
            out.append(ch)
    return ''.join(out)


## @brief Code space ranges, as (byte count, low code, high code), of a predefined
## CMap whose codes are all two bytes long.
_CS_TWO = [(2, 0, 0xFFFF)]
## @brief Code space ranges of the predefined Shift-JIS (RKSJ) CMaps.
_CS_RKSJ = [(1, 0, 0x80), (1, 0xA0, 0xDF), (2, 0x8140, 0x9FFC), (2, 0xE040, 0xFCFC)]
## @brief Code space ranges of the predefined EUC CMaps (EUC-H, GB-EUC, KSC-EUC, ...).
_CS_EUC = [(1, 0, 0x80), (2, 0x8EA0, 0x8EDF), (2, 0xA1A1, 0xFEFE)]
## @brief Code space ranges of the predefined GBK, Big Five and UHC CMaps.
_CS_LEAD81 = [(1, 0, 0x80), (2, 0x8140, 0xFEFE)]
## @brief Code space ranges of the predefined GBK2K CMaps (one, two and four bytes).
_CS_GBK2K = [(1, 0, 0x80), (2, 0x8140, 0xFEFE), (4, 0x81308130, 0xFE39FE39)]


def _predefined_codespace(name):
    """!
    @brief Return the code space ranges of a predefined CMap name.

    Two bytes: Identity, the ISO-2022 CMaps (H, V, Ext, Add, NWP, KSC, GB, CNS1, CNS2)
    and the Unicode UCS2 and UTF16 CMaps. Four bytes: the UTF32 CMaps. One byte:
    Hankaku, Hiragana, Katakana, Roman and WP-Symbol. Mixed widths: RKSJ (Shift-JIS),
    GBK2K, GBK, Big Five (B5) and UHC CMaps, then the other EUC CMaps.
    @param name CMap name, e.g. '90ms-RKSJ-H'
    @return list of (byte count, low code, high code), or None for an unknown name
    """
    base = re.sub(r'-[HV]$', '', name)
    if re.search(r'UCS2|UTF16', name) or base in (
        'Identity',
        'H',
        'V',
        'Ext',
        'Add',
        'NWP',
        'KSC',
        'GB',
        'CNS1',
        'CNS2',
    ):
        return _CS_TWO
    if 'UTF32' in name:
        return [(4, 0, 0x10FFFF)]
    if base in ('Hankaku', 'Hiragana', 'Katakana', 'Roman', 'WP-Symbol'):
        return [(1, 0, 0xFF)]
    if 'RKSJ' in name:
        return _CS_RKSJ
    if 'GBK2K' in name:
        return _CS_GBK2K
    if re.search(r'GBK|B5|UHC', name):
        return _CS_LEAD81
    if 'EUC' in name:
        return _CS_EUC
    return None


class Font:
    """!
    @brief Text decoding and glyph widths of one PDF font dictionary.

    Handles simple fonts (Type1, MMType1, TrueType, Type3) and composite Type0 (CID)
    fonts. Style flags come from the BaseFont name and the FontDescriptor.
    @par Attributes
    - subtype: font Subtype name (Type0, Type1, TrueType, Type3, ...)
    - bold: True when the font is bold
    - italic: True when the font is italic or oblique
    - mono: True when the font is monospaced
    - serif: True when the base font name suggests a serif face
    - cid: True for a Type0 (composite) font
    - tounicode: code -> Unicode string from the ToUnicode CMap
    - cid_text: CID -> text from the cmap table of an embedded TrueType program, used
      for composite fonts without ToUnicode (codes map to CIDs through cidmap)
    - codespace: list of (byte count, low code, high code) code space ranges
    - widths: code -> glyph width in 1/1000 em
    - default_width: width in 1/1000 em for codes missing from widths
    - encoding: code -> character table of a simple font
    - estimated: True when no widths are known and default_width is used
    - fmat: normalised Type 3 FontMatrix (6 values) when it rotates or flips, else None
    - cidmap: code -> CID from an embedded encoding CMap
    - unicode_codes: True for a predefined UCS2 or UTF16 encoding CMap
    - symbol: symbol table kind from the base font name ('symbol', 'zapf',
      'wingdings') or None
    - two_byte: True when every code is 2 bytes (composite fonts only)
    - name: BaseFont name used in problem reports
    - _cache: code -> decoded text cache
    - _text_total: characters stored into tounicode so far, compared with MAX_FONT_TEXT
    - _limited: names of the limits already reported for this font
    """

    def __init__(self, doc, fd):
        """!
        @brief Read a font dictionary and build its decoding and width tables.

        Style: bold from the name (bold, black, heavy, semibold, demi), FontDescriptor
        flag bit 19 (ForceBold) or FontWeight >= 600. Italic from the name (italic,
        oblique) or flag value 64. Monospace from the name (courier, mono, consol, menlo,
        typewriter, cmtt) or flag value 1. Serif from the name, unless it contains "sans".
        Default width: 600 for monospace, 500 otherwise, in 1/1000 em.
        A ToUnicode stream is parsed first; a parse error is recorded as 'malformed'.
        Type0 fonts: an embedded encoding CMap fills codespace and cidmap. A predefined
        CMap name sets the code space of its family (see _predefined_codespace); UCS2,
        UTF16 and UTF32 names also set unicode_codes. An unknown name keeps the code
        space of the ToUnicode CMap, or, without one, reads two-byte codes and records
        an 'unsupported' problem. Without any text mapping, the cmap
        table of an embedded TrueType program (FontFile2) of the descendant font is
        used, through the CIDToGIDMap stream when present. Widths come from DW (default
        1000) and the W array; a range in W covers at most 65536 codes.
        Simple fonts: Widths starting at FirstChar; Type 3 widths are scaled by
        FontMatrix[0] * 1000. Without Widths, built-in standard widths for codes 32 and
        up are used when known, else estimated is set. The encoding is a symbol table,
        the built-in encoding of an embedded Type 1 program, or a named base encoding,
        then overlaid with the Differences array.
        @param doc document object used to resolve references and decode streams
        @param fd font dictionary or a reference to it; anything else is treated as empty
        """
        fd = doc.resolve(fd)
        if not isinstance(fd, dict):
            fd = {}
        r = doc.resolve
        ## font Subtype name (Type0, Type1, TrueType, Type3, ...)
        self.subtype = r(fd.get('Subtype'))
        base = str(r(fd.get('BaseFont')) or '')
        ## True when the font is bold
        self.bold = bool(re.search(r'bold|black|heavy|semibold|demi', base, re.I))
        ## True when the font is italic or oblique
        self.italic = bool(re.search(r'italic|oblique', base, re.I))
        ## True when the font is monospaced
        self.mono = bool(re.search(r'courier|mono|consol|menlo|typewriter|cmtt', base, re.I))
        ## True when the base font name suggests a serif face
        self.serif = (
            bool(re.search(r'times|serif|roman|georgia|garamond|minion|cambria|cmr', base, re.I))
            and 'sans' not in base.lower()
        )
        desc = r(fd.get('FontDescriptor'))
        if isinstance(desc, dict):
            flags = r(desc.get('Flags')) or 0
            weight = r(desc.get('FontWeight')) or 0
            if isinstance(flags, int):
                self.mono = self.mono or bool(flags & 1)
                self.italic = self.italic or bool(flags & 64)
                self.bold = self.bold or bool(flags & (1 << 18))
            if isinstance(weight, (int, float)) and weight >= 600:
                self.bold = True
        ## True for a Type0 (composite) font
        self.cid = self.subtype == 'Type0'
        ## code -> Unicode string from the ToUnicode CMap
        self.tounicode = {}
        ## list of (byte count, low code, high code) code space ranges
        self.codespace = []
        ## code -> glyph width in 1/1000 em
        self.widths = {}
        ## width in 1/1000 em for codes missing from widths
        self.default_width = 600.0 if self.mono else 500.0
        ## code -> character table of a simple font
        self.encoding = {}
        ## True when no widths are known and default_width is used
        self.estimated = False
        ## normalised Type 3 FontMatrix (6 values) when it rotates or flips, else None
        self.fmat = None
        ## code -> CID from an embedded encoding CMap
        self.cidmap = {}
        ## CID -> text from the cmap table of an embedded TrueType program, used
        ## for composite fonts without ToUnicode (codes map to CIDs through cidmap)
        self.cid_text = {}
        ## True for a predefined UCS2 or UTF16 encoding CMap
        self.unicode_codes = False
        ## symbol table kind from the base font name ('symbol', 'zapf',
        ## 'wingdings') or None
        self.symbol = _symbol_kind(base)
        ## code -> decoded text cache
        self._cache = {}
        ## BaseFont name used in problem reports
        self.name = base or '(unnamed)'
        ## characters stored into tounicode so far, compared with MAX_FONT_TEXT
        self._text_total = 0
        ## names of the limits already reported for this font
        self._limited = set()
        tu = r(fd.get('ToUnicode'))
        if isinstance(tu, Stream):
            try:
                self._parse_cmap(doc.decoded(tu))
            except Exception as exc:
                self._bad('ToUnicode CMap', exc)
        if isinstance(desc, dict):
            ff = r(desc.get('FontFile')) or r(desc.get('FontFile2'))
        else:
            ff = None
        if self.cid:
            dfs = r(fd.get('DescendantFonts'))
            dsc = r(dfs[0]) if isinstance(dfs, list) and dfs else {}
            if not isinstance(dsc, dict):
                dsc = {}
            enc = r(fd.get('Encoding'))
            if isinstance(enc, Stream):
                self.codespace = []
                try:
                    self._parse_cmap(doc.decoded(enc), encoding=True)
                except Exception as exc:
                    self._bad('encoding CMap', exc)
            elif isinstance(enc, str):
                # predefined CMap: its own code space replaces the one declared in the
                # ToUnicode CMap; an unknown name keeps the ToUnicode code space
                cs = _predefined_codespace(enc)
                if cs is not None:
                    self.codespace = list(cs)
                elif not self.codespace:
                    problem(
                        'unsupported',
                        'font %s: encoding %s parsed as two-byte codes' % (self.name, enc),
                    )
                self.unicode_codes = bool(re.search(r'UCS2|UTF16|UTF32', enc))
            if not self.tounicode and not self.unicode_codes:
                # no text mapping: try the cmap table of an embedded TrueType program
                ddesc = r(dsc.get('FontDescriptor'))
                ff2 = r(ddesc.get('FontFile2')) if isinstance(ddesc, dict) else None
                if isinstance(ff2, Stream):
                    try:
                        gids = _truetype_cmap(doc.decoded(ff2))
                        c2g = r(dsc.get('CIDToGIDMap'))
                        c2g = doc.decoded(c2g) if isinstance(c2g, Stream) else None
                        if c2g:
                            n = len(c2g) // 2
                            if n > MAX_FONT_ENTRIES:
                                n = MAX_FONT_ENTRIES
                                self._limit('MAX_FONT_ENTRIES (CIDToGIDMap)')
                            for cid in range(n):
                                gid = (c2g[2 * cid] << 8) | c2g[2 * cid + 1]
                                if gid in gids:
                                    self.cid_text[cid] = gids[gid]
                        else:  # /Identity: CID = glyph id
                            self.cid_text = gids
                    except Exception as exc:
                        self._bad('TrueType cmap', exc)
            dw = r(dsc.get('DW'))
            self.default_width = float(dw) if isinstance(dw, (int, float)) else 1000.0
            w = r(dsc.get('W'))
            w = [r(x) for x in w] if isinstance(w, list) else []
            i = 0
            while i < len(w) and len(self.widths) < MAX_FONT_ENTRIES:
                a = w[i]
                b = w[i + 1] if i + 1 < len(w) else None
                if isinstance(b, list) and type(a) is int:
                    for j, x in enumerate(b[: MAX_FONT_ENTRIES - len(self.widths)]):
                        x = r(x)
                        if isinstance(x, (int, float)):
                            self.widths[a + j] = float(x)
                    i += 2
                elif (
                    i + 2 < len(w)
                    and type(b) is int
                    and type(a) is int
                    and isinstance(w[i + 2], (int, float))
                ):
                    c = float(w[i + 2])
                    room = MAX_FONT_ENTRIES - len(self.widths)
                    for k in range(a, min(b, a + 65535, a + room - 1) + 1):
                        self.widths[k] = c
                    i += 3
                else:
                    break
            if len(self.widths) >= MAX_FONT_ENTRIES and i < len(w):
                self._limit('MAX_FONT_ENTRIES (W)')
            ## True when every code is 2 bytes (composite fonts only)
            self.two_byte = not self.codespace or all(n == 2 for n, _, _ in self.codespace)
        else:
            self.two_byte = False
            scale = 1.0
            if self.subtype == 'Type3':
                fm = r(fd.get('FontMatrix'))
                if isinstance(fm, list) and len(fm) == 6:
                    fm = [float(r(v)) for v in fm[:4]]
                    scale = fm[0] * 1000.0
                    det = math.sqrt(abs(fm[0] * fm[3] - fm[1] * fm[2]))
                    if det > 0 and (fm[1] or fm[2] or fm[0] < 0 or fm[3] < 0):
                        # glyph orientation only; the em size of glyph space is unknown
                        self.fmat = [v / det for v in fm] + [0.0, 0.0]
            fc = r(fd.get('FirstChar'))
            fc = fc if isinstance(fc, int) else 0
            ws = r(fd.get('Widths'))
            ws = ws if isinstance(ws, list) else []
            if len(ws) > MAX_FONT_ENTRIES:
                ws = ws[:MAX_FONT_ENTRIES]
                self._limit('MAX_FONT_ENTRIES (Widths)')
            for i, x in enumerate(ws):
                x = r(x)
                if isinstance(x, (int, float)):
                    self.widths[fc + i] = float(x) * scale
            if not self.widths:
                std = _std_widths(base)
                if std:
                    self.widths = {32 + i: float(v) for i, v in enumerate(std)}
                else:
                    self.estimated = True
            enc = r(fd.get('Encoding'))
            basename, diffs = None, None
            if isinstance(enc, str):
                basename = enc
            elif isinstance(enc, dict):
                basename = r(enc.get('BaseEncoding'))
                diffs = r(enc.get('Differences'))
            builtin = None
            if basename is None:
                if self.symbol and self.subtype != 'Type3':
                    builtin = _symbol_table(self.symbol)
                elif self.subtype in ('Type1', 'MMType1'):
                    if (
                        isinstance(ff, Stream)
                        and isinstance(desc, dict)
                        and desc.get('FontFile') is not None
                    ):
                        try:
                            builtin = _type1_encoding(doc.decoded(ff))
                        except Exception as exc:
                            self._bad('Type 1 font program', exc)
                            builtin = None
                    if builtin is None:
                        basename = 'StandardEncoding'
            if builtin is not None:
                self.encoding = _base_encoding(None)
                self.encoding.update(builtin)
            else:
                self.encoding = _base_encoding(basename)
            if isinstance(diffs, list):
                code = 0
                for x in diffs:
                    x = r(x)
                    if isinstance(x, int):
                        code = x
                    elif isinstance(x, Name):
                        ch = glyph_char(str(x))
                        if ch is not None:
                            if len(self.encoding) < MAX_FONT_ENTRIES or code in self.encoding:
                                self.encoding[code] = ch
                            else:
                                self._limit('MAX_FONT_ENTRIES (Differences)')
                        code += 1

    def _bad(self, what, exc):
        """!
        @brief Record a font part that could not be read as a 'malformed' problem.
        @param what name of the part, e.g. 'ToUnicode CMap'
        @param exc the exception raised while reading it
        """
        problem(
            'malformed', 'font %s: %s not readable (%s)' % (self.name, what, type(exc).__name__)
        )

    def _limit(self, which):
        """!
        @brief Record once per font that a limit cut the font data.
        @param which name of the limit, e.g. 'MAX_FONT_TEXT'
        """
        if which not in self._limited:
            self._limited.add(which)
            problem('limit', 'font %s: %s limit reached' % (self.name, which))

    def _put_text(self, code, u):
        """!
        @brief Store the text of one code in tounicode within the font limits.

        Fails when tounicode holds MAX_FONT_ENTRIES codes and code is new, or when
        MAX_FONT_TEXT characters have been stored; the limit is then reported once.
        @param code character code
        @param u text, at most MAX_CMAP_DEST characters
        @return True when stored, False when a limit was reached
        """
        if len(self.tounicode) >= MAX_FONT_ENTRIES and code not in self.tounicode:
            self._limit('MAX_FONT_ENTRIES (ToUnicode)')
            return False
        if self._text_total + len(u) > MAX_FONT_TEXT:
            self._limit('MAX_FONT_TEXT')
            return False
        self._text_total += len(u)
        self.tounicode[code] = u
        return True

    def _parse_cmap(self, data, encoding=False):
        """!
        @brief Parse a CMap stream into this font's tables.

        As a ToUnicode CMap (encoding=False): bfchar and bfrange entries fill tounicode;
        codespace ranges are added only while cidmap is empty. A bfrange with a string
        destination increments the last character of that string across the range.
        As an encoding CMap (encoding=True): codespace ranges fill codespace, cidrange
        and cidchar entries fill cidmap.
        Ranges wider than 65536 codes are skipped (bfrange) or truncated (cidrange).
        Destinations are cut to MAX_CMAP_DEST characters. tounicode and cidmap stop
        growing at MAX_FONT_ENTRIES codes, and tounicode at MAX_FONT_TEXT characters
        stored; each cut is reported once per font as a 'limit' problem.
        @param data decoded CMap stream bytes
        @param encoding True to read an embedded encoding CMap instead of a ToUnicode CMap
        """
        lx = Lexer(data)
        ops = []
        while True:
            t = lx.token()
            if t is None:
                break
            if isinstance(t, Op) and t not in ('[', '<<'):
                kw = str(t)
                if kw == 'endcodespacerange':
                    if encoding or not self.cidmap:
                        for i in range(0, len(ops) - 1, 2):
                            a, b = ops[i], ops[i + 1]
                            if isinstance(a, bytes) and isinstance(b, bytes):
                                self.codespace.append(
                                    (len(a), int.from_bytes(a, 'big'), int.from_bytes(b, 'big'))
                                )
                elif encoding and kw == 'endcidrange':
                    for i in range(0, len(ops) - 2, 3):
                        lo, hi, cid = ops[i], ops[i + 1], ops[i + 2]
                        if isinstance(lo, bytes) and isinstance(hi, bytes) and isinstance(cid, int):
                            lo, hi = int.from_bytes(lo, 'big'), int.from_bytes(hi, 'big')
                            room = MAX_FONT_ENTRIES - len(self.cidmap)
                            if hi - lo >= room:
                                self._limit('MAX_FONT_ENTRIES (cidrange)')
                            for k in range(min(hi - lo, 65535, room - 1) + 1):
                                self.cidmap[lo + k] = cid + k
                elif encoding and kw == 'endcidchar':
                    for i in range(0, len(ops) - 1, 2):
                        src, cid = ops[i], ops[i + 1]
                        if isinstance(src, bytes) and isinstance(cid, int):
                            src = int.from_bytes(src, 'big')
                            if len(self.cidmap) < MAX_FONT_ENTRIES or src in self.cidmap:
                                self.cidmap[src] = cid
                            else:
                                self._limit('MAX_FONT_ENTRIES (cidchar)')
                elif kw == 'endbfchar':
                    for i in range(0, len(ops) - 1, 2):
                        src, dst = ops[i], ops[i + 1]
                        if isinstance(src, bytes):
                            u = self._u(dst)
                            if u is not None and not self._put_text(int.from_bytes(src, 'big'), u):
                                break
                elif kw == 'endbfrange':
                    for i in range(0, len(ops) - 2, 3):
                        lo, hi, dst = ops[i], ops[i + 1], ops[i + 2]
                        if not isinstance(lo, bytes) or not isinstance(hi, bytes):
                            continue
                        lo, hi = int.from_bytes(lo, 'big'), int.from_bytes(hi, 'big')
                        if hi < lo or hi - lo > 65535:
                            continue
                        if isinstance(dst, list):
                            for j, x in enumerate(dst[: hi - lo + 1]):
                                u = self._u(x)
                                if u is not None and not self._put_text(lo + j, u):
                                    break
                        elif isinstance(dst, bytes):
                            u = self._u(dst)
                            if u:
                                last = ord(u[-1])
                                for k in range(min(hi - lo, 0x10FFFF - last) + 1):
                                    if not self._put_text(lo + k, u[:-1] + chr(last + k)):
                                        break
                ops = []
            else:
                ops.append(lx.build(t))

    @staticmethod
    def _u(dst):
        """!
        @brief Decode a CMap destination string to text.

        A 1-byte string maps to the character with that code. Longer strings decode as
        UTF-16BE, with U+FFFD for invalid data, and are cut to MAX_CMAP_DEST characters.
        @param dst destination value from the CMap
        @return decoded string, or None when dst is not bytes
        """
        if not isinstance(dst, bytes):
            return None
        if len(dst) == 1:
            return chr(dst[0])
        return dst[: 4 * MAX_CMAP_DEST].decode('utf-16-be', errors='replace')[:MAX_CMAP_DEST]

    def codes(self, s):
        """!
        @brief Split a shown string into character codes.

        Simple fonts: one code per byte.
        Composite fonts with 2-byte codes: big-endian byte pairs; a trailing odd byte is
        padded with a zero low byte.
        Other composite fonts: the shortest prefix of 1 to 4 bytes that lies in a
        codespace range; when none matches, one byte.
        Codes are produced one at a time. After MAX_SHOW_CODES codes the rest of the
        string is dropped and a 'limit' problem is recorded.
        @param s string operand bytes
        @return iterator of (code, byte count) tuples
        """
        if not self.cid:
            it = ((b, 1) for b in s)
        elif self.two_byte:
            it = (
                ((s[i] << 8) | (s[i + 1] if i + 1 < len(s) else 0), 2) for i in range(0, len(s), 2)
            )
        else:
            it = self._var_codes(s)
        k = 0
        for item in it:
            if k >= MAX_SHOW_CODES:
                problem(
                    'limit',
                    'font %s: string of %d bytes cut at MAX_SHOW_CODES (%d codes)'
                    % (self.name, len(s), MAX_SHOW_CODES),
                )
                return
            k += 1
            yield item

    def _var_codes(self, s):
        """!
        @brief Split a string into variable-width codes using the code space ranges.

        Each code is the shortest prefix of 1 to 4 bytes that lies in a codespace
        range; when none matches, one byte.
        @param s string operand bytes
        @return iterator of (code, byte count) tuples
        """
        i = 0
        while i < len(s):
            for n in (1, 2, 3, 4):
                c = int.from_bytes(s[i : i + n], 'big')
                if any(nb == n and lo <= c <= hi for nb, lo, hi in self.codespace):
                    break
            else:
                n = 1
                c = s[i]
            yield c, n
            i += n

    def text(self, code):
        """!
        @brief Return the Unicode text of one character code.

        Lookup order: tounicode; then the code itself for unicode_codes fonts (U+FFFD for
        surrogates and values above U+10FFFF); then cid_text, reached through cidmap when
        the font has an embedded encoding CMap; then U+FFFD for other composite fonts;
        then the simple-font encoding (empty string when missing).
        A single mapped character in U+F020-U+F0FF is translated through the symbol table
        of the font (Symbol when the font has no symbol kind).
        The result passes through _clean and is cached per code.
        @param code character code
        @return decoded text, possibly empty
        """
        t = self._cache.get(code)
        if t is None:
            if code in self.tounicode:
                t = self.tounicode[code]
                if len(t) == 1 and 0xF020 <= ord(t) <= 0xF0FF:
                    # symbol fonts map their glyphs to the private use area U+F020-U+F0FF
                    t = _symbol_table(self.symbol or 'symbol').get(ord(t) - 0xF000, t)
            elif self.unicode_codes:
                t = chr(code) if code < 0xD800 or 0xE000 <= code <= 0x10FFFF else '\ufffd'
            elif self.cid_text:
                cid = self.cidmap.get(code) if self.cidmap else code
                t = self.cid_text.get(cid, '\ufffd')
            elif self.cid:
                t = '\ufffd'
            else:
                t = self.encoding.get(code, '')
            t = _clean(t)
            self._cache[code] = t
        return t

    def width(self, code):
        """!
        @brief Return the advance width of a character code in em.

        With an embedded encoding CMap the code is first mapped to its CID; codes missing
        from that map use default_width.
        @param code character code
        @return width in em (widths entry or default_width, divided by 1000)
        """
        if self.cidmap:
            code = self.cidmap.get(code, -1)
        return self.widths.get(code, self.default_width) / 1000.0


# ---------------------------------------------------------------------------
# Content stream interpreter
# ---------------------------------------------------------------------------


def mmul(a, b):
    """!
    @brief Multiply two PDF affine matrices.

    Matrices are 6-element lists [a b c d e f]. The result applies a first, then b.
    @param a first matrix
    @param b second matrix
    @return product matrix a x b as a 6-element list
    """
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def apply(m, x, y):
    """!
    @brief Transform a point by a PDF affine matrix.
    @param m matrix [a b c d e f]
    @param x x coordinate
    @param y y coordinate
    @return transformed (x, y) tuple
    """
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


class GState:
    """!
    @brief Graphics and text state tracked by the content stream interpreter.

    Colours are (r, g, b) tuples with components in 0..1.
    @par Attributes
    - ctm: current transformation matrix, 6 values
    - fill: fill colour
    - stroke: stroke colour
    - fcs: fill colour space descriptor from _colorspace, or None for device colours
    - scs: stroke colour space descriptor, or None
    - lw: line width in user space units
    - font: current Font, or None
    - fs: font size (Tf operand)
    - tc: character spacing (Tc)
    - tw: word spacing (Tw)
    - th: horizontal scaling as a fraction (Tz / 100)
    - tl: text leading (TL)
    - rise: text rise (Ts)
    - tr: text rendering mode (Tr)
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = (
        'ctm',
        'fill',
        'stroke',
        'fcs',
        'scs',
        'lw',
        'font',
        'fs',
        'tc',
        'tw',
        'th',
        'tl',
        'rise',
        'tr',
    )

    def __init__(self):
        """!
        @brief Set the initial state.

        Identity CTM, black fill and stroke, line width 1.0, no font, font size 0,
        spacing 0, horizontal scaling 1.0, leading 0, rise 0, rendering mode 0.
        """
        ## current transformation matrix, 6 values
        self.ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        ## fill colour
        self.fill = (0.0, 0.0, 0.0)
        ## stroke colour
        self.stroke = (0.0, 0.0, 0.0)
        ## fill colour space descriptor from _colorspace, or None for device colours
        self.fcs = None
        ## stroke colour space descriptor, or None
        self.scs = None
        ## line width in user space units
        self.lw = 1.0
        ## current Font, or None
        self.font = None
        ## font size (Tf operand)
        self.fs = 0.0
        ## character spacing (Tc)
        self.tc = 0.0
        ## word spacing (Tw)
        self.tw = 0.0
        ## horizontal scaling as a fraction (Tz / 100)
        self.th = 1.0
        ## text leading (TL)
        self.tl = 0.0
        ## text rise (Ts)
        self.rise = 0.0
        ## text rendering mode (Tr)
        self.tr = 0

    def copy(self):
        """!
        @brief Return a shallow copy of this state for q/Q save and restore.
        @return new GState with the same attribute values
        """
        g = GState.__new__(GState)
        for k in GState.__slots__:
            setattr(g, k, getattr(self, k))
        return g


class Char:
    """!
    @brief One shown glyph with its position and style.

    Coordinates are in the page space given by the initial CTM, in points.
    @par Attributes
    - x, y: glyph origin, including text rise
    - x2: x coordinate of the origin after the glyph advance
    - size: font size scaled by the text and current matrices, in points
    - text: decoded Unicode text
    - bold, italic, mono, serif: style flags copied from the font
    - color: fill colour (r, g, b) in 0..1
    - upright: True when the glyph x axis and y axis both point along the page axes
      (unit vector components > 0.995)
    - u: unit vector of the glyph x axis
    - v: unit vector of the glyph y axis
    - invisible: True for text rendering mode 3 or 7
    - est: True when the font widths are estimated
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = (
        'x',
        'y',
        'x2',
        'size',
        'text',
        'bold',
        'italic',
        'mono',
        'serif',
        'color',
        'upright',
        'u',
        'v',
        'invisible',
        'est',
    )


class Shape:
    """!
    @brief One painted path or image placement on a page.

    Coordinates are in page space points.
    @par Attributes
    - kind: 'path' or 'image'
    - bbox: (x0, y0, x1, y1); for paths padded by half the line width
    - seq: paint order index within the page
    - segs: path segments ('M', x, y), ('L', x, y), ('C', x1, y1, x2, y2, x3, y3),
      ('Z',); empty for images
    - fill: fill colour (r, g, b), or None when not filled
    - stroke: stroke colour (r, g, b), or None when not stroked
    - lw: stroke width scaled by the CTM; 0 when not stroked
    - evenodd: True for the even-odd fill rule
    - rect: True for an axis-aligned rectangle path
    - axis: True when the path has no curves and no diagonal lines
    - white: True when every used paint colour has all components above 0.95
    - stream: image XObject stream (images only)
    - resources: resource dictionary active at the Do operator (images only)
    - matrix: CTM at the Do operator (images only)
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = (
        'kind',
        'bbox',
        'seq',
        'segs',
        'fill',
        'stroke',
        'lw',
        'evenodd',
        'rect',
        'axis',
        'white',
        'stream',
        'resources',
        'matrix',
    )


## @brief Path painting operators mapped to (stroke, fill, even-odd, close) flags.
##
## Each flag is 0 or 1. The end-path operator n is handled separately and paints
## nothing.
PAINT = {
    'S': (1, 0, 0, 0),
    's': (1, 0, 0, 1),
    'f': (0, 1, 0, 0),
    'F': (0, 1, 0, 0),
    'f*': (0, 1, 1, 0),
    'B': (1, 1, 0, 0),
    'B*': (1, 1, 1, 0),
    'b': (1, 1, 0, 1),
    'b*': (1, 1, 1, 1),
}


def _lab_rgb(lab, wp):
    """!
    @brief Convert one CIE L*a*b* colour to sRGB.

    Uses the white point of the colour space for the XYZ step and the sRGB (D65)
    matrix without chromatic adaptation, so colours with a D50 white point are
    slightly shifted.
    @param lab (L*, a*, b*) with L* in 0..100
    @param wp white point (Xw, Yw, Zw)
    @return (r, g, b) with components in 0..1
    """
    L, a, b = lab
    fy = (L + 16) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0
    f = lambda t: t**3 if t > 6 / 29.0 else 3 * (6 / 29.0) ** 2 * (t - 4 / 29.0)
    x, y, z = wp[0] * f(fx), wp[1] * f(fy), wp[2] * f(fz)
    lin = (
        3.2406 * x - 1.5372 * y - 0.4986 * z,
        -0.9689 * x + 1.8758 * y + 0.0415 * z,
        0.0557 * x - 0.2040 * y + 1.0570 * z,
    )
    out = []
    for c in lin:
        c = max(0.0, min(1.0, c))
        out.append(12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)
    return tuple(out)


def _index_rgb(cs, i):
    """!
    @brief Look up one entry of an Indexed colour space as an RGB tuple.
    @param cs colour space descriptor from _colorspace with kind 'indexed'
    @param i palette index; clamped to 0..hival
    @return (r, g, b) with components in 0..1
    """
    base, hival, lookup = cs[2], cs[3], cs[4]
    i = max(0, min(hival, i))
    n = base[1]
    comp = [v / 255.0 for v in lookup[i * n : (i + 1) * n].ljust(n, b'\0')]
    if base[0] == 'lab':  # bytes span L* 0..100 and the a*, b* ranges
        rng = base[3]
        return _lab_rgb(
            (
                comp[0] * 100.0,
                rng[0] + comp[1] * (rng[1] - rng[0]),
                rng[2] + comp[2] * (rng[3] - rng[2]),
            ),
            base[2],
        )
    if base[0] == 'indexed':
        return (0.5, 0.5, 0.5)  # an Indexed base is not allowed
    return _color(comp, base)


def _color(ops, cs=None):
    """!
    @brief Convert colour operator operands to an RGB tuple.

    With a known colour space: Separation and DeviceN tints t give gray 1 - max(t)
    (full tint is the darkest ink); Indexed looks up the palette; Lab converts with
    _lab_rgb; Pattern gives mid gray (0.5, 0.5, 0.5). Otherwise the operand count
    decides: 1 number gray, 3 numbers RGB, 4 numbers CMYK, converted as
    ((1-c)(1-k), (1-m)(1-k), (1-y)(1-k)). Numbers are clamped to 0..1 except for
    Indexed and Lab. A pattern name without numbers gives mid gray. Any other
    operand count gives black.
    @param ops operand list of the colour operator
    @param cs colour space descriptor from _colorspace, or None
    @return (r, g, b) tuple with components in 0..1
    """
    nums = [float(x) for x in ops if isinstance(x, (int, float)) and not isinstance(x, bool)]
    nums = [v for v in nums if math.isfinite(v)]
    kind = cs[0] if cs else None
    if kind == 'pattern' or (not nums and any(isinstance(x, Name) for x in ops)):
        return (0.5, 0.5, 0.5)  # pattern colour
    if kind == 'indexed' and nums:
        return _index_rgb(cs, int(nums[0]))
    if kind == 'lab' and len(nums) >= 3:
        return _lab_rgb(nums[:3], cs[2])
    nums = [min(1.0, max(0.0, v)) for v in nums]
    if kind in ('sep', 'devn') and nums:
        return (1.0 - max(nums),) * 3
    if len(nums) == 1:
        return (nums[0],) * 3
    if len(nums) == 3:
        return tuple(nums)
    if len(nums) == 4:
        c, m, y, k = nums
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (0.0, 0.0, 0.0)


def _path_flags(segs):
    """!
    @brief Classify a path as rectangle and as axis-aligned.

    A line segment counts as diagonal when both its x and y change by more than
    0.5 points. Any curve makes the path not axis-aligned.
    A rectangle is an axis-aligned path with segment pattern MLLLZ, MLLLLZ, MLLLL or
    MLLL and at least 4 points.
    @param segs path segments as built by PageContent.run
    @return (is_axis_aligned_rectangle, all_segments_axis_aligned_lines)
    """
    axis = True
    pts = []
    cur = None
    for s in segs:
        if s[0] == 'C':
            axis = False
            cur = s[5:7]
        elif s[0] == 'L':
            if cur is not None and abs(cur[0] - s[1]) > 0.5 and abs(cur[1] - s[2]) > 0.5:
                axis = False
            cur = s[1:3]
            pts.append(cur)
        elif s[0] == 'M':
            cur = s[1:3]
            pts.append(cur)
    kinds = ''.join(s[0] for s in segs)
    rect = axis and kinds in ('MLLLZ', 'MLLLLZ', 'MLLLL', 'MLLL') and len(pts) >= 4
    return rect, axis


class PageContent:
    """!
    @brief Content stream interpreter that collects the glyphs and shapes of a page.

    Coordinates of the collected objects are in the space of the CTM passed in.
    @par Attributes
    - doc: document object used to resolve references and decode streams
    - fonts: shared cache id(font dictionary) -> (Font, font dictionary)
    - chars: collected Char objects
    - shapes: collected Shape objects
    - seq: next paint order index
    - _active: ids of Form XObjects currently being run (recursion guard)
    - no_shapes: True while annotation appearances run; then only text is collected
    """

    def __init__(self, doc, fonts):
        """!
        @brief Create an empty interpreter.
        @param doc document object used to resolve references and decode streams
        @param fonts font cache dict shared between pages
        """
        ## document object used to resolve references and decode streams
        self.doc = doc
        ## shared cache id(font dictionary) -> (Font, font dictionary)
        self.fonts = fonts
        ## collected Char objects
        self.chars = []
        ## collected Shape objects
        self.shapes = []
        ## next paint order index
        self.seq = 0
        ## ids of Form XObjects currently being run (recursion guard)
        self._active = set()
        ## Form XObject runs executed so far on this page (limit MAX_FORM_RUNS)
        self.form_runs = 0
        ## content stream operators executed so far on this page (limit MAX_PAGE_OPS)
        self.op_count = 0
        ## True while annotation appearances run; then only text is collected
        self.no_shapes = False

    def _ext_gstate(self, res, name, gs):
        """!
        @brief Apply the font (/Font [font size]) and line width (/LW) of an ExtGState.
        @param res resource dictionary
        @param name ExtGState resource name from the gs operator
        @param gs graphics state to change
        """
        r = self.doc.resolve
        egs = r(res.get('ExtGState'))
        d = r(egs.get(name)) if isinstance(egs, dict) else None
        if not isinstance(d, dict):
            return
        lw = r(d.get('LW'))
        if isinstance(lw, (int, float)) and not isinstance(lw, bool):
            gs.lw = float(lw)
        f = r(d.get('Font'))
        if isinstance(f, list) and len(f) == 2 and isinstance(r(f[1]), (int, float)):
            gs.font = self._font_dict(r(f[0]))
            gs.fs = float(r(f[1]))

    def _font_dict(self, fd):
        """!
        @brief Return the Font for a font dictionary, using the shared cache.

        A font dictionary that raises during parsing is replaced by an empty Font.
        The cache keys on id() of the dictionary and stores the dictionary to keep it
        alive.
        @param fd font dictionary, or None
        @return Font object
        """
        key = id(fd)
        if key not in self.fonts:
            try:
                font = Font(self.doc, fd)
            except Exception:  # malformed font dictionary
                font = Font(self.doc, None)
            self.fonts[key] = (font, fd)  # keep fd alive so id() stays unique
        return self.fonts[key][0]

    def _font(self, res, name):
        """!
        @brief Return the Font for a font resource name, using the shared cache.

        See _font_dict.
        @param res resource dictionary
        @param name font resource name from the Tf operator
        @return Font object
        """
        fonts = self.doc.resolve(res.get('Font')) or {}
        fd = self.doc.resolve(fonts.get(name)) if isinstance(fonts, dict) else None
        return self._font_dict(fd)

    def run(self, data, res, gs, depth=0):
        """!
        @brief Interpret one content stream.

        Handles q/Q, cm, w, colour operators, path construction (m, l, c, v, y, h, re),
        path painting (PAINT operators and n), text state and positioning, text showing
        (Tj, TJ, ', "), Do and inline images (skipped). Color space operators cs and CS
        reset the colour to black. Other operators are ignored.
        An operator that raises an exception is skipped.
        @param data decoded content stream bytes
        @param res resource dictionary or a reference to it
        @param gs initial graphics state; modified in place until the first Q restore
        @param depth Form XObject nesting depth
        """
        res = self.doc.resolve(res)
        if not isinstance(res, dict):
            res = {}
        lx = Lexer(data)
        ops = []
        stack = []
        path = []
        cur = start = (0.0, 0.0)
        tm = [1.0, 0, 0, 1.0, 0, 0]
        tlm = list(tm)
        while True:
            t = lx.token()
            if t is None:
                break
            if not isinstance(t, Op) or t in ('[', '<<', 'true', 'false', 'null'):
                ops.append(lx.build(t))
                continue
            self.op_count += 1
            if self.op_count > MAX_PAGE_OPS:
                break  # page budget used up: the rest of the page is not drawn
            op = str(t)
            try:
                if op == 'q':
                    stack.append(gs.copy())
                elif op == 'Q':
                    if stack:
                        gs = stack.pop()
                elif op == 'cm':
                    gs.ctm = mmul([float(x) for x in ops[-6:]], gs.ctm)
                elif op == 'w':
                    gs.lw = float(ops[-1])
                elif op in ('g', 'rg', 'k'):
                    gs.fcs = None
                    gs.fill = _color(ops)
                elif op in ('G', 'RG', 'K'):
                    gs.scs = None
                    gs.stroke = _color(ops)
                elif op in ('sc', 'scn'):
                    gs.fill = _color(ops, gs.fcs)
                elif op in ('SC', 'SCN'):
                    gs.stroke = _color(ops, gs.scs)
                elif op in ('cs', 'CS'):
                    space = _colorspace(self.doc, ops[-1], res)
                    # initial colour: palette entry 0 for Indexed, mid gray for Pattern,
                    # black for all others (tint 1.0 for Separation and DeviceN)
                    if space and space[0] == 'indexed':
                        col = _index_rgb(space, 0)
                    elif space and space[0] == 'pattern':
                        col = (0.5, 0.5, 0.5)
                    else:
                        col = (0.0, 0.0, 0.0)
                    if op == 'cs':
                        gs.fcs, gs.fill = space, col
                    else:
                        gs.scs, gs.stroke = space, col
                elif op == 'gs':
                    self._ext_gstate(res, ops[-1], gs)
                elif op in ('m', 'l'):
                    p = apply(gs.ctm, float(ops[-2]), float(ops[-1]))
                    path.append((op.upper(),) + p)
                    cur = p
                    if op == 'm':
                        start = p
                elif op in ('c', 'v', 'y'):
                    n = [float(x) for x in ops]
                    pts = [apply(gs.ctm, n[i], n[i + 1]) for i in range(0, len(n) - 1, 2)]
                    if op == 'c':
                        p1, p2, p3 = pts[-3:]
                    elif op == 'v':
                        p1, (p2, p3) = cur, pts[-2:]
                    else:
                        p1, p3 = pts[-2:]
                        p2 = p3
                    path.append(('C',) + p1 + p2 + p3)
                    cur = p3
                elif op == 'h':
                    path.append(('Z',))
                    cur = start
                elif op == 're':
                    x, y, w, h = [float(v) for v in ops[-4:]]
                    p0 = apply(gs.ctm, x, y)
                    path += [
                        ('M',) + p0,
                        ('L',) + apply(gs.ctm, x + w, y),
                        ('L',) + apply(gs.ctm, x + w, y + h),
                        ('L',) + apply(gs.ctm, x, y + h),
                        ('Z',),
                    ]
                    cur = start = p0
                elif op in PAINT:
                    self._paint(path, gs, op)
                    path = []
                elif op == 'n':
                    path = []
                elif op == 'BT':
                    tm = [1.0, 0, 0, 1.0, 0, 0]
                    tlm = list(tm)
                elif op == 'Tf':
                    gs.font = self._font(res, ops[-2])
                    gs.fs = float(ops[-1])
                elif op == 'Tc':
                    gs.tc = float(ops[-1])
                elif op == 'Tw':
                    gs.tw = float(ops[-1])
                elif op == 'Tz':
                    gs.th = float(ops[-1]) / 100.0
                elif op == 'TL':
                    gs.tl = float(ops[-1])
                elif op == 'Ts':
                    gs.rise = float(ops[-1])
                elif op == 'Tr':
                    gs.tr = int(ops[-1])
                elif op in ('Td', 'TD'):
                    tx, ty = float(ops[-2]), float(ops[-1])
                    if op == 'TD':
                        gs.tl = -ty
                    tlm = mmul([1, 0, 0, 1, tx, ty], tlm)
                    tm = list(tlm)
                elif op == 'Tm':
                    tlm = [float(x) for x in ops[-6:]]
                    tm = list(tlm)
                elif op in ('T*', "'", '"'):
                    if op == '"':
                        gs.tw, gs.tc = float(ops[-3]), float(ops[-2])
                    tlm = mmul([1, 0, 0, 1, 0, -gs.tl], tlm)
                    tm = list(tlm)
                    if op != 'T*':
                        tm = self._show(gs, tm, ops[-1])
                elif op == 'Tj':
                    tm = self._show(gs, tm, ops[-1])
                elif op == 'TJ':
                    for el in ops[-1]:
                        if isinstance(el, bytes):
                            tm = self._show(gs, tm, el)
                        elif isinstance(el, (int, float)):
                            adv = -float(el) / 1000.0 * gs.fs * gs.th
                            tm = [
                                tm[0],
                                tm[1],
                                tm[2],
                                tm[3],
                                tm[4] + adv * tm[0],
                                tm[5] + adv * tm[1],
                            ]
                elif op == 'Do':
                    self._do(res, ops[-1], gs, depth)
                elif op == 'BI':
                    self._skip_inline_image(lx)
            except Exception:
                pass
            ops = []

    def _paint(self, path, gs, op):
        """!
        @brief Record the current path as a 'path' Shape.

        Does nothing while no_shapes is set, for paths with fewer than 2 segments, and
        for paths without coordinates. Closing operators append a Z segment.
        The stroke width is the line width times sqrt(|det CTM|); a stroked path with
        width <= 0 gets 0.5.
        @param path list of path segments
        @param gs current graphics state
        @param op painting operator, a key of PAINT
        """
        if self.no_shapes:
            return
        stroke, fill, eo, close = PAINT[op]
        if close:
            path = path + [('Z',)]
        xs, ys = [], []
        for s in path:
            xs += s[1::2]
            ys += s[2::2]
        if not xs or len(path) < 2:
            return
        fillc = gs.fill if fill else None
        strokec = gs.stroke if stroke else None
        m = gs.ctm
        lw = gs.lw * math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) if strokec else 0.0
        if strokec and lw <= 0:
            lw = 0.5
        pad = lw / 2
        sh = Shape()
        sh.kind = 'path'
        sh.bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        sh.seq = self.seq
        self.seq += 1
        sh.segs = list(path)
        sh.fill, sh.stroke, sh.lw, sh.evenodd = fillc, strokec, lw, bool(eo)
        sh.rect, sh.axis = _path_flags(path)
        sh.white = all(col is None or min(col) > 0.95 for col in (fillc, strokec))
        self.shapes.append(sh)

    def _do(self, res, name, gs, depth):
        """!
        @brief Execute the Do operator for an XObject.

        Form XObjects run with their Matrix applied to a copy of the state and their own
        resources (or the current ones). Forms nested deeper than 12 levels, or already
        being run, are skipped.
        Image XObjects become an 'image' Shape covering the unit square mapped by the
        CTM, unless no_shapes is set or the image is under 1 point in both width and
        height.
        @param res current resource dictionary
        @param name XObject resource name
        @param gs current graphics state
        @param depth current Form XObject nesting depth
        """
        r = self.doc.resolve
        xobjs = r(res.get('XObject'))
        xo = r(xobjs.get(name)) if isinstance(xobjs, dict) else None
        if not isinstance(xo, Stream):
            return
        st = r(xo.get('Subtype'))
        if st == 'Form':
            if depth > 12 or id(xo) in self._active or self.form_runs >= MAX_FORM_RUNS:
                return
            self.form_runs += 1
            mat = r(xo.get('Matrix'))
            mat = (
                [float(r(v)) for v in mat]
                if isinstance(mat, list) and len(mat) == 6
                else [1, 0, 0, 1, 0, 0]
            )
            g = gs.copy()
            g.ctm = mmul(mat, gs.ctm)
            self._active.add(id(xo))
            try:
                self.run(self.doc.decoded(xo), xo.get('Resources') or res, g, depth + 1)
            finally:
                self._active.discard(id(xo))
        elif st == 'Image' and not self.no_shapes:
            pts = [apply(gs.ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            sh = Shape()
            sh.kind = 'image'
            sh.bbox = (min(xs), min(ys), max(xs), max(ys))
            if sh.bbox[2] - sh.bbox[0] < 1 and sh.bbox[3] - sh.bbox[1] < 1:
                return
            sh.seq = self.seq
            self.seq += 1
            sh.stream, sh.resources, sh.matrix = xo, res, list(gs.ctm)
            sh.rect = sh.axis = sh.white = False
            sh.segs, sh.fill, sh.stroke, sh.lw, sh.evenodd = [], None, None, 0, False
            self.shapes.append(sh)

    def upright_count(self):
        """!
        @brief Count the collected characters that are upright.
        @return number of Char objects with upright set
        """
        return sum(1 for c in self.chars if c.upright)

    def rotated_ok(self):
        """!
        @brief Tell whether most text reads upright on the page as displayed.
        @return True when at least 50 % of the collected characters are upright
        """
        return self.upright_count() >= 0.5 * len(self.chars)

    def run_annotations(self, page, ctm):
        """!
        @brief Collect the text of visible annotation appearance streams.

        Covers form field values, free text and stamps. Their borders and backgrounds
        are skipped because no_shapes is set while they run.
        Skips Link, Popup, Text, FileAttachment and Sound annotations, and annotations
        with the Hidden (2) or NoView (32) flag.
        The normal appearance (AP N, selected by AS when it is a dictionary) is mapped
        from its BBox and Matrix onto the annotation Rect.
        @param page page dictionary
        @param ctm page transformation matrix applied after the Rect mapping
        """
        r = self.doc.resolve
        annots = r(page.get('Annots'))
        if not isinstance(annots, list):
            return
        self.no_shapes = True
        try:
            for a in annots:
                a = r(a)
                if not isinstance(a, dict) or r(a.get('Subtype')) in (
                    'Link',
                    'Popup',
                    'Text',
                    'FileAttachment',
                    'Sound',
                ):
                    continue
                flags = r(a.get('F'))
                if isinstance(flags, int) and flags & (2 | 32):  # Hidden, NoView
                    continue
                ap = r(a.get('AP'))
                xo = r(ap.get('N')) if isinstance(ap, dict) else None
                if isinstance(xo, dict):
                    state = r(a.get('AS'))
                    xo = r(xo.get(state)) if isinstance(state, str) else None
                rect = r(a.get('Rect'))
                if not isinstance(xo, Stream) or not isinstance(rect, list) or len(rect) != 4:
                    continue
                try:
                    rx = [float(r(v)) for v in rect]
                    bb = r(xo.get('BBox'))
                    bb = (
                        [float(r(v)) for v in bb]
                        if isinstance(bb, list) and len(bb) == 4
                        else [0, 0, 1, 1]
                    )
                    mat = r(xo.get('Matrix'))
                    mat = (
                        [float(r(v)) for v in mat]
                        if isinstance(mat, list) and len(mat) == 6
                        else [1, 0, 0, 1, 0, 0]
                    )
                except (TypeError, ValueError):
                    continue
                pts = [apply(mat, x, y) for x in (bb[0], bb[2]) for y in (bb[1], bb[3])]
                tx0, ty0 = min(p[0] for p in pts), min(p[1] for p in pts)
                tx1, ty1 = max(p[0] for p in pts), max(p[1] for p in pts)
                if tx1 - tx0 <= 0 or ty1 - ty0 <= 0:
                    continue
                sx = (max(rx[0], rx[2]) - min(rx[0], rx[2])) / (tx1 - tx0)
                sy = (max(rx[1], rx[3]) - min(rx[1], rx[3])) / (ty1 - ty0)
                fit = [sx, 0, 0, sy, min(rx[0], rx[2]) - sx * tx0, min(rx[1], rx[3]) - sy * ty0]
                g = GState()
                g.ctm = mmul(mmul(mat, fit), ctm)
                self.run(self.doc.decoded(xo), xo.get('Resources') or {}, g, 1)
        finally:
            self.no_shapes = False

    @staticmethod
    def _skip_inline_image(lx):
        """!
        @brief Move the lexer past an inline image (BI ... ID data EI).

        Reads tokens up to ID, then searches the raw data for EI preceded by whitespace
        and followed by whitespace or end of data. Without EI the lexer moves to the end.
        @param lx Lexer positioned after BI
        """
        while True:
            t = lx.token()
            if t is None:
                return
            if isinstance(t, Op) and t == 'ID':
                break
        m = _EI_RE.search(lx.d, lx.p + 1)
        lx.p = m.end() if m else lx.n

    def _show(self, gs, tm, s):
        """!
        @brief Show a string operand: add one Char per code and advance the text matrix.

        The advance per code is (width * font size + Tc + Tw) * horizontal scaling; Tw
        applies only to the single-byte code 32.
        Codes with empty text or a scaled size under 1 point produce no Char but still
        advance. A Type 3 fmat changes the glyph orientation, not the origin.
        @param gs current graphics state
        @param tm current text matrix
        @param s string operand bytes
        @return updated text matrix; tm unchanged when there is no font or s is not bytes
        """
        font = gs.font
        if font is None or not isinstance(s, bytes):
            return tm
        fs, th = gs.fs, gs.th
        fmat = font.fmat
        for code, nb in font.codes(s):
            m = mmul(tm, gs.ctm)
            ox, oy = apply(m, 0, gs.rise)
            if fmat:
                m = mmul(fmat, m)
            adv = (font.width(code) * fs + gs.tc + (gs.tw if nb == 1 and code == 32 else 0)) * th
            tm = [tm[0], tm[1], tm[2], tm[3], tm[4] + adv * tm[0], tm[5] + adv * tm[1]]
            ex, _ = apply(mmul(tm, gs.ctm), 0, gs.rise)
            un = math.hypot(m[0], m[1])
            vn = math.hypot(m[2], m[3])
            size = abs(fs) * vn
            text = font.text(code)
            if size < 1 or not text:
                continue
            c = Char()
            c.x, c.y, c.x2, c.size, c.text = ox, oy, ex, size, text
            c.bold, c.italic, c.mono, c.serif = font.bold, font.italic, font.mono, font.serif
            c.color = gs.fill
            c.u = (m[0] / un, m[1] / un) if un else (1.0, 0.0)
            c.v = (m[2] / vn, m[3] / vn) if vn else (0.0, 1.0)
            c.upright = c.u[0] > 0.995 and c.v[1] > 0.995
            c.invisible = gs.tr in (3, 7)
            c.est = font.estimated
            self.chars.append(c)
        return tm


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _union(a, b):
    """!
    @brief Return the bounding box that encloses two boxes.
    @param a box (x0, y0, x1, y1)
    @param b box (x0, y0, x1, y1)
    @return enclosing box as a list [x0, y0, x1, y1]
    """
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _overlap(a, b, gap):
    """!
    @brief Tell whether two boxes overlap or lie within a gap of each other.
    @param a box (x0, y0, x1, y1)
    @param b box (x0, y0, x1, y1)
    @param gap allowed distance between the boxes, in points
    @return True when the boxes grown by gap intersect
    """
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def cluster(items, gap):
    """!
    @brief Group boxes into regions whose members lie within a gap of each other.

    Regions whose bounding boxes come within gap of each other merge, repeated until
    no two regions do. Candidate pairs come from a grid of cells, so the work grows
    about linearly with the number of boxes. Regions are returned in the order of
    their last member in items.
    @param items iterable of (bbox, object) pairs
    @param gap merge distance in points
    @return list of [bbox list, member objects list] pairs
    """
    groups = [[list(bb), [obj], k] for k, (bb, obj) in enumerate(items)]
    while True:
        merged = _merge_pass(groups, gap)
        if len(merged) == len(groups):
            merged.sort(key=lambda g: g[2])
            return [g[:2] for g in merged]
        groups = merged


def _merge_pass(groups, gap):
    """!
    @brief Merge every pair of groups whose boxes overlap within gap (one pass).

    Pairs are found through a grid with cells of max(64, 4 * gap) points; connected
    groups are joined with a union-find structure. A merged box can reach further
    groups, so cluster() repeats the pass until the count stays the same.
    @param groups list of [bbox list, member list, index of the last member in items]
    @param gap merge distance in points
    @return list of merged groups in the same form
    """
    size = max(64.0, 4.0 * gap)
    parent = list(range(len(groups)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid = {}
    for i, (bb, _, _) in enumerate(groups):
        cx0, cx1 = int((bb[0] - gap) // size), int((bb[2] + gap) // size)
        cy0, cy1 = int((bb[1] - gap) // size), int((bb[3] + gap) // size)
        seen = set()
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                cell = grid.setdefault((cx, cy), [])
                for j in cell:
                    if j not in seen:
                        seen.add(j)
                        if _overlap(groups[j][0], bb, gap):
                            a, b = find(i), find(j)
                            if a != b:
                                parent[max(a, b)] = min(a, b)
                cell.append(i)
    out = {}
    for i, (bb, members, last) in enumerate(groups):
        root = find(i)
        if root in out:
            out[root][0] = _union(out[root][0], bb)
            out[root][1] += members
            out[root][2] = max(out[root][2], last)
        else:
            out[root] = [list(bb), list(members), last]
    return [out[k] for k in sorted(out)]


class Region:
    """!
    @brief A figure area on a page, built from clustered shapes.
    @par Attributes
    - bbox: [x0, y0, x1, y1] in page space points
    - shapes: Shape objects of the figure
    - chars: Char objects that lie inside the figure
    - raster: single image Shape exported as a raster file, or None
    """

    def __init__(self, bbox, shapes):
        """!
        @brief Create a region with no characters and no raster.
        @param bbox bounding box (x0, y0, x1, y1)
        @param shapes Shape objects in the region
        """
        ## [x0, y0, x1, y1] in page space points
        self.bbox = list(bbox)
        ## Shape objects of the figure
        self.shapes = shapes
        ## Char objects that lie inside the figure
        self.chars = []
        ## single image Shape exported as a raster file, or None
        self.raster = None

    def signature(self):
        """!
        @brief Return a key that identifies a figure repeated at the same place.
        @return tuple of the bbox coordinates rounded to whole points and the shape count
        """
        b = self.bbox
        return (round(b[0]), round(b[1]), round(b[2]), round(b[3]), len(self.shapes))


class Line:
    """!
    @brief One text line built from a run of characters.
    @par Attributes
    - x0: left edge of the first character
    - x1: right edge (largest x2) of the run
    - ix0: left edge of the first non-blank character
    - y: most common baseline, rounded to 0.1 points
    - top: y + 0.8 * size
    - size: most common character size, rounded to 0.1 points
    - segs: tuples (text, bold, italic) of equally styled text
    - text: concatenated text of segs
    - bold: True when all non-blank characters are bold
    - mono: True when all non-blank characters are monospace
    - ref: first non-blank Char, used for style lookups
    - chars: Char objects of the line
    - col: column key from reading order; () when not set
    - right: right edge of the text column holding the line; x1 by default
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = (
        'x0',
        'x1',
        'ix0',
        'y',
        'top',
        'size',
        'segs',
        'text',
        'bold',
        'mono',
        'ref',
        'chars',
        'col',
        'right',
    )


def _bidi(ch):
    """!
    @brief Classify text by the bidirectional class of its first character.

    Classes R and AL give 'R'. Classes L, EN and AN give 'L'. Everything else,
    including empty text, gives 'N'.
    @param ch text string
    @return 'R', 'L' or 'N'
    """
    d = unicodedata.bidirectional(ch[0]) if ch else ''
    return 'R' if d in ('R', 'AL') else ('L' if d in ('L', 'EN', 'AN') else 'N')


def _logical_order(items):
    """!
    @brief Reorder visual (left to right) items into reading order.

    Lines without right-to-left items are returned unchanged.
    A line with more right-to-left than left-to-right items is reversed, keeping runs
    of left-to-right text and digits in their order. In other lines only the
    right-to-left runs are reversed.
    A neutral item joins a run only when the item after it continues the run.
    @param items list of (text, bold, italic) tuples in visual order
    @return list of the same tuples in logical order
    """
    dirs = [_bidi(t) for t, _, _ in items]
    if 'R' not in dirs:
        return items
    rtl = dirs.count('R') > dirs.count('L')
    if rtl:
        items, dirs = items[::-1], dirs[::-1]
    keep = 'L' if rtl else 'R'  # runs of the minority direction are reversed back
    out = []
    i = 0
    while i < len(items):
        if dirs[i] != keep:
            out.append(items[i])
            i += 1
            continue
        j = i
        while j + 1 < len(items) and (
            dirs[j + 1] == keep
            or (dirs[j + 1] == 'N' and j + 2 < len(items) and dirs[j + 2] == keep)
        ):
            j += 1
        out += items[i : j + 1][::-1]
        i = j + 1
    return out


def make_line(run):
    """!
    @brief Build a Line from characters of one row, sorted by x.

    A character with the same text as the previous one, offset by less than
    min(0.15 * size, half the previous advance) in x and less than 0.15 * size in y,
    is an overprinted duplicate (fake bold) and is dropped.
    A gap wider than 0.18 * the larger size (em) inserts a space, unless a space is
    already there; the space keeps the style only when both neighbours share it.
    Text is put in logical order, merged into styled segments, and whitespace is
    collapsed and trimmed.
    @param run list of Char objects
    @return Line, or None when the run has no text after trimming
    """
    items = []
    prev = None
    last = ''
    for c in run:
        if prev is not None:
            dup = min(0.15 * c.size, 0.5 * abs(prev.x2 - prev.x))
            if (
                c.text == prev.text
                and abs(c.x - prev.x) < dup
                and abs(c.y - prev.y) < 0.15 * c.size
            ):
                continue  # overprinted duplicate (fake bold)
            gap = c.x - prev.x2
            if (
                gap > 0.18 * max(c.size, prev.size)
                and not last.endswith(' ')
                and not c.text.startswith(' ')
            ):
                same = prev.bold == c.bold and prev.italic == c.italic
                items.append((' ', prev.bold if same else False, prev.italic if same else False))
        items.append((c.text, c.bold, c.italic))
        last = c.text
        prev = c
    segs = []
    for text, bold, italic in _logical_order(items):
        if segs and segs[-1][1] == bold and segs[-1][2] == italic:
            segs[-1][0] += text
        else:
            segs.append([text, bold, italic])
    # collapse whitespace across segments
    out = []
    space = True
    for text, b, i in segs:
        t = re.sub(r'\s+', ' ', text)
        if space and t.startswith(' '):
            t = t[1:]
        if t:
            space = t.endswith(' ')
            out.append([t, b, i])
    while out and out[-1][0].endswith(' '):
        out[-1][0] = out[-1][0].rstrip()
        if not out[-1][0]:
            out.pop()
    if not out:
        return None
    ink = [c for c in run if c.text.strip()] or run
    ln = Line()
    ln.segs = [tuple(s) for s in out]
    ln.text = ''.join(s[0] for s in out)
    ln.size = Counter(round(c.size, 1) for c in ink).most_common(1)[0][0]
    ln.y = Counter(round(c.y, 1) for c in ink).most_common(1)[0][0]
    ln.x0 = min(c.x for c in run)
    ln.ix0 = min(c.x for c in ink)
    ln.x1 = max(c.x2 for c in run)
    ln.top = ln.y + 0.8 * ln.size
    ln.bold = all(c.bold for c in ink)
    ln.mono = all(c.mono for c in ink)
    ln.ref = ink[0]
    ln.chars = run
    ln.col = ()
    ln.right = ln.x1
    return ln


def _rows(chars):
    """!
    @brief Group upright characters into baseline rows.

    Characters are sorted top to bottom, then by x. Characters with empty text are
    dropped. A character joins the current row when its baseline differs from the
    baseline of the row's first character by at most 0.45 * the larger of the two
    sizes.
    @param chars iterable of Char objects
    @return list of rows, top to bottom, each a list of Char sorted by x
    """
    chars = sorted((c for c in chars if c.upright and c.text), key=lambda c: (-c.y, c.x))
    rows = []
    for c in chars:
        if rows and abs(rows[-1][0] - c.y) <= 0.45 * max(rows[-1][1], c.size):
            rows[-1][2].append(c)
        else:
            rows.append([c.y, c.size, [c]])
    out = []
    for _, _, cs in rows:
        cs.sort(key=lambda c: c.x)
        out.append(cs)
    return out


def _split_row(cs, split):
    """!
    @brief Split a row of characters at wide horizontal gaps.

    A new run starts where the gap from the previous character's x2 exceeds
    split * the larger size (em) of the two characters.
    @param cs non-empty list of Char sorted by x
    @param split gap threshold in em, or None to keep the row as one run
    @return list of runs, each a list of Char
    """
    runs = [[cs[0]]]
    for c in cs[1:]:
        p = runs[-1][-1]
        if split is not None and c.x - p.x2 > split * max(c.size, p.size):
            runs.append([c])
        else:
            runs[-1].append(c)
    return runs


def build_lines(chars, split=None):
    """!
    @brief Build text lines from characters, row by row.
    @param chars iterable of Char objects; only upright characters are used
    @param split gap threshold in em passed to _split_row, or None for no split
    @return list of Line objects, top to bottom
    """
    lines = []
    for cs in _rows(chars):
        for run in _split_row(cs, split):
            ln = make_line(run)
            if ln:
                lines.append(ln)
    return lines


class _Frag:
    """!
    @brief Part of a text row between gaps wider than 0.9 em, or a figure region.

    Unit of column detection.
    @par Attributes
    - x0: left edge in page space points
    - y0: bottom edge in page space points
    - x1: right edge in page space points
    - y1: top edge in page space points
    - row: index of the text row, or None for a region
    - chars: Char objects of the fragment, or None for a region
    - obj: Region for a figure, None for text
    """

    ## Attribute names; each is described in the class documentation.
    __slots__ = ('x0', 'y0', 'x1', 'y1', 'row', 'chars', 'obj')

    def __init__(self, x0, y0, x1, y1, row, chars, obj=None):
        """!
        @brief Store the box and contents of a fragment.
        @param x0 left edge
        @param y0 bottom edge
        @param x1 right edge
        @param y1 top edge
        @param row text row index, or None
        @param chars Char objects, or None
        @param obj Region, or None for text
        """
        ## left edge in page space points
        self.x0 = x0
        ## bottom edge in page space points
        self.y0 = y0
        ## right edge in page space points
        self.x1 = x1
        ## top edge in page space points
        self.y1 = y1
        ## index of the text row, or None for a region
        self.row = row
        ## Char objects of the fragment, or None for a region
        self.chars = chars
        ## Region for a figure, None for text
        self.obj = obj


def _side_ok(frags, edge, em, left):
    """!
    @brief Test whether the fragments on one side of a gutter form a text column.

    Only the half of the side next to the gutter is considered; columns further away
    are tested in later passes. Per row the fragment nearest the gutter is taken.
    Requirements: at least 3 rows; median fragment width at least 6 em; at least
    50 % of the rows reaching the gutter edge within max(2 em, 0.2 * median width).
    The right edge counts for the left side, the left edge for the right side.
    @param frags text fragments on this side
    @param edge x coordinate of the gutter edge on this side
    @param em typical character size in points
    @param left True for the left side, False for the right side
    @return True when the side qualifies as a column
    """
    if not frags:
        return False
    # only the column next to the gutter counts; further columns are tested later
    if left:
        far = max(f.x1 for f in frags)
        lim = far - 0.5 * (far - min(f.x0 for f in frags))
        frags = [f for f in frags if f.x1 > lim]
    else:
        near = min(f.x0 for f in frags)
        lim = near + 0.5 * (max(f.x1 for f in frags) - near)
        frags = [f for f in frags if f.x0 < lim]
    rows = {}
    for f in frags:
        o = rows.get(f.row)
        if o is None or (f.x1 > o.x1 if left else f.x0 < o.x0):
            rows[f.row] = f
    if len(rows) < 3:
        return False
    near = sorted(rows.values(), key=lambda f: f.x1 - f.x0)
    med = near[len(near) // 2].x1 - near[len(near) // 2].x0
    if med < 6 * em:
        return False
    tol = max(2 * em, 0.2 * med)
    if left:
        hits = sum(1 for f in near if f.x1 >= edge - tol)
    else:
        hits = sum(1 for f in near if f.x0 <= edge + tol)
    return hits >= 0.5 * len(near)


def _gutter(items, em):
    """!
    @brief Find a vertical gutter between text columns.

    Needs at least 6 text fragments and a total width of at least 12 em.
    A coverage count per 1-point slice is built from all items. Candidate runs lie
    between 15 % and 85 % of the width, are at least 0.9 em wide, and are covered by
    at most 25 % of the text fragments. Runs are tried by lowest coverage, then by
    width. Each run is narrowed to its longest stretch at minimum coverage, which
    must be at least 0.5 em. Items crossing it (titles, full-width figures, footers)
    are allowed when they are at most 25 % of the fragments. Both sides must pass
    _side_ok.
    @param items _Frag objects, text and regions
    @param em typical character size in points
    @return (gx0, gx1) gutter edges in points, or None
    """
    frags = [f for f in items if f.obj is None]
    if len(frags) < 6:
        return None
    bx0 = min(f.x0 for f in items)
    bx1 = max(f.x1 for f in items)
    width = bx1 - bx0
    if not math.isfinite(width) or width < 12 * em or width > 100000:
        return None  # 100000 points: about 35 m, no real page
    nb = int(width) + 2
    cov = [0] * (nb + 1)
    for f in items:
        a = max(0, int(f.x0 - bx0))
        b = min(nb, int(math.ceil(f.x1 - bx0)))
        cov[a] += 1
        cov[b] -= 1
    acc = 0
    for i in range(nb + 1):
        acc += cov[i]
        cov[i] = acc
    limit = 0.25 * len(frags)
    lo, hi = int(0.15 * width), int(0.85 * width)
    runs = []
    i = lo
    while i < hi:
        if cov[i] <= limit:
            j = i
            while j + 1 < hi and cov[j + 1] <= limit:
                j += 1
            if j + 1 - i >= 0.9 * em:
                runs.append((min(cov[i : j + 1]), -(j - i), i, j))
            i = j + 1
        else:
            i += 1
    for _, _, i, j in sorted(runs):
        # narrow the run to its least covered part: the gutter between the columns
        floor = min(cov[i : j + 1])
        best = (0, 0)
        x = i
        while x <= j:  # longest stretch at the floor: crossed only by spanning items
            if cov[x] == floor:
                y = x
                while y + 1 <= j and cov[y + 1] == floor:
                    y += 1
                if y - x > best[1] - best[0]:
                    best = (x, y)
                x = y + 1
            else:
                x += 1
        if best[1] + 1 - best[0] < 0.5 * em:
            continue
        gx0, gx1 = bx0 + best[0] + 1, bx0 + best[1]
        cross = [f for f in frags if f.x0 < gx1 - 0.5 and f.x1 > gx0 + 0.5]
        left = [f for f in frags if f.x1 <= gx0 + 0.5]
        right = [f for f in frags if f.x0 >= gx1 - 0.5]
        if len(cross) > 0.25 * len(frags):
            continue
        if _side_ok(left, gx0, em, True) and _side_ok(right, gx1, em, False):
            return gx0, gx1
    return None


def _columns(items, em, key=(), depth=0):
    """!
    @brief Order items for reading by recursive column splitting.

    Columns are read left to right. Items that cross the gutter are kept in their
    vertical position and separate the columns into sections. Each column is split
    again, up to 6 levels. Without a gutter, items are sorted top to bottom, then
    left to right. Consecutive spanning items share one key.
    @param items _Frag objects
    @param em typical character size in points
    @param key column key prefix of this level
    @param depth recursion depth
    @return list of (column key, item) pairs
    """
    g = _gutter(items, em) if depth < 6 else None
    if g is None:
        return [(key, f) for f in sorted(items, key=lambda f: (-f.y1, f.x0))]
    gx0, gx1 = g
    mid = (gx0 + gx1) / 2
    span = sorted((f for f in items if f.x0 < gx1 - 0.5 and f.x1 > gx0 + 0.5), key=lambda f: -f.y1)
    rest = [f for f in items if not (f.x0 < gx1 - 0.5 and f.x1 > gx0 + 0.5)]
    segs = [([], []) for _ in range(len(span) + 1)]
    for f in rest:
        ym = (f.y0 + f.y1) / 2
        k = sum(1 for sp in span if (sp.y0 + sp.y1) / 2 > ym)
        segs[k][0 if (f.x0 + f.x1) / 2 < mid else 1].append(f)
    out = []
    skey = None
    for k, (left, right) in enumerate(segs):
        if left or right:
            skey = None
        if left:
            out += _columns(left, em, key + (k, 0), depth + 1)
        if right:
            out += _columns(right, em, key + (k, 1), depth + 1)
        if k < len(span):
            skey = skey or key + (k, 2)  # consecutive spanning items share one key
            out.append((skey, span[k]))
    return out


def reading_order(chars, regions):
    """!
    @brief Return the page text lines and figure regions in reading order.

    Rows are split into fragments at gaps over 0.9 em. A fragment box spans
    0.25 * size below to 0.8 * size above the baseline. The em is the median
    character size, 10.0 when there are no characters. Fragments of one row inside
    the same column are joined into one Line. Each Line gets its column key and
    the right edge of its column's text.
    @param chars Char objects of the page
    @param regions Region objects of the page
    @return list of Line and Region objects in reading order
    """
    chars = [c for c in chars if math.isfinite(c.x) and math.isfinite(c.x2) and math.isfinite(c.y)]
    rows = _rows(chars)
    items = []
    sizes = []
    for ri, cs in enumerate(rows):
        for run in _split_row(cs, 0.9):
            ink = [c for c in run if c.text.strip()] or run
            size = max(c.size for c in ink)
            y = ink[0].y
            items.append(
                _Frag(
                    min(c.x for c in run),
                    y - 0.25 * size,
                    max(c.x2 for c in run),
                    y + 0.8 * size,
                    ri,
                    run,
                )
            )
            sizes += [c.size for c in ink]
    for r in regions:
        items.append(_Frag(r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3], None, None, r))
    if not items:
        return []
    sizes.sort()
    em = sizes[len(sizes) // 2] if sizes else 10.0
    order = _columns(items, em)
    right = {}
    for key, f in order:
        if f.obj is None:
            right[key] = max(right.get(key, f.x1), f.x1)
    out = []
    i = 0
    while i < len(order):
        key = order[i][0]
        j = i
        while j < len(order) and order[j][0] == key:
            j += 1
        group = [f for _, f in order[i:j]]
        merged = {}
        seq = []
        for f in group:  # rejoin fragments of one row inside a column
            if f.obj is not None:
                seq.append((f.y1, f.obj))
            elif f.row in merged:
                merged[f.row] += f.chars
            else:
                merged[f.row] = list(f.chars)
                seq.append((f.y1, f.row))
        seq.sort(key=lambda t: -t[0])
        for _, x in seq:
            if isinstance(x, Region):
                out.append(x)
                continue
            ln = make_line(sorted(merged[x], key=lambda c: c.x))
            if ln:
                ln.col = key
                ln.right = right.get(key, ln.x1)
                out.append(ln)
        i = j
    return out


class PageLayout:
    """!
    @brief Layout of one page: text lines, figure regions and their reading order.

    @par Attributes
    - number: 1-based page number.
    - box: displayed page box (x0, y0, x1, y1) in PDF user-space points.
    - lines: text lines (Line objects) kept for output.
    - regions: figure regions (Region objects) kept for output.
    - order: lines and regions in reading order.
    """

    def __init__(self, number, box, lines, regions, order=None):
        """!
        @brief Store the page layout data.

        @param number 1-based page number.
        @param box Displayed page box (x0, y0, x1, y1) in points.
        @param lines List of Line objects.
        @param regions List of Region objects.
        @param order Lines and regions in reading order; None uses lines followed by regions.
        """
        ## 1-based page number.
        self.number = number
        ## displayed page box (x0, y0, x1, y1) in PDF user-space points.
        self.box = box
        ## text lines (Line objects) kept for output.
        self.lines = lines
        ## figure regions (Region objects) kept for output.
        self.regions = regions
        ## lines and regions in reading order.
        self.order = order if order is not None else lines + regions


def _grow(region, chars):
    """!
    @brief Enlarge a region's bounding box to cover the glyph boxes of the given characters.

    An upright glyph box spans x to x2 and from 0.25 em below to 0.8 em above the
    baseline. A rotated glyph box spans 1 em along its baseline direction and the same
    -0.25 to 0.8 em along its up direction; the region grows by its axis-aligned bounds.

    @param region Region whose bbox is updated in place.
    @param chars Iterable of characters.
    """
    for c in chars:
        if c.upright:
            region.bbox = _union(region.bbox, [c.x, c.y - 0.25 * c.size, c.x2, c.y + 0.8 * c.size])
            continue
        (ux, uy), (vx, vy) = c.u, c.v
        pts = [
            (c.x + c.size * (a * ux + b * vx), c.y + c.size * (a * uy + b * vy))
            for a in (0, 1)
            for b in (-0.25, 0.8)
        ]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        region.bbox = _union(region.bbox, [min(xs), min(ys), max(xs), max(ys)])


def _absorb_labels(regions, flow):
    """!
    @brief Move axis labels, tick labels and rotated text next to a figure into it.

    A text line qualifies when it has at most 30 characters, is not bold, does not end
    with one of . ! ? : ;, is not larger than 1.1 x the median text size of the page
    (10.0 when the page has no text), and its box comes within 1.2 x its font size of
    the figure's bounding box.
    A rotated character qualifies when its origin lies within 1.5 x its font size of
    the figure's bounding box.
    Each absorbed item grows the figure's box. The pass repeats up to 3 times and stops
    early when nothing is absorbed.

    @param regions List of Region objects; their chars and bbox are updated in place.
    @param flow Characters outside all figures.
    @return The characters of flow that no figure absorbed.
    """
    if not regions:
        return flow
    sizes = sorted(c.size for c in flow if c.text.strip())
    body = sizes[len(sizes) // 2] if sizes else 10.0
    for _ in range(3):
        taken = set()
        for ln in build_lines(flow, split=0.9):
            if (
                len(ln.text) > 30
                or ln.bold
                or ln.size > 1.1 * body
                or re.search(r'[.!?:;]$', ln.text)
            ):
                continue
            m = 1.2 * ln.size
            for r in regions:
                if _overlap(r.bbox, (ln.x0, ln.y - 0.25 * ln.size, ln.x1, ln.top), m):
                    r.chars += ln.chars
                    _grow(r, ln.chars)
                    taken.update(id(c) for c in ln.chars)
                    break
        for c in flow:
            if c.upright or id(c) in taken:
                continue
            m = 1.5 * c.size
            for r in regions:
                b = r.bbox
                if b[0] - m <= c.x <= b[2] + m and b[1] - m <= c.y <= b[3] + m:
                    r.chars.append(c)
                    _grow(r, [c])
                    taken.add(id(c))
                    break
        if not taken:
            break
        flow = [c for c in flow if id(c) not in taken]
    return flow


def layout_page(number, pc, box, figures=True):
    """!
    @brief Split a page's interpreted content into figure regions and text lines.

    Drawing rules, in PDF user-space points:
    - White paths are kept aside and added only to figures they overlap.
    - Images covering more than 85 % of the page width and height are backgrounds.
    - Paths covering more than 90 % of the page width and height are ignored.
    - Remaining shapes are clipped to the page box and clustered with a 6.0 pt gap.
    - A cluster smaller than 15 x 15 pt is dropped (bullets, icons).
    - A cluster without images is dropped when it is under 3 pt wide or high, when it
      is a single rectangle, when it is at most 3 unstroked rectangles, or when it is at
      most 8 axis-aligned paths that each span at least 90 % of the cluster in both
      directions (framed text box or form field).
    A character joins a figure when its reference point lies within 3 pt of the figure
    box; nearby labels are then absorbed (see _absorb_labels). Overlapping figures are
    merged. A figure made of one image and no visible text is exported as a raster.
    When figures are enabled and fewer than 20 upright visible characters remain as text,
    each background image becomes a raster figure.

    @param number 1-based page number.
    @param pc PageContent with the page's characters and shapes.
    @param box Displayed page box (x0, y0, x1, y1) in points.
    @param figures False to skip figure detection; all characters then become text.
    @return PageLayout for the page.
    """
    bx0, by0, bx1, by1 = box
    pw, ph = bx1 - bx0, by1 - by0
    # text far outside the page (beyond 10 % of its size) or at non-finite
    # positions is not visible and is dropped
    lox, hix = bx0 - 0.1 * pw, bx1 + 0.1 * pw
    loy, hiy = by0 - 0.1 * ph, by1 + 0.1 * ph
    # NaN fails every comparison and infinity lies outside the range
    pc.chars = [
        c
        for c in pc.chars
        if lox <= c.x <= hix and lox <= c.x2 <= hix and loy <= c.y <= hiy and c.size < 1e5
    ]
    cands, backgrounds, whites = [], [], []
    for s in pc.shapes:
        b = s.bbox
        if not (b[0] <= b[2] and b[1] <= b[3]):
            continue  # NaN coordinates; infinite ones are clipped to the page below
        if s.kind == 'path' and s.white:
            whites.append(s)  # invisible on paper; drawn in a figure only when it overlaps one
            continue
        x0, y0 = max(s.bbox[0], bx0), max(s.bbox[1], by0)
        x1, y1 = min(s.bbox[2], bx1), min(s.bbox[3], by1)
        if x0 > x1 or y0 > y1:
            continue
        w, h = x1 - x0, y1 - y0
        if s.kind == 'image' and w > 0.85 * pw and h > 0.85 * ph:
            backgrounds.append(s)
            continue
        if s.kind == 'path' and w > 0.9 * pw and h > 0.9 * ph:
            continue  # page background or frame
        cands.append(((x0, y0, x1, y1), s))

    regions = []
    for bb, members in cluster(cands, 6.0) if figures else []:
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if w < 15 and h < 15:
            continue  # bullets, icons
        paths = [s for s in members if s.kind == 'path']
        images = [s for s in members if s.kind == 'image']
        if not images:
            if w < 3 or h < 3:
                continue  # horizontal or vertical rules
            if all(s.rect for s in paths):
                if len(paths) == 1:
                    continue  # box around text
                if len(paths) <= 3 and all(s.stroke is None for s in paths):
                    continue  # shading behind text
            if len(paths) <= 8 and all(
                s.axis and s.bbox[2] - s.bbox[0] >= 0.9 * w and s.bbox[3] - s.bbox[1] >= 0.9 * h
                for s in paths
            ):
                continue  # nested frames and fills: a framed text box or form field
        regions.append(Region(bb, sorted(members, key=lambda s: s.seq)))

    flow = []
    for c in pc.chars:
        cx = (c.x + c.x2) / 2 if c.upright else c.x
        cy = c.y + 0.3 * c.size if c.upright else c.y
        for r in regions:
            b = r.bbox
            if b[0] - 3 <= cx <= b[2] + 3 and b[1] - 3 <= cy <= b[3] + 3:
                r.chars.append(c)
                break
        else:
            flow.append(c)

    for r in regions:
        _grow(r, r.chars)
    flow = _absorb_labels(regions, flow)
    while True:  # merge overlapping regions until none overlap
        merged = []
        for r in sorted(regions, key=lambda r: -r.bbox[3]):
            for o in merged:
                if _overlap(o.bbox, r.bbox, 0):
                    o.bbox = _union(o.bbox, r.bbox)
                    o.shapes = sorted(o.shapes + r.shapes, key=lambda s: s.seq)
                    o.chars += r.chars
                    break
            else:
                merged.append(r)
        if len(merged) == len(regions):
            break
        regions = merged
    regions = merged
    for r in regions:
        extra = [s for s in whites if _overlap(r.bbox, s.bbox, 0)]
        if extra:
            r.shapes = sorted(r.shapes + extra, key=lambda s: s.seq)
    for r in regions:
        if (
            len(r.shapes) == 1
            and r.shapes[0].kind == 'image'
            and not [c for c in r.chars if not c.invisible]
        ):
            r.raster = r.shapes[0]

    ink = sum(1 for c in flow if c.text.strip() and c.upright)
    if ink < 20 and figures:
        for s in backgrounds:
            r = Region(s.bbox, [s])
            r.raster = s
            regions.append(r)

    order = reading_order(flow, regions)
    return PageLayout(number, box, [o for o in order if isinstance(o, Line)], regions, order)


# ---------------------------------------------------------------------------
# Images and SVG
# ---------------------------------------------------------------------------


def _png(w, h, depth, ctype, data, palette=None):
    """!
    @brief Encode raw image samples as a PNG file.

    Each row is prefixed with filter type 0. The sample data is truncated or padded with
    zero bytes to the exact size. IDAT data uses zlib level 6 and is compressed row by
    row, so no uncompressed copy of the whole image is built. No interlacing.

    @param w Image width in pixels.
    @param h Image height in pixels.
    @param depth Bits per sample.
    @param ctype PNG color type: 0 (gray), 2 (RGB) or 3 (palette).
    @param data Packed sample bytes, rows padded to whole bytes.
    @param palette RGB palette bytes for a PLTE chunk, or None.
    @return PNG file contents as bytes.
    """
    rowlen = (w * {0: 1, 2: 3, 3: 1}[ctype] * depth + 7) // 8
    need = rowlen * h
    if len(data) != need:
        data = bytes(data[:need]).ljust(need, b'\0')
    view = memoryview(data)
    comp = zlib.compressobj(6)
    parts = []
    for i in range(h):
        parts.append(comp.compress(b'\0'))
        parts.append(comp.compress(view[i * rowlen : (i + 1) * rowlen]))
    parts.append(comp.flush())
    idat = b''.join(parts)

    def chunk(tag, body):
        """!
        @brief Build one PNG chunk: big-endian length, tag, body and CRC-32 of tag plus body.

        @param tag 4-byte chunk type.
        @param body Chunk data bytes.
        @return Chunk bytes.
        """
        return (
            struct.pack('>I', len(body))
            + tag
            + body
            + struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    out = b'\x89PNG\r\n\x1a\n' + chunk(
        b'IHDR', struct.pack('>IIBBBBB', w, h, depth, ctype, 0, 0, 0)
    )
    if palette is not None:
        out += chunk(b'PLTE', palette)
    return out + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def _colorspace(doc, cs, res):
    """!
    @brief Map a PDF color space to a simplified description for image export.

    Named spaces are looked up in the ColorSpace resource dictionary. ICCBased uses its
    N entry (3 when the stream is missing). CalRGB and Lab map to rgb, CalGray to gray,
    Separation to sep.

    @param doc Document used to resolve references and decode streams.
    @param cs Color space object (name or array).
    @param res Resource dictionary for named color spaces, or None.
    @return Tuple (kind, components): ('gray', 1), ('rgb', 3), ('cmyk', 4) or ('sep', 1);
            or ('indexed', 1, base, hival, lookup bytes) for Indexed; None when unsupported.
    """
    r = doc.resolve
    cs = r(cs)
    if isinstance(cs, str):
        if cs in ('DeviceGray', 'G', 'CalGray'):
            return ('gray', 1)
        if cs in ('DeviceRGB', 'RGB', 'CalRGB'):
            return ('rgb', 3)
        if cs in ('DeviceCMYK', 'CMYK'):
            return ('cmyk', 4)
        if cs == 'Pattern':
            return ('pattern', 1)
        named = r((res or {}).get('ColorSpace')) if isinstance(res, dict) else None
        if isinstance(named, dict) and cs in named:
            return _colorspace(doc, named[cs], None)
        return None
    if isinstance(cs, list) and cs:
        kind = r(cs[0])
        if kind == 'ICCBased' and len(cs) > 1:
            st = r(cs[1])
            n = r(st.get('N')) if isinstance(st, Stream) else 3
            return {1: ('gray', 1), 3: ('rgb', 3), 4: ('cmyk', 4)}.get(n)
        if kind == 'CalRGB':
            return ('rgb', 3)
        if kind == 'Lab':
            d = r(cs[1]) if len(cs) > 1 else None
            d = d if isinstance(d, dict) else {}
            wp = r(d.get('WhitePoint'))
            rng = r(d.get('Range'))
            try:
                wp = (
                    [float(r(v)) for v in wp][:3] if isinstance(wp, list) and len(wp) >= 3 else None
                )
                rng = (
                    [float(r(v)) for v in rng][:4]
                    if isinstance(rng, list) and len(rng) >= 4
                    else None
                )
            except (TypeError, ValueError):
                wp = rng = None
            return ('lab', 3, wp or [0.9505, 1.0, 1.089], rng or [-100.0, 100.0, -100.0, 100.0])
        if kind == 'Pattern':
            return ('pattern', 1)
        if kind == 'DeviceN' and len(cs) > 1 and isinstance(r(cs[1]), list):
            return ('devn', max(1, len(r(cs[1]))))
        if kind == 'CalGray':
            return ('gray', 1)
        if kind in ('Indexed', 'I') and len(cs) >= 4:
            base = _colorspace(doc, cs[1], res)
            lookup = r(cs[3])
            if isinstance(lookup, Stream):
                lookup = doc.decoded(lookup)
            hival = r(cs[2])
            if base is None or not isinstance(lookup, bytes) or type(hival) is not int or hival < 0:
                return None
            return ('indexed', 1, base, hival, lookup)
        if kind == 'Separation':
            return ('sep', 1)
    return None


## @brief bytes.translate table that inverts 8-bit samples (v -> 255 - v).
_INVERT = bytes(range(255, -1, -1))
## @brief bytes.translate table that leaves 8-bit samples unchanged.
_IDENTITY = bytes(range(256))
## @brief Images with more pixels than this (2^26, about 67 million) are not exported.
MAX_IMAGE_PIXELS = 1 << 26


def _cmyk_to_rgb(data):
    """!
    @brief Convert 8-bit CMYK samples to 8-bit RGB.

    Each channel is (255 - C) * (255 - K) // 255, likewise for M and Y.

    @param data CMYK bytes, 4 per pixel.
    @return RGB bytes, 3 per pixel.
    """
    c, m, y, k = data[0::4], data[1::4], data[2::4], data[3::4]
    out = bytearray(len(k) * 3)
    out[0::3] = bytes((255 - a) * (255 - kk) // 255 for a, kk in zip(c, k))
    out[1::3] = bytes((255 - a) * (255 - kk) // 255 for a, kk in zip(m, k))
    out[2::3] = bytes((255 - a) * (255 - kk) // 255 for a, kk in zip(y, k))
    return bytes(out)


def _decode_pairs(r, decode, defaults):
    """!
    @brief Read the (Dmin, Dmax) pairs of an image /Decode array.

    @param r Function that resolves references.
    @param decode /Decode value, or None.
    @param defaults Default pairs, one per color component.
    @return List of (Dmin, Dmax) float pairs, one per component; the defaults when
            decode is not an array of at least 2 finite numbers per component.
    """
    n = 2 * len(defaults)
    if isinstance(decode, list) and len(decode) >= n:
        try:
            vals = [float(r(v)) for v in decode[:n]]
        except (TypeError, ValueError):
            vals = None
        if vals and all(math.isfinite(v) for v in vals):
            return [(vals[2 * i], vals[2 * i + 1]) for i in range(len(defaults))]
    return list(defaults)


def _decode_table(pair, maxv):
    """!
    @brief Build the sample mapping of one /Decode pair.

    A sample v in 0..maxv maps to Dmin + v * (Dmax - Dmin) / maxv, clamped to 0..1 and
    scaled back to 0..maxv.

    @param pair (Dmin, Dmax) for a color range of 0..1.
    @param maxv Largest sample value, 2^bits - 1.
    @return List of maxv + 1 mapped sample values.
    """
    d0, d1 = pair
    return [
        int(round(max(0.0, min(1.0, d0 + v * (d1 - d0) / maxv)) * maxv)) for v in range(maxv + 1)
    ]


def _apply_decode(data, ncomp, bpc, pairs):
    """!
    @brief Map image samples through /Decode pairs for color ranges of 0..1.

    Pairs (0, 1) leave a component unchanged and (1, 0) invert it. 8-bit samples go
    through one 256-entry translate table per component. Below 8 bits (one component
    only) one table maps each packed byte. 16-bit samples are inverted bytewise; other
    16-bit intervals reduce the samples to their high byte first.

    @param data Packed sample bytes, rows padded to whole bytes.
    @param ncomp Number of color components.
    @param bpc Bits per component: 1, 2, 4, 8 or 16.
    @param pairs (Dmin, Dmax) per component.
    @return Tuple (data, bpc) with the mapped samples and their bit depth; None for
            several components below 8 bits.
    """
    if all(p == (0.0, 1.0) for p in pairs):
        return data, bpc
    if bpc < 8:
        if ncomp != 1:
            return None
        maxv = (1 << bpc) - 1
        smap = _decode_table(pairs[0], maxv)
        tbl = bytearray(256)
        for byte in range(256):
            v = 0
            for shift in range(8 - bpc, -1, -bpc):
                v |= smap[(byte >> shift) & maxv] << shift
            tbl[byte] = v
        return data.translate(bytes(tbl)), bpc
    if bpc == 16 and all(p in ((0.0, 1.0), (1.0, 0.0)) for p in pairs):
        tables = [_INVERT if p == (1.0, 0.0) else _IDENTITY for p in pairs for _ in (0, 1)]
    else:
        if bpc == 16:
            data, bpc = data[0::2], 8
        tables = [bytes(_decode_table(p, 255)) for p in pairs]
    if all(t == tables[0] for t in tables):
        return data.translate(tables[0]), bpc
    out = bytearray(data)
    step = len(tables)
    for i, t in enumerate(tables):
        if t != _IDENTITY:
            out[i::step] = data[i::step].translate(t)
    return bytes(out), bpc


def _why(why, kind, text):
    """!
    @brief Record why image_file returns no file.

    @param why List that receives (kind, text), or None.
    @param kind Problem kind for problem(): 'unsupported', 'malformed' or 'limit'.
    @param text Cause, without the image name.
    @return None.
    """
    if why is not None:
        why.append((kind, text))
    return None


def image_file(doc, s, res, why=None):
    """!
    @brief Convert an image XObject to a file format.

    DCTDecode data is returned unchanged as JPEG and JPXDecode data as JPEG 2000. Other
    native image filters are unsupported. Decoded samples become PNG for gray or
    Separation (1, 2, 4, 8 or 16 bits), RGB (8 or 16 bits), Lab (8 bits, converted
    with _lab_rgb), Indexed (1, 2, 4 or 8 bits, at most 2^bits palette entries) and
    CMYK (8 bits, converted to RGB). Image masks are treated as 1-bit gray.
    /Decode maps each component (see _apply_decode); Separation samples are inverted
    after it, since a full tint is dark. Lab uses the /Decode ranges for L*, a* and b*.
    For Indexed only the default and the reversed index range [2^bits-1 0] are
    supported. When the color space is unknown, the component count is guessed from
    the data length. Images with a declared width or height below 1 or above
    MAX_IMAGE_SIDE, more than MAX_IMAGE_PIXELS pixels, or a declared size above twice
    the data length plus 64 KiB are not exported; the size limits also apply to JPEG
    and JPEG 2000. A soft mask (/SMask) is ignored.

    @param doc Document used to resolve references and decode the stream.
    @param s Image XObject stream.
    @param res Resource dictionary for named color spaces.
    @param why List that receives (kind, text) for each reason the result is None or
           incomplete, or None.
    @return Tuple (extension, bytes) with extension 'jpg', 'jp2' or 'png'; None when
            unsupported.
    """
    r = doc.resolve
    w = r(s.get('Width', s.get('W')))
    h = r(s.get('Height', s.get('H')))
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (w, h)
    ):
        return _why(why, 'malformed', 'no valid /Width and /Height')
    w, h = int(w), int(h)
    if w <= 0 or h <= 0:
        return _why(why, 'malformed', 'size %d x %d' % (w, h))
    if w > MAX_IMAGE_SIDE or h > MAX_IMAGE_SIDE or w * h > MAX_IMAGE_PIXELS:
        return _why(
            why,
            'limit',
            'size %d x %d above MAX_IMAGE_SIDE (%d) or MAX_IMAGE_PIXELS (%d)'
            % (w, h, MAX_IMAGE_SIDE, MAX_IMAGE_PIXELS),
        )
    try:
        data, native = doc.decode(s)
    except PDFError as exc:
        return _why(why, 'unsupported', str(exc))
    if native in ('DCTDecode', 'DCT'):
        return ('jpg', data)
    if native == 'JPXDecode':
        return ('jp2', data)
    if native:
        return _why(why, 'unsupported', '%s not exported' % native)
    decode = r(s.get('Decode', s.get('D')))
    if r(s.get('ImageMask', s.get('IM'))):
        cs, bpc = ('gray', 1), 1
    else:
        bpc = r(s.get('BitsPerComponent', s.get('BPC'))) or 8
        if isinstance(bpc, float) and bpc.is_integer():
            bpc = int(bpc)
        cs = _colorspace(doc, s.get('ColorSpace', s.get('CS')), res)
        if cs is None:
            n = round(len(data) * 8 / max(1, w * h * bpc)) if bpc in (1, 2, 4, 8, 16) else 0
            cs = {1: ('gray', 1), 3: ('rgb', 3), 4: ('cmyk', 4)}.get(n)
            if cs is None:
                return _why(why, 'unsupported', 'color space not exported')
    ncomp = cs[1]
    if bpc not in (1, 2, 4, 8, 16):
        return _why(why, 'malformed', '/BitsPerComponent %r' % (bpc,))
    rowlen = (w * ncomp * bpc + 7) // 8
    need = rowlen * h
    if need > 2 * len(data) + 65536:
        # far less data than the declared size: corrupt or hostile
        return _why(why, 'malformed', 'declared size %d x %d far above the data' % (w, h))
    data = bytes(data[:need]).ljust(need, b'\0')
    kind = cs[0]
    if kind == 'lab' and bpc == 8:
        rng = cs[3]
        d = _decode_pairs(r, decode, [(0.0, 100.0), (rng[0], rng[1]), (rng[2], rng[3])])
        wp = cs[2]
        out = bytearray(len(data))
        memo = {}
        for i in range(0, len(data) - 2, 3):
            key = data[i : i + 3]
            v = memo.get(key)
            if v is None:
                if len(memo) >= MAX_LAB_MEMO:
                    memo.clear()
                c = _lab_rgb([d[j][0] + key[j] * (d[j][1] - d[j][0]) / 255.0 for j in range(3)], wp)
                v = memo[key] = bytes(int(round(x * 255)) for x in c)
            out[i : i + 3] = v
        return ('png', _png(w, h, 8, 2, bytes(out)))
    if kind == 'indexed' and bpc in (1, 2, 4, 8):
        top = float((1 << bpc) - 1)
        d = _decode_pairs(r, decode, [(0.0, top)])[0]
        if d == (top, 0.0):
            data = data.translate(_INVERT)  # bitwise NOT reverses every packed index
        elif d != (0.0, top):
            return _why(why, 'unsupported', 'Indexed /Decode %s not exported' % list(d))
        pal = bytearray()
        for i in range(min(cs[3], 2**bpc - 1) + 1):  # PNG allows 2^bitdepth entries
            pal += bytes(int(round(x * 255)) for x in _index_rgb(cs, i))
        return ('png', _png(w, h, bpc, 3, data, bytes(pal)))
    if (kind in ('gray', 'sep') and ncomp == 1) or (
        kind in ('rgb', 'cmyk') and bpc in (8, 16) and (bpc == 8 or kind == 'rgb')
    ):
        d = _decode_pairs(r, decode, [(0.0, 1.0)] * ncomp)
        if kind == 'sep':  # tint 1 is full ink: gray 1 - tint
            d = [(1.0 - a, 1.0 - b) for a, b in d]
        mapped = _apply_decode(data, ncomp, bpc, d)
        if mapped is None:
            return _why(why, 'unsupported', '/Decode not exported')
        data, bpc = mapped
        if kind == 'cmyk':
            return ('png', _png(w, h, 8, 2, _cmyk_to_rgb(data)))
        return ('png', _png(w, h, bpc, 2 if kind == 'rgb' else 0, data))
    return _why(
        why, 'unsupported', 'color space %s with %s bits not exported' % (kind.upper(), bpc)
    )


def _link(rel):
    """!
    @brief Turn a relative path into a Markdown link target.

    Path separators become forward slashes. The characters space % ( ) < > [ ] # are
    percent-encoded because they end or break a link target.

    @param rel Relative file path.
    @return Link target string.
    """
    rel = rel.replace(os.sep, '/')
    return ''.join('%%%02X' % ord(ch) if ch in ' %()<>[]#&\\' else ch for ch in rel)


def _fmt(v):
    """!
    @brief Format a number with at most 2 decimals and no trailing zeros.

    @param v Number.
    @return String; '0' for zero and negative zero.
    """
    s = ('%.2f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def _hex(c):
    """!
    @brief Convert an RGB color with components 0 to 1 to a @#rrggbb string.

    Components are clamped to 0..255 after scaling.

    @param c Sequence of 3 color components.
    @return Hex color string.
    """
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(round(v * 255)))) for v in c)


def _xml(t):
    """!
    @brief Escape &, <, > and double quotes for XML text and attribute values.

    Characters that XML 1.0 does not allow (controls other than tab, line feed and
    carriage return, U+FFFE, U+FFFF, lone surrogates) are removed.
    @param t Text.
    @return Escaped text.
    """
    t = _XML_BAD_RE.sub('', t)
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


## @brief Characters outside the XML 1.0 Char production.
_XML_BAD_RE = re.compile('[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def _family(c):
    """!
    @brief Choose an SVG font-family list for a character's font class.

    @param c Character with mono and serif flags.
    @return Monospace, serif or sans-serif font-family string.
    """
    if c.mono:
        return 'Courier New, monospace'
    return 'Times New Roman, serif' if c.serif else 'Helvetica, Arial, sans-serif'


def _svg_image(m, f):
    """!
    @brief Build an SVG image element that places an image like the PDF Do operator.

    The unit square of the image is mapped by the placement matrix m. Image row 0
    is at the top of the unit square (y = 1), as PDF requires. The element is meant
    for a group whose y axis points up (PDF user space).

    @param m Placement matrix [a b c d e f] (CTM at the Do operator).
    @param f Tuple (extension, bytes) with extension 'png' or 'jpg'.
    @return SVG image element with the image as a base64 data URI.
    """
    mime = 'image/png' if f[0] == 'png' else 'image/jpeg'
    return (
        '<image width="1" height="1" preserveAspectRatio="none" '
        'transform="matrix(%s) matrix(1 0 0 -1 0 1)" xlink:href="data:%s;base64,%s"/>'
        % (' '.join(_fmt(v) for v in m), mime, base64.b64encode(f[1]).decode('ascii'))
    )


def _upright(m):
    """!
    @brief Tell whether an image placement shows the image in its stored orientation.

    True when the x and y scales are positive and the shear terms are below 0.1 % of
    the larger scale. Then the image file looks like the page without a transform.

    @param m Placement matrix [a b c d e f].
    @return True for an upright, unmirrored, unsheared placement.
    """
    a, b, c, d = m[:4]
    tol = 1e-3 * max(abs(a), abs(d))
    return a > 0 and d > 0 and abs(b) <= tol and abs(c) <= tol


def image_svg(shape, f):
    """!
    @brief Wrap one placed image in an SVG document that applies its placement matrix.

    The view is the image's bounding box on the page; width and height are in points.
    The image is drawn rotated, mirrored or sheared as on the page (see _svg_image).

    @param shape Image shape with bbox and matrix.
    @param f Tuple (extension, bytes) with extension 'png' or 'jpg'.
    @return SVG text ending with a newline.
    """
    x0, y0, x1, y1 = shape.bbox
    w, h = x1 - x0, y1 - y0
    return (
        '\n'.join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" '
                'width="%spt" height="%spt" viewBox="0 0 %s %s">'
                % (_fmt(w), _fmt(h), _fmt(w), _fmt(h)),
                '<g transform="matrix(1 0 0 -1 %s %s)">' % (_fmt(-x0), _fmt(y1)),
                _svg_image(shape.matrix, f),
                '</g>',
                '</svg>',
            ]
        )
        + '\n'
    )


def region_svg(reg, box, raster_for):
    """!
    @brief Render a figure region as a standalone SVG document.

    The view covers the region box plus a 2 pt margin, clipped to the page box. Width
    and height are in points. A white background is drawn first. Paths keep fill,
    even-odd fill rule and stroke; the stroke width is at least 0.25. PNG and JPEG images
    are embedded as base64 data URIs; other formats are skipped. Visible text is drawn
    as horizontal lines with font size, family, color and bold weight; lines of more
    than 1 character whose fonts have known widths get a textLength matching their PDF
    width. Rotated characters are drawn one by one with their own transform.

    @param reg Region to render.
    @param box Displayed page box (x0, y0, x1, y1) in points.
    @param raster_for Function that maps an image shape to (extension, bytes) or None.
    @return SVG text ending with a newline.
    """
    x0 = max(reg.bbox[0] - 2, box[0])
    y0 = max(reg.bbox[1] - 2, box[1])
    x1 = min(reg.bbox[2] + 2, box[2])
    y1 = min(reg.bbox[3] + 2, box[3])
    w, h = x1 - x0, y1 - y0
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="%spt" height="%spt" viewBox="0 0 %s %s">' % (_fmt(w), _fmt(h), _fmt(w), _fmt(h)),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g transform="matrix(1 0 0 -1 %s %s)">' % (_fmt(-x0), _fmt(y1)),
    ]
    for s in reg.shapes:
        if s.kind == 'path':
            if not math.isfinite(sum(v for seg in s.segs for v in seg[1:])):
                continue  # a path with non-finite coordinates cannot be drawn
            d = []
            for seg in s.segs:
                d.append('Z' if seg[0] == 'Z' else seg[0] + ' '.join(_fmt(v) for v in seg[1:]))
            attrs = 'fill="%s"' % (_hex(s.fill) if s.fill else 'none')
            if s.fill and s.evenodd:
                attrs += ' fill-rule="evenodd"'
            if s.stroke:
                attrs += ' stroke="%s" stroke-width="%s"' % (_hex(s.stroke), _fmt(max(s.lw, 0.25)))
            out.append('<path d="%s" %s/>' % (' '.join(d), attrs))
        else:
            f = raster_for(s)
            if f and f[0] in ('png', 'jpg'):
                out.append(_svg_image(s.matrix, f))
    out.append('</g>')
    visible = [c for c in reg.chars if not c.invisible]
    for ln in build_lines(visible, split=0.9):
        c = ln.ref
        attrs = 'x="%s" y="%s" font-size="%s" font-family="%s" fill="%s"' % (
            _fmt(ln.x0 - x0),
            _fmt(y1 - ln.y),
            _fmt(ln.size),
            _family(c),
            _hex(c.color),
        )
        if ln.bold:
            attrs += ' font-weight="bold"'
        if len(ln.text) > 1 and ln.x1 - ln.x0 > 1 and not c.est:
            attrs += ' textLength="%s" lengthAdjust="spacingAndGlyphs"' % _fmt(ln.x1 - ln.x0)
        out.append('<text %s>%s</text>' % (attrs, _xml(ln.text)))
    for c in visible:
        if c.upright or not c.text.strip():
            continue
        (ux, uy), (vx, vy) = c.u, c.v
        out.append(
            '<text transform="matrix(%s %s %s %s %s %s)" font-size="%s" font-family="%s" fill="%s">%s</text>'
            % (
                _fmt(ux),
                _fmt(-uy),
                _fmt(-vx),
                _fmt(vy),
                _fmt(c.x - x0),
                _fmt(y1 - c.y),
                _fmt(c.size),
                _family(c),
                _hex(c.color),
                _xml(c.text),
            )
        )
    out.append('</svg>')
    return '\n'.join(out) + '\n'


def region_label(reg):
    """!
    @brief Classify a vector figure as 'Table' or 'Figure'.

    The result is 'Table' when the figure has at least 4 non-white paths, at least 4
    characters, no images, only axis-aligned lines and rectangles, and at least 80 % of
    its characters lie inside the paths' common bounding box (cell text). Chart axis
    labels sit outside the plot area, so charts keep the label 'Figure'.

    @param reg Region to classify.
    @return 'Table' or 'Figure'.
    """
    paths = [s for s in reg.shapes if s.kind == 'path' and not s.white]
    if len(paths) < 4 or len(reg.chars) < 4 or any(s.kind == 'image' for s in reg.shapes):
        return 'Figure'
    if not all(s.axis for s in paths):
        return 'Figure'
    bb = paths[0].bbox
    for s in paths[1:]:
        bb = _union(bb, s.bbox)
    inside = sum(
        1
        for c in reg.chars
        if bb[0] <= (c.x + c.x2) / 2 <= bb[2] and bb[1] <= c.y + 0.3 * c.size <= bb[3]
    )
    return 'Table' if inside >= 0.8 * len(reg.chars) else 'Figure'


class Exporter:
    """!
    @brief Writes figure files for regions and returns the Markdown that references them.

    @par Attributes
    - doc: Document the images come from.
    - assets_dir: directory that receives the figure files.
    - md_dir: directory of the Markdown file; links are relative to it.
    - cache: converted images keyed by id of the image stream; None for failures.
    - files: (alt text, file path) of written raster images keyed by (id of the image
      stream, appearance); a repeated image with the same appearance reuses both.
    - counts: Counter of exported figures per label ('Image', 'Table', 'Figure').
    - names: object number keyed by id of each stream object, built on first use.
    """

    def __init__(self, doc, assets_dir, md_dir):
        """!
        @brief Set up an exporter with empty caches and counters.

        @param doc Document the images come from.
        @param assets_dir Directory for figure files; created on first write.
        @param md_dir Directory that links are made relative to.
        """
        ## Document the images come from.
        self.doc = doc
        ## directory that receives the figure files.
        self.assets_dir = assets_dir
        ## directory of the Markdown file; links are relative to it.
        self.md_dir = md_dir
        ## converted images keyed by id of the image stream; None for failures.
        self.cache = {}
        ## (alt text, file path) of written raster images keyed by (id of the image
        ## stream, appearance); a repeated image with the same appearance reuses both.
        self.files = {}
        ## Counter of exported figures per label ('Image', 'Table', 'Figure').
        self.counts = Counter()
        ## object number keyed by id of each stream object, built on first use.
        self.names = None

    def _image_name(self, stream):
        """!
        @brief Name an image stream for problem reports.

        @param stream Image XObject stream.
        @return 'image N 0 R' with its object number, or 'image' for a direct object.
        """
        if self.names is None:
            self.names = {
                id(o): num for num, o in self.doc.objects.items() if isinstance(o, Stream)
            }
        num = self.names.get(id(stream))
        return 'image' if num is None else 'image %d 0 R' % num

    def raster(self, shape):
        """!
        @brief Convert an image shape to a file format, with caching per image stream.

        Each reason why the image is not exported, or exported incompletely, is
        recorded once with problem(); an exception during conversion is recorded as
        'internal' and cached as None.

        @param shape Image shape with stream and resources.
        @return Tuple (extension, bytes) from image_file, or None.
        """
        key = id(shape.stream)
        if key not in self.cache:
            why = []
            try:
                self.cache[key] = image_file(self.doc, shape.stream, shape.resources, why)
            except Exception as exc:
                self.cache[key] = None
                why.append(
                    ('internal', '%s: %s' % (type(exc).__name__, ' '.join(str(exc).split())))
                )
            for kind, text in why:
                problem(kind, '%s: %s' % (self._image_name(shape.stream), text))
        return self.cache[key]

    ## @brief Path of the input PDF file; _write refuses a target that refers to it.
    ## None skips the check. convert() and convert_split() set it.
    source = None

    def _write(self, name, data):
        """!
        @brief Write a file into the assets directory, creating the directory if needed.

        The file is written through a temporary file and os.replace (see _write_atomic),
        so an existing file stays intact when the write fails.

        @param name File name.
        @param data File contents as bytes.
        @return Path of the written file.
        @exception UsageError when the target refers to the input file (see _check_output).
        """
        os.makedirs(self.assets_dir, exist_ok=True)
        path = os.path.join(self.assets_dir, name)
        if self.source is not None:
            _check_output(path, self.source)
        _write_atomic(path, data)
        return path

    def _rel(self, path):
        """!
        @brief Make a path into a link target relative to the Markdown directory.

        On Windows a path on another drive has no relative form; it becomes a file:
        URI instead.
        @param path File path.
        @return Link target string (see _link).
        """
        try:
            return _link(os.path.relpath(path, self.md_dir))
        except ValueError:  # different drive
            import pathlib

            return pathlib.Path(os.path.abspath(path)).as_uri()

    def figure(self, page, box, reg):
        """!
        @brief Export a figure region and return its Markdown image reference.

        Raster regions are labelled 'Image', other regions 'Table' or 'Figure' (see
        region_label). The alt text is the label and its running number; only exported
        figures are numbered. A raster image placed upright (see _upright) is written
        as its file; one placed rotated, mirrored or sheared, including by the page
        rotation, is written as an SVG that applies the placement (see image_svg).
        JPEG 2000 cannot be embedded in SVG and is written as its file; the lost
        transform is reported. A raster image already written for the same stream and
        the same appearance (upright, or the same linear part of the matrix) is linked
        again with the same file and alt text. Raster files are named pNNN_imgN.ext and
        vector figures pNNN_@<label@>N.svg. Problems are recorded for this page.

        @param page 1-based page number used in the file name.
        @param box Displayed page box (x0, y0, x1, y1) in points.
        @param reg Region to export.
        @return Markdown image link, or an HTML comment when the image encoding is
                unsupported.
        """
        before = _CURRENT_PAGE[0]
        _CURRENT_PAGE[0] = page
        try:
            label = 'Image' if reg.raster else region_label(reg)
            if reg.raster:
                sh = reg.raster
                f = self.raster(sh)
                if f is None:
                    return (
                        '<!-- image on page %d not exported: unsupported image encoding -->' % page
                    )
                svg = f[0] in ('png', 'jpg') and not _upright(sh.matrix)
                look = tuple(round(v, 2) for v in sh.matrix[:4]) if svg else None
                key = (id(sh.stream), look)
                if key in self.files:  # same image and appearance again: same file and number
                    alt, path = self.files[key]
                    return '![%s](%s)' % (alt, self._rel(path))
                if not svg and not _upright(sh.matrix):
                    problem(
                        'unsupported',
                        '%s: JPEG 2000 image written without its rotation, mirroring or shear'
                        % self._image_name(sh.stream),
                    )
                self.counts[label] += 1
                n = self.counts[label]
                alt = '%s %d' % (label, n)
                if svg:
                    data = image_svg(sh, f).encode('utf-8')
                    path = self._write('p%03d_img%d.svg' % (page, n), data)
                else:
                    path = self._write('p%03d_img%d.%s' % (page, n, f[0]), f[1])
                self.files[key] = (alt, path)
                return '![%s](%s)' % (alt, self._rel(path))
            for s in reg.shapes:
                if s.kind == 'image':
                    f = self.raster(s)
                    if f is not None and f[0] not in ('png', 'jpg'):
                        problem(
                            'unsupported',
                            '%s: JPEG 2000 image not embedded in SVG figure'
                            % self._image_name(s.stream),
                        )
            self.counts[label] += 1
            n = self.counts[label]
            alt = '%s %d' % (label, n)
            svg = region_svg(reg, box, self.raster)
            path = self._write('p%03d_%s%d.svg' % (page, label.lower(), n), svg.encode('utf-8'))
            return '![%s](%s)' % (alt, self._rel(path))
        finally:
            _CURRENT_PAGE[0] = before


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

## @brief Matches a list item marker followed by whitespace at the start of a line.
##
## Group 1: a bullet character (for example U+2022, U+25CF, U+25A0, U+2713, -, en dash,
## em dash, *). Group 2: a number of 1 to 3 digits followed by . or ). Group 3: a
## lowercase letter followed by ), optionally preceded by (, or a 1 to 3 digit number
## in parentheses.
LIST_RE = re.compile(
    r'^(?:([\u2022\u25e6\u25aa\u25ab\u2023\u2219\u00b7\u25cf\u25cb\u25a0\u25a1\u27a2\u25ba\u2713\-\u2013\u2014*])'
    r'|(\d{1,3}[.)])|(\(?[a-z]\)|\(\d{1,3}\)))\s+'
)
## @brief Matches a bare page number such as 12, Page 3, 3/10 or 3 of 10.
##
## Matching ignores case.
##
## Used with fullmatch on header and footer lines.
_PAGENUM_RE = re.compile(r'(?:page\s*)?\d+(?:\s*(?:/|of)\s*\d+)?', re.I)


def _esc(t, brackets=False):
    """!
    @brief Escape text for Markdown.

    Backslashes, * and backticks get a backslash. Underscores not between two letters
    or digits get a backslash. A < followed by a letter, /, ! or ? becomes &lt;.
    "](" gets a backslash before the bracket, so no inline link or image forms.
    An & that starts an entity reference (@&name; or @&#123;) gets a backslash.

    @param t Text.
    @param brackets True to escape every [ and ], for text inside a link label.
    @return Escaped text.
    """
    t = t.replace('\\', '\\\\').replace('*', '\\*').replace('`', '\\`')
    t = re.sub(r'(?<![^\W_])_|_(?![^\W_])', r'\\_', t)
    if brackets:
        t = t.replace('[', '\\[').replace(']', '\\]')
    else:
        t = t.replace('](', '\\](')
    t = re.sub(r'&(?=#?\w+;)', r'\\&', t)
    return re.sub(r'<(?=[A-Za-z/!?])', '&lt;', t)


## @brief Text at the start of a block that Markdown reads as syntax: ATX heading,
## block quote, bullet marker, numbered list marker "1." / "1)", thematic break,
## tilde code fence, or link reference definition "[label]:".
_BLOCK_START_RE = re.compile(
    r'^(?:#{1,6}(?:\s|$)|>|[+\-](?:\s|$)|\d+[.)](?:\s|$)|~{3,}' r'|-(?:\s*-){2,}\s*$|\[[^\]]*\]:)'
)


def _esc_start(text):
    """!
    @brief Backslash-escape Markdown block syntax at the start of a paragraph or item.
    @param text escaped Markdown text (see _esc)
    @return text that starts as plain text
    """
    m = _BLOCK_START_RE.match(text)
    if not m:
        return text
    if text[0].isdigit():  # "12." -> "12\."
        k = m.end() - 1 if text[m.end() - 1].isspace() else m.end()
        return text[: k - 1] + '\\' + text[k - 1 :]
    return '\\' + text


def _emphasis(segs):
    """!
    @brief Render styled text segments as Markdown with bold and italic markers.

    Adjacent segments with the same style are merged. Text is escaped with _esc. Bold
    uses **, italic *, both ***. Markers enclose the stripped text; leading and trailing
    whitespace stays outside them. Whitespace-only segments get no markers. Where a
    marker would sit between punctuation inside and a letter or digit outside, Markdown
    does not apply it; such spans use the HTML tags @<b@> and @<i@> instead.

    @param segs Iterable of (text, bold, italic) tuples.
    @return Markdown text.
    """
    merged = []
    for t, b, i in segs:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1][0] += t
        else:
            merged.append([t, b, i])
    texts = [_esc(t) for t, _, _ in merged]
    out = []
    for k, (t, (_, b, i)) in enumerate(zip(texts, merged)):
        core = t.strip()
        if not (b or i) or not core:
            out.append(t)
            continue
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()) :]
        before = '' if lead or k == 0 else texts[k - 1][-1:]
        after = '' if trail or k + 1 == len(texts) else texts[k + 1][:1]
        punct = lambda ch: bool(ch) and not ch.isalnum() and not ch.isspace()
        if (punct(core[0]) and before.isalnum()) or (punct(core[-1]) and after.isalnum()):
            o = ('<b>' if b else '') + ('<i>' if i else '')
            c = ('</i>' if i else '') + ('</b>' if b else '')
            out.append(lead + o + core + c + trail)
            continue
        mk = '***' if b and i else ('**' if b else '*')
        out.append(lead + mk + core + mk + trail)
    return ''.join(out)


def _drop_prefix(segs, n):
    """!
    @brief Remove the first n characters from a list of styled segments.

    Segments that become empty are dropped.

    @param segs Iterable of (text, bold, italic) tuples.
    @param n Number of characters to remove.
    @return List of remaining (text, bold, italic) tuples.
    """
    out = []
    for t, b, i in segs:
        if n > 0:
            cut = min(n, len(t))
            t, n = t[cut:], n - cut
        if t:
            out.append((t, b, i))
    return out


class Para:
    """!
    @brief A block of consecutive text lines that form one Markdown block.

    @par Attributes
    - kind: 'p' (paragraph), 'h' (heading) or 'li' (list item).
    - level: heading level 1 to 4; 0 for other kinds.
    - mono: True when the first line uses a monospaced font (code block).
    - lines: the Line objects of the block.
    """

    def __init__(self, kind, level, line):
        """!
        @brief Start a block with its first line.

        @param kind 'p', 'h' or 'li'.
        @param level Heading level, or 0.
        @param line First Line of the block.
        """
        ## 'p' (paragraph), 'h' (heading) or 'li' (list item).
        self.kind = kind
        ## heading level 1 to 4; 0 for other kinds.
        self.level = level
        ## True when the first line uses a monospaced font (code block).
        self.mono = line.mono
        ## the Line objects of the block.
        self.lines = [line]


class MarkdownWriter:
    """!
    @brief Groups text lines into paragraphs, headings, list items and code blocks and
    renders Markdown.

    @par Attributes
    - levels: map from font size (rounded to 0.5) to heading level.
    - blocks: finished Markdown blocks.
    - cur: Para being built, or None.
    - prev: last line added, or None.
    - new_page: True when the next line is the first of a new page and a block is open.
    """

    def __init__(self, heading_levels):
        """!
        @brief Create an empty writer.

        @param heading_levels Map from font size (rounded to 0.5) to heading level 1 to 4.
        """
        ## map from font size (rounded to 0.5) to heading level.
        self.levels = heading_levels
        ## finished Markdown blocks.
        self.blocks = []
        ## Para being built, or None.
        self.cur = None
        ## last line added, or None.
        self.prev = None
        ## True when the next line is the first of a new page and a block is open.
        self.new_page = False

    def classify(self, ln):
        """!
        @brief Decide the block kind of a line.

        Monospaced lines are paragraphs. A line whose rounded size has a heading level and
        whose text has at most 200 characters is a heading. A line starting with a list
        marker (LIST_RE) is a list item. All others are paragraphs.

        @param ln Line to classify.
        @return Tuple (kind, level) with kind 'p', 'h' or 'li'.
        """
        if ln.mono:
            return 'p', 0
        lvl = self.levels.get(round(ln.size * 2) / 2)
        if lvl and len(ln.text) <= 200:
            return 'h', lvl
        if LIST_RE.match(ln.text):
            return 'li', 0
        return 'p', 0

    def start_page(self, marker=None):
        """!
        @brief Mark the start of a page.

        With a marker, the open block is finished and the marker is added as its own block.
        The first line of the page can still continue the open paragraph.

        @param marker Text block to insert before the page, or None.
        """
        if marker:
            self.flush()
            self.blocks.append(marker)
        self.new_page = self.cur is not None

    def add_figure(self, md):
        """!
        @brief Finish the open block and add a figure reference as its own block.

        @param md Markdown text for the figure.
        """
        self.flush()
        self.blocks.append(md)

    def add_line(self, ln):
        """!
        @brief Add a line, either continuing the open block or starting a new one.

        After a page break or column change, a line continues only a non-monospaced
        paragraph when the sizes differ by less than 0.6, the previous line does not end in
        . ! ? : ; quote ) or ], and the line starts with a lowercase letter.
        Otherwise the line must lie below the previous one by at most 1.65 em (em = larger of
        the two sizes). Then:
        - a heading continues a heading of the same level within 1.35 em;
        - a list item, or any line after a heading, starts a new block;
        - a line continues a list item when indented more than 0.3 em past its first line;
        - monospaced lines continue only monospaced blocks;
        - a paragraph line continues when sizes differ by less than 0.6, boldness matches,
          it starts at most 1.2 em right of the previous line, and the previous line does
          not end in . ! ? : more than 3 em before the column's right edge.

        @param ln Line to add.
        """
        kind, level = self.classify(ln)
        cur, prev = self.cur, self.prev
        cont = False
        if cur is not None and prev is not None:
            sz = max(ln.size, prev.size)
            if self.new_page or ln.col != prev.col:  # page break or column change
                cont = (
                    kind == 'p'
                    and cur.kind == 'p'
                    and not cur.mono
                    and not ln.mono
                    and abs(ln.size - prev.size) < 0.6
                    and not re.search(r'[.!?:;"\u201d)\]]$', prev.text)
                    and ln.text[:1].islower()
                )
            else:
                gap = prev.y - ln.y
                if 0 < gap <= 1.65 * sz:
                    if kind == 'h':
                        cont = cur.kind == 'h' and cur.level == level and gap <= 1.35 * sz
                    elif kind == 'li' or cur.kind == 'h':
                        cont = False
                    elif cur.kind == 'li':
                        cont = ln.x0 > cur.lines[0].x0 + 0.3 * ln.size
                    elif cur.mono or ln.mono:
                        cont = cur.mono and ln.mono
                    else:
                        cont = (
                            abs(ln.size - prev.size) < 0.6
                            and ln.bold == prev.bold
                            and ln.x0 <= prev.x0 + 1.2 * ln.size
                            and not (
                                re.search(r'[.!?:]$', prev.text) and prev.x1 < prev.right - 3 * sz
                            )
                        )
        if cont:
            cur.lines.append(ln)
        else:
            self.flush()
            self.cur = Para(kind, level, ln)
        self.prev = ln
        self.new_page = False

    def flush(self):
        """!
        @brief Render the open block into Markdown and append it to the blocks.

        Monospaced blocks become fenced code with indentation from the line offsets (one
        space per 0.6 em). Lines are joined with spaces; a word broken by a hyphen at the
        line end is joined without the hyphen when the next line starts lowercase. Headings
        get one # per level. Bullet items become "- ", numbered items "N. ", and lettered
        items "- (a) ". Paragraphs and item bodies pass through _esc_start so they do not
        start with Markdown block syntax. A trailing # in a heading is escaped. The code
        fence is one backtick longer than the longest backtick run in the block.
        """
        p = self.cur
        self.cur = None
        if p is None:
            return
        if p.mono:
            left = min(l.ix0 for l in p.lines)
            rows = []
            for l in p.lines:
                indent = int(round((l.ix0 - left) / max(0.6 * l.size, 1)))
                rows.append(' ' * indent + l.text)
            body = '\n'.join(rows)
            ticks = max([len(m) for m in re.findall(r'`+', body)] + [2]) + 1
            fence = '`' * ticks  # longer than any backtick run inside
            self.blocks.append(fence + '\n' + body + '\n' + fence)
            return
        segs = []
        for l in p.lines:
            if segs:
                last = segs[-1][0]
                if re.search(r'[A-Za-z\u00c0-\u024f]-$', last) and l.text[:1].islower():
                    segs[-1] = (last[:-1],) + tuple(segs[-1][1:])
                else:
                    segs.append((' ', False, False))
            segs += list(l.segs)
        if p.kind == 'h':
            text = _esc(''.join(s[0] for s in segs).strip())
            text = re.sub(
                r'(#+)$', lambda m: '\\#' * len(m.group(1)), text
            )  # not a closing sequence
            self.blocks.append('#' * p.level + ' ' + text)
            return
        if p.kind == 'li':
            plain = ''.join(s[0] for s in segs)
            m = LIST_RE.match(plain)
            body = _esc_start(_emphasis(_drop_prefix(segs, m.end())))
            if m.group(1):
                self.blocks.append('- ' + body)
            elif m.group(2):
                self.blocks.append(m.group(2)[:-1] + '. ' + body)
            else:
                self.blocks.append('- ' + m.group(3) + ' ' + body)
            return
        self.blocks.append(_esc_start(_emphasis(segs)))

    def text(self):
        """!
        @brief Finish the open block and return the whole Markdown text.

        Consecutive bullet items and consecutive numbered items are joined into one list.
        Other blocks are separated by a blank line.

        @return Markdown text ending with a newline.
        """
        self.flush()
        out = []
        for b in self.blocks:
            # keep consecutive list items in one list
            if out and b[:2] == '- ' and out[-1][:2] == '- ':
                out[-1] += '\n' + b
            elif out and re.match(r'\d+\. ', b) and re.match(r'\d+\. ', out[-1].split('\n')[-1]):
                out[-1] += '\n' + b
            else:
                out.append(b)
        return '\n\n'.join(out) + '\n'


def _drop_repeats(layouts):
    """!
    @brief Remove running headers, footers, page numbers and repeated figures.

    The header and footer bands are the top and bottom 10 % of the page box height. A
    band line is dropped when its lowercase text, with digit runs replaced by #, occurs
    in the same band on at least max(3, 0.5 x page count) pages, or when it is a bare
    page number (_PAGENUM_RE). With at least 3 pages, figure regions whose signature
    (rounded box and shape count) occurs at least as often are dropped too.

    @param layouts List of PageLayout objects; lines and regions are updated in place.
    """
    n = len(layouts)
    limit = max(3, 0.5 * n)
    keys = Counter()
    sigs = Counter()
    for lay in layouts:
        h = lay.box[3] - lay.box[1]
        top_band, bot_band = lay.box[3] - 0.1 * h, lay.box[1] + 0.1 * h
        page_keys = set()
        for l in lay.lines:
            if l.y >= top_band or l.y <= bot_band:
                page_keys.add((re.sub(r'\d+', '#', l.text.lower()), l.y >= top_band))
        keys.update(page_keys)
        sigs.update({r.signature() for r in lay.regions})
    for lay in layouts:
        h = lay.box[3] - lay.box[1]
        top_band, bot_band = lay.box[3] - 0.1 * h, lay.box[1] + 0.1 * h
        kept = []
        for l in lay.lines:
            band = l.y >= top_band or l.y <= bot_band
            if band and (
                keys[(re.sub(r'\d+', '#', l.text.lower()), l.y >= top_band)] >= limit
                or _PAGENUM_RE.fullmatch(l.text.strip())
            ):
                continue
            kept.append(l)
        lay.lines = kept
        if n >= 3:
            lay.regions = [r for r in lay.regions if sigs[r.signature()] < limit]


def _heading_levels(layouts):
    """!
    @brief Derive heading levels from the font sizes of the text.

    Sizes are rounded to 0.5 and weighted by character count over non-monospaced lines.
    The most common size is the body size. Sizes at least 1.12 x the body size that hold
    at most 20 % of all characters are headings; the largest is level 1, the next level
    2, and so on, capped at level 4.

    @param layouts List of PageLayout objects.
    @return Map from rounded size to heading level; empty when there is no text.
    """
    chars = Counter()
    for lay in layouts:
        for l in lay.lines:
            if not l.mono:
                chars[round(l.size * 2) / 2] += len(l.text)
    if not chars:
        return {}
    body = chars.most_common(1)[0][0]
    total = sum(chars.values())
    sizes = sorted((s for s in chars if s >= body * 1.12 and chars[s] <= 0.2 * total), reverse=True)
    return {s: min(i + 1, 4) for i, s in enumerate(sizes)}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _box(doc, inh):
    """!
    @brief Return the visible page box: the CropBox clipped to the MediaBox.

    Boxes are normalized to x0 <= x1 and y0 <= y1. A box with non-finite values or a
    width or height below 1 point is ignored. When CropBox and MediaBox do not
    overlap by at least 1 point, the MediaBox is used.

    @param doc Document used to resolve references.
    @param inh Page attributes including inherited ones.
    @return Tuple (x0, y0, x1, y1) in points; (0, 0, 612, 792) (US Letter) when neither
            box is valid.
    """
    boxes = {}
    for key in ('CropBox', 'MediaBox'):
        b = doc.resolve(inh.get(key))
        if isinstance(b, list) and len(b) == 4:
            try:
                v = [float(doc.resolve(x)) for x in b]
            except (TypeError, ValueError):
                continue
            box = (min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))
            if all(math.isfinite(x) for x in box) and box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
                boxes[key] = box
    crop, media = boxes.get('CropBox'), boxes.get('MediaBox')
    if crop and media:  # the visible area is the CropBox clipped to the MediaBox
        box = (
            max(crop[0], media[0]),
            max(crop[1], media[1]),
            min(crop[2], media[2]),
            min(crop[3], media[3]),
        )
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            return box
        return media
    return crop or media or (0.0, 0.0, 612.0, 792.0)


def _rotation(box, rotate):
    """!
    @brief Return the displayed page box and initial current transformation matrix (CTM)
    for a /Rotate value.

    The CTM maps user space to the page as displayed, so rotated pages read upright.
    For 90 and 270 degrees the displayed box is (0, 0, height, width). Values that are
    not integers count as 0; other values are taken modulo 360, and only 90, 180 and 270
    rotate.

    @param box Page box (x0, y0, x1, y1) in points.
    @param rotate /Rotate value.
    @return Tuple (displayed box, CTM as 6 numbers).
    """
    x0, y0, x1, y1 = box
    rot = rotate % 360 if isinstance(rotate, int) else 0
    w, h = x1 - x0, y1 - y0
    if rot == 90:
        return (0.0, 0.0, h, w), [0.0, -1.0, 1.0, 0.0, -y0, x1]
    if rot == 180:
        return box, [-1.0, 0.0, 0.0, -1.0, x0 + x1, y0 + y1]
    if rot == 270:
        return (0.0, 0.0, h, w), [0.0, 1.0, -1.0, 0.0, y1, -x0]
    return box, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def _parse_pages(spec):
    """!
    @brief Parse a page selection such as "1-3,7" into a set of page numbers.

    Parts are separated by commas; empty parts are ignored. A part "a-b" adds a through
    b inclusive.

    @param spec Selection string.
    @return PageSelection of 1-based page numbers, never empty.
    @exception ValueError when a part is not a decimal number (ASCII digits) or range
               "a-b", a page number is below 1, a range is reversed, or the selection
               is empty.
    """
    ranges = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r'([0-9]+)(?:\s*-\s*([0-9]+))?', part)
        if not m:
            raise ValueError(part)
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a:
            raise ValueError(part)
        ranges.append((a, b))
    if not ranges:
        raise ValueError(spec)
    return PageSelection(ranges)


class PageSelection:
    """!
    @brief Set of page numbers stored as inclusive ranges, so a range of any size
    costs no memory.
    @par Attributes
    - ranges: list of (first, last) pairs, 1-based and inclusive
    """

    def __init__(self, ranges):
        """!
        @brief Store the ranges.
        @param ranges iterable of (first, last) pairs with first <= last
        """
        ## list of (first, last) pairs, 1-based and inclusive
        self.ranges = sorted(ranges)

    def __contains__(self, n):
        """!
        @brief Test whether page n is selected.
        @param n 1-based page number
        @return True when n lies in one of the ranges
        """
        return any(a <= n <= b for a, b in self.ranges)

    def __bool__(self):
        """!
        @brief Test whether any page is selected.
        @return True when there is at least one range
        """
        return bool(self.ranges)


def analyze(pdf_path, pages=None, figures=True, log=None):
    """!
    @brief Parse the file and lay out the selected pages.

    Page headers and footers are removed from the layouts (see _drop_repeats). A page
    whose content fails to process is recorded with problem() and kept as an empty
    layout. When /Rotate is set but less than half the text reads upright, the
    unrotated view is used if it has more upright characters.

    @param pdf_path Path of the PDF file.
    @param pages PageSelection (or set) of 1-based page numbers to process; None for all.
    @param figures False to skip figure detection.
    @param log Function that receives progress messages, or None.
    @return Tuple (Document, page list, list of PageLayout).
    @exception PDFError when the file structure cannot be parsed, the file is encrypted,
               or it has no pages.
    @exception UsageError when no selected page exists in the document.
    """
    PROBLEMS.clear()
    _CURRENT_PAGE[0] = None
    with open(pdf_path, 'rb') as fh:
        data = fh.read()
    try:
        doc = Document(data)
    except PDFError:
        raise
    except Exception as exc:  # malformed file structure
        raise PDFError('cannot parse file structure (%s: %s)' % (type(exc).__name__, exc))
    if doc.encrypted:
        raise PDFError('encrypted PDF files are not supported')
    try:
        page_list = doc.pages()
    except Exception as exc:  # malformed page tree
        raise PDFError('cannot read the page tree (%s: %s)' % (type(exc).__name__, exc))
    if not page_list:
        raise PDFError('no pages found')
    if pages is not None and not isinstance(pages, PageSelection):
        pages = PageSelection((n, n) for n in pages)  # a set or list of page numbers
    if pages:
        missing = [r for r in pages.ranges if r[0] > len(page_list)]
        if len(missing) == len(pages.ranges):
            raise UsageError('no selected page exists; the document has %d pages' % len(page_list))
        if (missing or pages.ranges[-1][1] > len(page_list)) and log:
            log('pages above %d do not exist and are skipped' % len(page_list))
    fonts = {}
    layouts = []
    for i, (page, inh) in enumerate(page_list, 1):
        if pages and i not in pages:
            continue
        if log:
            log('page %d/%d' % (i, len(page_list)))
        _CURRENT_PAGE[0] = i
        box = _box(doc, inh)
        try:
            contents = doc.resolve(page.get('Contents'))
            items = contents if isinstance(contents, list) else [contents]
            data = b'\n'.join(
                doc.decoded(doc.resolve(s)) for s in items if isinstance(doc.resolve(s), Stream)
            )
            ident = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]

            def interpret(ctm):
                pc = PageContent(doc, fonts)
                gs = GState()
                gs.ctm = ctm
                pc.run(data, inh.get('Resources'), gs)
                pc.run_annotations(page, ctm)
                return pc

            rbox, ctm = _rotation(box, doc.resolve(inh.get('Rotate')))
            pc = interpret(ctm)
            if ctm != ident and not pc.rotated_ok():
                # /Rotate set on content laid out upright: keep the unrotated view
                alt = interpret(ident)
                if alt.upright_count() > pc.upright_count():
                    pc, rbox = alt, box
            box = rbox
            layouts.append(layout_page(i, pc, box, figures))
        except Exception as exc:
            problem('internal', 'page skipped (%s: %s)' % (type(exc).__name__, exc))
            layouts.append(PageLayout(i, box, [], []))
    _CURRENT_PAGE[0] = None
    _drop_repeats(layouts)
    return doc, page_list, layouts


def _page_markdown(writer, exporter, lay, marker=None):
    """!
    @brief Feed one page layout into a Markdown writer in reading order.

    Objects removed as headers or footers are skipped. Figures are exported through the
    exporter.

    @param writer MarkdownWriter.
    @param exporter Exporter for figure files.
    @param lay PageLayout of the page.
    @param marker Block to insert before the page, or None.
    """
    writer.start_page(marker)
    keep = {id(o) for o in lay.lines + lay.regions}
    for obj in lay.order:
        if id(obj) not in keep:
            continue  # dropped as page header or footer
        if isinstance(obj, Region):
            writer.add_figure(exporter.figure(lay.number, lay.box, obj))
        else:
            writer.add_line(obj)


def _same_file(path, source):
    """!
    @brief Tell whether a path refers to the same file as the source path.

    True when both resolve to the same path after following symbolic links
    (os.path.realpath), or when both exist and os.stat (which follows links) gives the
    same device and a non-zero inode number, which also matches hard links.

    @param path Output path; it need not exist.
    @param source Path of the input file.
    @return True when writing to path through open() would change source.
    """
    try:
        if os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(source)):
            return True
    except (OSError, ValueError):
        pass
    try:
        a, b = os.stat(path), os.stat(source)
    except (OSError, ValueError):
        return False
    return a.st_ino != 0 and os.path.samestat(a, b)


def _check_output(path, source):
    """!
    @brief Refuse an output path that refers to the input file.

    @param path Output path.
    @param source Path of the input file.
    @exception UsageError when path refers to source (see _same_file).
    """
    if _same_file(path, source):
        raise UsageError(
            'the output path %s refers to the input file %s; choose another one with -o'
            % (path, source)
        )


def _write_atomic(path, data):
    """!
    @brief Write a file through a temporary file in the same directory and os.replace.

    The temporary file name is a dot, the target name cut to 32 characters, a dot, 12
    random hex digits and .tmp. It is created exclusively with mode 0666 minus the
    umask; it takes the mode of an existing regular file at path. os.replace then swaps it in; a symbolic link at path is
    replaced by the file, its target is not written. When anything fails, the temporary
    file is removed and an existing file at path keeps its content.

    @param path Target path; its directory must exist.
    @param data File contents as bytes.
    @exception OSError when the file cannot be written or replaced.
    """
    folder, name = os.path.split(os.path.abspath(path))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
    for _ in range(100):
        tmp = os.path.join(folder, '.%s.%s.tmp' % (name[:32], os.urandom(6).hex()))
        try:
            fd = os.open(tmp, flags, 0o666)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError('cannot create a temporary file in %s' % folder)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        try:
            st = os.lstat(path)
        except OSError:
            st = None
        if st is not None and (st.st_mode & 0o170000) == 0o100000:  # regular file
            os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def convert(pdf_path, md_path, assets_dir, pages=None, page_markers=False, figures=True, log=None):
    """!
    @brief Write one Markdown file for the whole document.

    An output path that refers to the input file (same path, symbolic link or hard link)
    is refused before the input is read. Every file is written through a temporary file
    (see _write_atomic).

    @param pdf_path Path of the PDF file.
    @param md_path Path of the Markdown file to write (UTF-8, LF line endings).
    @param assets_dir Directory for figure files.
    @param pages Set of 1-based page numbers to convert; None for all.
    @param page_markers True to insert a <!-- page N --> comment before each page.
    @param figures False to skip figure export.
    @param log Function that receives progress messages, or None.
    @return Counter of exported figures per label.
    @exception UsageError when md_path or a figure file refers to the input file.
    """
    _check_output(md_path, pdf_path)
    doc, _, layouts = analyze(pdf_path, pages, figures, log)
    md_dir = os.path.dirname(os.path.abspath(md_path))
    os.makedirs(md_dir, exist_ok=True)
    exporter = Exporter(doc, assets_dir, md_dir)
    exporter.source = pdf_path
    writer = MarkdownWriter(_heading_levels(layouts))
    try:
        for lay in layouts:
            _CURRENT_PAGE[0] = lay.number
            _page_markdown(
                writer, exporter, lay, '<!-- page %d -->' % lay.number if page_markers else None
            )
    finally:
        _CURRENT_PAGE[0] = None
    _check_output(md_path, pdf_path)
    _write_atomic(md_path, writer.text().encode('utf-8'))
    return exporter.counts


# ---------------------------------------------------------------------------
# Printed table of contents
# ---------------------------------------------------------------------------

## @brief Matches a contents entry whose page number follows a leader.
##
## The leader is two or more dots (spaces allowed between them) or a run of ellipsis,
## underscore or middle dot characters. The page is 1 to 4 digits or a Roman numeral of
## up to 7 letters. Examples: "Title . . . . 12", "Title ......12", "Title ___ 12".
## Groups: title, page.
_TOC_LEADER_RE = re.compile(
    r'^(?P<title>.*?[^\s.])\s*(?:(?:\.\s*){2,}|[\u2026_\u00b7]+\s*)'
    r'(?P<page>\d{1,4}|[ivxlcdm]{1,7})$',
    re.I,
)
## @brief Matches "Title 12": text, whitespace, then a page number.
##
## The page is 1 to 4 digits or a Roman numeral of up to 7 letters.
##
## The gap before the number is checked on the characters (see _toc_entry).
## Groups: title, page.
_TOC_PLAIN_RE = re.compile(r'^(?P<title>.*\S)\s+(?P<page>\d{1,4}|[ivxlcdm]{1,7})$', re.I)
## @brief Matches a contents heading in English, German, French, Spanish, Italian, Dutch or
## Polish (case-insensitive).
_TOC_HEAD_RE = re.compile(
    r'^(?:table of contents|contents|inhalt|inhaltsverzeichnis|sommaire|'
    r'table des mati[eè]res|[íi]ndice|contenido|indice|inhoud|spis tre[sś]ci)$',
    re.I,
)
## @brief Matches the section number at the start of a contents entry.
##
## An optional word (chapter, part, appendix, section and German or French forms, any
## case) may come first. The number is dotted digits (1, 1.2.3), an upper-case letter
## followed by dotted digits (A.1), a single upper-case letter after the word, or an
## upper-case Roman numeral. Groups: word, num.
_TOC_NUM_RE = re.compile(
    r'^(?:(?P<word>chapter|kapitel|part|teil|appendix|anhang|annex|section|'
    r'abschnitt|chapitre|annexe)\s+)?(?P<num>\d+(?:\.\d+)*|(?-i:[A-Z])(?=\.\d)(?:\.\d+)*'
    r'|(?<=\s)(?-i:[A-Z])|(?-i:[IVX]+)(?=[.:\s]))\.?(?=[\s:]|$)',
    re.I,
)


## @brief A valid Roman numeral from 1 to 3999, any case.
_ROMAN_RE = re.compile(r'(?=[mdclxvi])m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})', re.I)


def _norm_title(t):
    """!
    @brief Normalize a title for matching: lowercase, without non-word characters and
    underscores, at most 40 characters.

    @param t Title text.
    @return Normalized key.
    """
    return re.sub(r'[\W_]+', '', t.lower())[:40]


def _toc_entry(ln):
    """!
    @brief Recognize a line as a contents entry.

    The title must contain a letter. A page number in letters must be a valid Roman
    numeral (_ROMAN_RE).

    The page number must follow a dot leader (_TOC_LEADER_RE), or follow the title
    after a horizontal gap of at least 1.5 em between the glyphs.

    @param ln Line to test.
    @return Tuple (title, printed page string), or None when the line is not an entry.
    """
    text = ln.text.strip()
    m = _TOC_LEADER_RE.match(text) or _TOC_PLAIN_RE.match(text)
    if not m or not re.search(r'[^\W\d_]', m.group('title')):
        return None  # the title needs at least one letter
    page = m.group('page')
    if not page.isdigit() and not _ROMAN_RE.fullmatch(page):
        return None  # "mild", "did": letters that are no valid Roman numeral
    if m.re is _TOC_LEADER_RE:
        return m.group('title').strip(), page
    ink = [c for c in ln.chars if c.text.strip()]
    digits = len(m.group('page'))
    if len(ink) <= digits:
        return None
    first, before = ink[-digits], ink[-digits - 1]
    if first.x - before.x2 < 1.5 * ln.size:
        return None
    return m.group('title').strip(), m.group('page')


def printed_toc(layouts, labels=None, log=None, page_count=None):
    """!
    @brief Read a printed table of contents from the first pages.

    The first max(20, min(60, page count // 10)) pages are scanned. A contents page has
    at least 4 entry lines (see _toc_entry) that make up at least 40 % of its lines; the
    first run of consecutive such pages forms the table. A line of at most 120
    characters without a page number, directly above an entry (within 1.8 em, size
    difference under 0.6), is joined to its title.
    Entry levels come from indentation when the entries use at least two indents
    (steps over 0.8 em), else from section numbers (1, 1.2, 1.2.3; Chapter, Appendix,
    Part start level 0).
    Entries whose page number is lower than an earlier one are dropped; when more than
    20 % are dropped, or fewer than 4 remain, there is no table.
    Printed page numbers map to PDF pages through the page labels when at least 80 % of
    the numeric entries match a label. Else an offset from -10 to 80 is chosen at which
    most entry titles are found within the first characters of a line on the target
    page; at least max(2, 25 % of the numeric entries) must match, otherwise there is
    no table.
    The contents pages themselves get a top-level entry named after the contents
    heading, inserted before the first entry that starts after them.

    @param layouts List of PageLayout objects.
    @param labels Page labels, one string per page, or None.
    @param log Function that receives progress messages, or None.
    @param page_count Number of pages in the document; targets beyond it are None.
            Defaults to the number of the last page in layouts.
    @return Tuple (outline as nested [title, page, children] lists, list of the contents
            page numbers); ([], []) when no table is found.
    """
    if not layouts:
        return [], []
    limit = max(20, min(60, len(layouts) // 10))
    scan = layouts[:limit]
    found = []
    heading = 'Contents'
    for lay in scan:
        entries, pending = [], None
        for ln in lay.lines:
            e = _toc_entry(ln)
            if e is None:
                if _TOC_HEAD_RE.match(ln.text.strip().rstrip(':')):
                    if not found:
                        heading = ln.text.strip().rstrip(':')
                    pending = None
                    continue
                pending = (ln, ln.text.strip()) if len(ln.text) <= 120 else None
                continue
            title, page = e
            if _TOC_HEAD_RE.match(title.rstrip(':')):
                pending = None
                continue  # running header such as "Table of contents 2"
            x0 = ln.ix0
            if (
                pending
                and abs(pending[0].size - ln.size) < 0.6
                and pending[0].y - ln.y <= 1.8 * ln.size
            ):
                title = pending[1] + ' ' + title
                x0 = min(x0, pending[0].ix0)
            pending = None
            entries.append((title, page, x0, ln.size))
        if len(entries) >= 4 and len(entries) >= 0.4 * len(lay.lines):
            found.append((lay.number, entries))
        elif found:
            break
    if not found:
        return [], []
    # keep the first run of consecutive contents pages
    run = [found[0]]
    for f in found[1:]:
        if f[0] != run[-1][0] + 1:
            break
        run.append(f)
    toc_pages = [n for n, _ in run]
    entries = [e for _, es in run for e in es]
    # page numbers must not go back: drop entries that do; reject the table when
    # more than 20 % of the entries do
    kept, last = [], 0
    for e in entries:
        if e[1].isdigit():
            if int(e[1]) < last:
                continue
            last = int(e[1])
        kept.append(e)
    if len(kept) < 4 or len(kept) < 0.8 * len(entries):
        return [], []
    entries = kept
    if log:
        log(
            'printed table of contents on page%s %s, %d entries'
            % ('s' if len(toc_pages) > 1 else '', _page_span(toc_pages), len(entries))
        )
    # levels
    em = sorted(e[3] for e in entries)[len(entries) // 2]
    indents = []
    for x in sorted(e[2] for e in entries):
        if not indents or x - indents[-1] > 0.8 * em:
            indents.append(x)
    levels = []
    for title, _, x0, _ in entries:
        if len(indents) >= 2:
            lvl = max(i for i, v in enumerate(indents) if v <= x0 + 0.8 * em)
        else:
            m = _TOC_NUM_RE.match(title)
            lvl = 0 if not m or m.group('word') else m.group('num').count('.')
        levels.append(lvl)
    # printed page -> PDF page
    last_toc = toc_pages[-1]
    by_label = {}
    if labels:
        for i, lab in enumerate(labels, 1):
            by_label.setdefault(lab.lower(), i)
    starts = {}
    for lay in layouts:
        if lay.number > last_toc:
            starts[lay.number] = [_norm_title(l.text) for l in lay.lines]
    numeric = [(i, int(e[1])) for i, e in enumerate(entries) if e[1].isdigit()][:60]
    offset, best = None, 0
    if not (
        labels and sum(1 for _, p in numeric if str(p) in by_label) >= 0.8 * max(1, len(numeric))
    ):
        for d in range(-10, 81):
            score = 0
            for i, p in numeric:
                key = _norm_title(entries[i][0])
                if len(key) >= 4 and any(key in t[: len(key) + 8] for t in starts.get(p + d, ())):
                    score += 1
            if score > best:
                offset, best = d, score
        if best < max(2, 0.25 * len(numeric)):
            return [], []  # entry titles not found on the pages: not a table of contents
        labels_ok = False
    else:
        labels_ok = True
    npages = page_count or layouts[-1].number

    def target(printed):
        """!
        @brief Map a printed page number to a PDF page number.

        Page labels are used when they matched; else a numeric page plus the offset.

        @param printed Printed page string.
        @return 1-based page number, or None when it cannot be mapped or lies outside
                1 to npages.
        """
        if labels_ok and printed.lower() in by_label:
            return by_label[printed.lower()]
        if offset is not None and printed.isdigit():
            n = int(printed) + offset
            return n if 1 <= n <= npages else None
        return None

    # tree
    root = []
    stack = [(-1, root)]
    for (title, printed, _, _), lvl in zip(entries, levels):
        while stack[-1][0] >= lvl:
            stack.pop()
        lvl = stack[-1][0] + 1
        node = [title, target(printed), []]
        stack[-1][1].append(node)
        stack.append((lvl, node[2]))
    # the contents pages themselves, as a top-level entry in page order
    at = next(
        (i for i, e in enumerate(root) if e[1] is not None and e[1] > toc_pages[0]), len(root)
    )
    root.insert(at, [heading, toc_pages[0], []])
    return root, toc_pages


def _page_span(nums):
    """!
    @brief Format a list of page numbers as "first-last", or a single number.

    @param nums Non-empty list of page numbers.
    @return Span string.
    """
    return '%d-%d' % (nums[0], nums[-1]) if len(nums) > 1 else str(nums[0])


def _safe_name(title, limit=48):
    """!
    @brief Build a folder name that is valid on Windows, macOS and Linux.

    The title is normalized to Unicode NFC. Control characters and < > : " / \\ | ? * become
    spaces, whitespace runs collapse to one space, the result is cut to limit
    characters, and trailing dots and spaces are removed.

    @param title Title text.
    @param limit Maximum length in characters.
    @return Folder name; 'untitled' when nothing remains.
    """
    t = unicodedata.normalize('NFC', title)
    t = re.sub(r'[\x00-\x1f<>:"/\\|?*]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > limit:
        t = t[:limit]
    return t.rstrip('. ') or 'untitled'


def _toc_folders(outline, depth):
    """!
    @brief Flatten the outline into (start page, folder parts, title, level) tuples.

    The tuples are in document order.

    Folders are named "NN Title" and nest up to depth levels. Entries deeper than depth
    use their parent's folder. Titles are shortened so that a page file path relative
    to the output directory stays within MAX_REL_PATH characters; a level with fewer
    than 8 characters left uses its parent's folder.

    @param outline Nested [title, page, children] lists.
    @param depth Number of outline levels that become folders.
    @return List of (page, list of folder names, title, level) tuples.
    """
    out = []

    def walk(entries, parts, level):
        """!
        @brief Append the entries of one outline level and their children to the result.

        The number prefix is zero-padded to at least 2 digits, or to the digit count of the
        number of siblings.

        @param entries List of [title, page, children] entries.
        @param parts Folder names of the parent.
        @param level Outline level of entries, 0 at the top.
        """
        width = max(2, len(str(len(entries))))
        used = len('/'.join(parts)) + (1 if parts else 0)
        for n, (title, page, kids) in enumerate(entries, 1):
            # keep "<folders>/page_NNNNNN.md" within MAX_REL_PATH characters
            room = MAX_REL_PATH - used - 16 - (width + 1)
            name = '%0*d %s' % (width, n, _safe_name(title, min(48, room)))
            here = parts + [name] if level < depth and room >= 8 else parts
            out.append((page, here, title, level))
            walk(kids, here, level + 1)

    walk(outline, [], 0)
    return out


def convert_split(
    pdf_path, out_dir, assets_dir=None, pages=None, figures=True, toc_depth=3, log=None
):
    """!
    @brief Write one Markdown file per page into out_dir, plus index.md with links.

    The outline comes from the PDF bookmarks, else from a printed table of contents
    (see printed_toc). With an outline and toc_depth > 0, each page file goes into the
    folder of the first outline entry that starts on that page, descending into children
    that start on the same page; a page where no entry starts goes into the folder of
    the entry still running. Pages before the first entry stay in out_dir. Page files
    are named page_NNNN.md, zero-padded to at least 4 digits. index.md lists the outline
    entries with links to their start pages, or all page files when there is no outline.

    An outline or page label tree that cannot be read is recorded with problem(). An
    output path (a page file, index.md or a figure file) that refers to the input file
    is refused; page files and index.md are checked before anything is written. Every
    file is written through a temporary file (see _write_atomic).

    @param pdf_path Path of the PDF file.
    @param out_dir Output directory.
    @param assets_dir Directory for figure files; None uses out_dir/_assets.
    @param pages Set of 1-based page numbers to convert; None for all.
    @param figures False to skip figure export.
    @param toc_depth Outline levels that become folders; 0 for no folders and no outline.
    @param log Function that receives progress messages, or None.
    @return Tuple (figure counts, number of page files, number of outline entries).
    @exception UsageError when an output file refers to the input file.
    """
    index_path = os.path.join(os.path.abspath(out_dir), 'index.md')
    _check_output(index_path, pdf_path)
    doc, page_list, layouts = analyze(pdf_path, pages, figures, log)
    numbers = {id(p): n for n, (p, _) in enumerate(page_list, 1)}
    source = None
    entries = []
    if toc_depth > 0:
        try:
            outline = doc.outline(numbers)
        except Exception as exc:  # malformed outline: continue without it
            outline = []
            problem('malformed', 'outline not readable (%s: %s)' % (type(exc).__name__, exc))
        if outline:
            source = 'PDF outline (bookmarks)'
        else:
            try:
                labels = doc.page_labels(len(page_list))
            except Exception as exc:
                labels = None
                problem(
                    'malformed', 'page labels not readable (%s: %s)' % (type(exc).__name__, exc)
                )
            outline, toc_pages = printed_toc(layouts, labels, log, len(page_list))
            if outline:
                source = 'printed table of contents on page %s' % _page_span(toc_pages)
        entries = _toc_folders(outline, toc_depth)
    on_page = {}
    for k, e in enumerate(entries):
        if e[0] is not None:
            on_page.setdefault(e[0], []).append(k)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    assets_dir = os.path.abspath(assets_dir or os.path.join(out_dir, '_assets'))
    exporter = Exporter(doc, assets_dir, out_dir)
    exporter.source = pdf_path
    levels = _heading_levels(layouts)
    width = max(4, len(str(len(page_list))))
    files = {}
    plan = []  # (layout, folder, page file path)
    running = None  # last outline entry started before the current page
    started = sorted(on_page)
    si = 0
    for lay in layouts:
        while si < len(started) and started[si] < lay.number:
            running = on_page[started[si]][-1]
            si += 1
        here = on_page.get(lay.number)
        if here:
            # the first section starting on this page, descending into children
            # that start on the same page
            k = here[0]
            for nxt in here[1:]:
                if nxt == k + 1 and entries[nxt][3] == entries[k][3] + 1:
                    k = nxt
                else:
                    break
        else:
            k = running
        parts = entries[k][1] if k is not None else []
        folder = os.path.join(out_dir, *parts)
        plan.append((lay, folder, os.path.join(folder, 'page_%0*d.md' % (width, lay.number))))
    for _, _, path in plan:
        _check_output(path, pdf_path)
    _check_output(index_path, pdf_path)
    try:
        for lay, folder, path in plan:
            _CURRENT_PAGE[0] = lay.number
            os.makedirs(folder, exist_ok=True)
            exporter.md_dir = folder
            writer = MarkdownWriter(levels)
            _page_markdown(writer, exporter, lay)
            _check_output(path, pdf_path)
            _write_atomic(path, writer.text().encode('utf-8'))
            files[lay.number] = path
    finally:
        _CURRENT_PAGE[0] = None
    rel = lambda p: _link(os.path.relpath(p, out_dir))
    title = os.path.splitext(os.path.basename(pdf_path))[0]
    lines = ['# ' + _esc(title), '']
    if source:
        lines += ['Contents from the %s.' % source, '']
    if entries:
        for page, _, name, level in entries:
            name = ''.join(ch if ch.isprintable() else ' ' for ch in name or '')
            name = re.sub(r'\s+', ' ', name).strip() or 'untitled'
            text = _esc(name, brackets=True)
            link = files.get(page)
            item = '[%s](%s)' % (text, rel(link)) if link else text
            lines.append('  ' * level + '- ' + item + (' (page %d)' % page if page else ''))
    else:
        for n in sorted(files):
            lines.append('- [Page %d](%s)' % (n, rel(files[n])))
    _check_output(index_path, pdf_path)
    _write_atomic(index_path, ('\n'.join(lines) + '\n').encode('utf-8'))
    return exporter.counts, len(files), len(entries)


class _ArgumentParser(argparse.ArgumentParser):
    """!
    @brief Argument parser that exits with code 1 on usage errors (argparse uses 2,
    which pdf2md reserves for files that cannot be parsed).
    """

    def error(self, message):
        """!
        @brief Print the usage and the error, then exit with code 1.
        @param message error text from argparse
        """
        self.print_usage(sys.stderr)
        sys.stderr.write('pdf2md: error: %s\n' % message)
        sys.exit(1)


def main(argv=None):
    """!
    @brief Command-line entry point.

    Without --split-pages the output defaults to the input name with .md and the assets
    to the output name with _assets instead of .md; --flat and --toc-depth are rejected.
    With --split-pages, --page-markers is rejected. With --split-pages the
    output directory defaults to the input name without extension. Progress goes to
    stderr unless -q is given.

    An output path equal to the input file is rejected here; convert() and
    convert_split() also refuse output files that are symbolic or hard links to it.
    Unexpected errors while reading the file (for example MemoryError) are reported in
    one line with exit code 2, without a traceback, also when part of the output was
    already written. Recorded problems are printed on stderr, also with -q.

    @param argv Argument list; None uses sys.argv.
    @return Exit status: 0 on success, 1 for usage and file errors, 2 for files that
            cannot be parsed and other conversion errors, 3 when the output was written
            but PROBLEMS is not empty.
    """
    ap = _ArgumentParser(description='Convert a text-based PDF file to Markdown.')
    ap.add_argument('pdf', help='input PDF file')
    ap.add_argument(
        '-o',
        '--output',
        help='output Markdown file, or output directory with --split-pages '
        '(default: input name with .md, or input name without extension)',
    )
    ap.add_argument(
        '--assets',
        help='directory for figure files (default: <output stem>_assets, '
        'or <output directory>/_assets with --split-pages)',
    )
    ap.add_argument(
        '--split-pages',
        action='store_true',
        help='write one Markdown file per page, in folders that follow the PDF outline, plus index.md',
    )
    ap.add_argument(
        '--toc-depth',
        type=int,
        metavar='N',
        help='with --split-pages: outline levels that become folders (default 3, 0 = no folders)',
    )
    ap.add_argument(
        '--flat', action='store_true', help='with --split-pages: no folders, same as --toc-depth 0'
    )
    ap.add_argument('--pages', help='page selection, 1-based, e.g. 1-3,7')
    ap.add_argument('--page-markers', action='store_true', help='insert <!-- page N --> comments')
    ap.add_argument(
        '--no-figures', action='store_true', help='skip table, diagram and image export'
    )
    ap.add_argument('-q', '--quiet', action='store_true', help='no progress output on stderr')
    args = ap.parse_args(argv)

    if not args.split_pages and (args.flat or args.toc_depth is not None):
        print('pdf2md: --flat and --toc-depth need --split-pages', file=sys.stderr)
        return 1
    if args.split_pages and args.page_markers:
        print('pdf2md: --page-markers does not apply to --split-pages', file=sys.stderr)
        return 1
    if args.split_pages:
        out = args.output or os.path.splitext(args.pdf)[0]
        assets = args.assets
    else:
        out = args.output or os.path.splitext(args.pdf)[0] + '.md'
        assets = args.assets or os.path.splitext(out)[0] + '_assets'
    if args.toc_depth is None:
        args.toc_depth = 3
    if args.toc_depth < 0:
        print('pdf2md: --toc-depth must be 0 or more', file=sys.stderr)
        return 1
    if os.path.abspath(out) == os.path.abspath(args.pdf):
        print('pdf2md: the output path equals the input file; choose one with -o', file=sys.stderr)
        return 1
    try:
        pages = _parse_pages(args.pages) if args.pages else None
    except ValueError:
        print('pdf2md: invalid --pages value: %s' % args.pages, file=sys.stderr)
        return 1
    log = None if args.quiet else (lambda m: print('pdf2md: ' + m, file=sys.stderr))
    try:
        if args.split_pages:
            counts, nfiles, nentries = convert_split(
                args.pdf,
                out,
                assets,
                pages,
                not args.no_figures,
                0 if args.flat else args.toc_depth,
                log,
            )
        else:
            counts = convert(
                args.pdf, out, assets, pages, args.page_markers, not args.no_figures, log
            )
    except UsageError as exc:
        print('pdf2md: %s' % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print('pdf2md: %s' % exc, file=sys.stderr)
        return 1
    except PDFError as exc:
        print('pdf2md: %s: %s' % (args.pdf, exc), file=sys.stderr)
        return 2
    except (Exception, MemoryError) as exc:  # no traceback for the user
        print(
            'pdf2md: %s: conversion failed (%s: %s)' % (args.pdf, type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2
    if log:
        summary = (
            ', '.join('%d %s' % (v, k.lower()) for k, v in sorted(counts.items())) or 'no figures'
        )
        if args.split_pages:
            log(
                'wrote %d page files to %s (%d outline entries, %s)'
                % (nfiles, out, nentries, summary)
            )
        else:
            log('wrote %s (%s)' % (out, summary))
    if PROBLEMS:
        for line in problem_lines():
            print('pdf2md: %s' % line, file=sys.stderr)
        print('pdf2md: the output is incomplete', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
