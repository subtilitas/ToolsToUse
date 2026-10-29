#!/usr/bin/env python3
"""!
@file test_pdf2md.py
@brief Regression tests for pdf2md.py. Python standard library only, no network.

Each test builds the PDF it needs in a temporary directory. Tests that render
Markdown with pandoc are skipped when pandoc is not installed.

@code{.sh}
python test_pdf2md.py            # pdf2md.py next to this file
python test_pdf2md.py -v
PDF2MD=/path/to/pdf2md.py python test_pdf2md.py
@endcode
"""

import contextlib
import errno
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
import xml.etree.ElementTree as ET
import zlib

## directory of this file
HERE = os.path.dirname(os.path.abspath(__file__))
## pdf2md.py under test; the environment variable PDF2MD overrides it
SCRIPT = os.environ.get('PDF2MD', os.path.join(HERE, 'pdf2md.py'))
## import specification of the module under test
_spec = importlib.util.spec_from_file_location('pdf2md', SCRIPT)
## the module under test
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

## content stream that shows 'Hello world' in font F1
HELLO = b'BT /F1 12 Tf 72 700 Td (Hello world) Tj ET\n'


def make_pdf(
    path, content=HELLO, objs=(), resources=b'', page=b'', catalog=b'', media=b'0 0 612 792'
):
    """!
    @brief Write a one-page PDF with a Helvetica font F1 (object 5).
    @param path output file
    @param content page content stream
    @param objs further objects, numbered from 6
    @param resources extra entries for the page resource dictionary
    @param page extra entries for the page dictionary
    @param catalog extra entries for the catalog
    @param media MediaBox numbers
    """
    body = [
        b'<< /Type /Catalog /Pages 2 0 R ' + catalog + b' >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [' + media + b'] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> ' + resources + b' >> ' + page + b' >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ] + list(objs)
    out = b'%PDF-1.4\n'
    offs = []
    for i, o in enumerate(body):
        offs.append(len(out))
        out += b'%d 0 obj\n' % (i + 1) + o + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(body) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offs)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(body) + 1, x)
    with open(path, 'wb') as fh:
        fh.write(out)


def stream(data, extra=b''):
    """!
    @brief Return an uncompressed stream object body.
    @param data stream data
    @param extra extra dictionary entries
    @return object bytes
    """
    return b'<< /Length %d ' % len(data) + extra + b' >>\nstream\n' + data + b'\nendstream'


def _read(path):
    """!
    @brief Read a UTF-8 text file.
    @param path file path
    @return file content
    """
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _readb(path):
    """!
    @brief Read a binary file.
    @param path file path
    @return file content
    """
    with open(path, 'rb') as fh:
        return fh.read()


def _write(path, data):
    """!
    @brief Write bytes to a file.
    @param path file path
    @param data bytes to write
    """
    with open(path, 'wb') as fh:
        fh.write(data)


class Base(unittest.TestCase):
    """!
    @brief Temporary directory and conversion helpers.
    """

    ## temporary directory of the running test
    dir = None

    def setUp(self):
        """!
        @brief Create an empty temporary directory for the test.
        """
        self.dir = tempfile.mkdtemp(prefix='pdf2md_test_')

    def tearDown(self):
        """!
        @brief Remove the temporary directory.
        """
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        """!
        @brief Return a path inside the temporary directory.
        @param name file name
        @return absolute path
        """
        return os.path.join(self.dir, name)

    def cli(self, *args, timeout=60):
        """!
        @brief Run pdf2md.py as a subprocess and fail on any traceback.
        @param args command-line arguments
        @param timeout seconds before the run fails
        @return (exit code, stdout, stderr)
        """
        r = subprocess.run(
            [sys.executable, '-I', SCRIPT] + list(args),
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        self.assertNotIn('Traceback', r.stderr)
        return r.returncode, r.stdout, r.stderr

    def md(self, pdf, *args, timeout=60):
        """!
        @brief Convert pdf to Markdown and return (exit code, Markdown text).
        @param pdf PDF file
        @param args further command-line arguments
        @param timeout seconds before the run fails
        @return (exit code, Markdown text, empty when no file was written)
        """
        out = self.path('out.md')
        if os.path.exists(out):
            os.remove(out)
        rc, _, err = self.cli('-q', pdf, '-o', out, *args, timeout=timeout)
        text = _read(out) if os.path.exists(out) else ''
        return rc, text


class Parsing(Base):
    """!
    @brief Hostile or malformed file structure.
    """

    def test_deep_nesting_keeps_page_text(self):
        """!
        @brief Test: deep nesting keeps page text.
        """
        f = self.path('a.pdf')
        make_pdf(f, HELLO + b'[' * 100000 + b'] Tj\n')
        rc, text = self.md(f)
        self.assertEqual(rc, 0)
        self.assertIn('Hello world', text)

    def test_huge_cmap_and_width_ranges_are_bounded(self):
        """!
        @brief Test: huge cmap and width ranges are bounded.
        """
        ranges = b''.join(b'<%04X0000> <%04XFFFF> <0041>\n' % (k, k) for k in range(300))
        cmap = b'begincmap 1 begincodespacerange <00000000> <FFFFFFFF> endcodespacerange\n'
        cmap += b'300 beginbfrange\n' + ranges + b'endbfrange endcmap'
        w = b' '.join(b'%d %d 500' % (k * 65536, k * 65536 + 65535) for k in range(300))
        f = self.path('a.pdf')
        make_pdf(
            f,
            b'BT /F2 12 Tf 72 700 Td <0041> Tj ET',
            [
                b'<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H '
                b'/DescendantFonts [8 0 R] /ToUnicode 7 0 R >>',
                stream(cmap),
                b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /X /W [' + w + b'] >>',
            ],
            resources=b'/XObject << >>',
        )
        data = _readb(f).replace(b'/Font << /F1 5 0 R >>', b'/Font << /F1 5 0 R /F2 6 0 R >>')
        _write(f, data)
        t = time.time()
        rc, _ = self.md(f)
        self.assertEqual(rc, 0)
        self.assertLess(time.time() - t, 20)

    def test_form_fan_out_is_bounded(self):
        """!
        @brief Test: form fan out is bounded.
        """
        objs = []
        for k in range(8):  # form k calls form k+1 ten times: 10^8 runs without a budget
            nxt = b'/X%d Do ' % (k + 1) * 10 if k < 7 else b'BT /F1 9 Tf 1 0 0 1 72 72 Tm (z) Tj ET'
            res = b'/Resources << /XObject << /X%d %d 0 R >> /Font << /F1 5 0 R >> >>' % (
                k + 1,
                7 + k,
            )
            objs.append(stream(nxt, b'/Type /XObject /Subtype /Form /BBox [0 0 612 792] ' + res))
        f = self.path('a.pdf')
        make_pdf(f, HELLO + b'/X0 Do', objs, resources=b'/XObject << /X0 6 0 R >>')
        t = time.time()
        rc, text = self.md(f, timeout=120)
        self.assertEqual(rc, 0)
        self.assertIn('Hello world', text)
        self.assertLess(time.time() - t, 60)

    def test_object_stream_count_is_bounded(self):
        """!
        @brief Test: object stream count is bounded.
        """
        f = self.path('a.pdf')
        make_pdf(f, HELLO, [stream(b'1 0 2 5 ', b'/Type /ObjStm /N 300000000 /First 8')])
        t = time.time()
        rc, text = self.md(f)
        self.assertEqual(rc, 0)
        self.assertLess(time.time() - t, 10)

    def test_flate_output_is_capped(self):
        """!
        @brief Test: flate output is capped.
        """
        old = P.MAX_DECODED
        P.MAX_DECODED = 1 << 20
        try:
            self.assertEqual(len(P._flate(zlib.compress(b'\0' * (10 << 20)))), 1 << 20)
        finally:
            P.MAX_DECODED = old

    def test_predictor_with_absurd_columns_returns_data(self):
        """!
        @brief Test: predictor with absurd columns returns data.
        """
        self.assertEqual(P._predict(b'\0abc', {'Predictor': 12, 'Columns': 3 * 10**8}), b'\0abc')

    def test_kids_not_an_array(self):
        """!
        @brief Test: kids not an array.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        data = _readb(f).replace(b'/Kids [3 0 R]', b'/Kids 5')
        _write(f, data)
        rc, _ = self.md(f)
        self.assertIn(rc, (0, 2))

    def test_named_destination_with_array_key(self):
        """!
        @brief Test: named destination with array key.
        """
        f = self.path('a.pdf')
        make_pdf(
            f,
            HELLO,
            [
                b'<< /Dests << /Names [[1] [3 0 R /Fit] (ok) [3 0 R /Fit]] >> >>',
                b'<< /First 8 0 R >>',
                b'<< /Title (T) /Dest (ok) >>',
            ],
            catalog=b'/Names 6 0 R /Outlines 7 0 R',
        )
        rc, _, _ = self.cli('-q', '--split-pages', f, '-o', self.path('o'))
        self.assertEqual(rc, 0)

    def test_page_labels_with_huge_start(self):
        """!
        @brief Test: page labels with huge start.
        """
        f = self.path('a.pdf')
        make_pdf(
            f,
            HELLO,
            [b'<< /Nums [0 << /S /A /St 100000000000000 >>] >>'],
            catalog=b'/PageLabels 6 0 R',
        )
        doc = P.Document(_readb(f))
        t = time.time()
        labels = doc.page_labels(1)
        self.assertLess(time.time() - t, 1)
        self.assertLess(len(labels[0]), 20)

    def test_identity_h_ignores_tounicode_code_space(self):
        """!
        @brief Test: identity h ignores tounicode code space.
        """
        cmap = b'begincmap 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <0041> <0058> endbfchar endcmap'
        f = self.path('a.pdf')
        make_pdf(
            f,
            b'BT /F2 12 Tf 72 700 Td <0041> Tj ET',
            [
                b'<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H '
                b'/DescendantFonts [8 0 R] /ToUnicode 7 0 R >>',
                stream(cmap),
                b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /X >>',
            ],
        )
        data = _readb(f).replace(b'/Font << /F1 5 0 R >>', b'/Font << /F1 5 0 R /F2 6 0 R >>')
        _write(f, data)
        rc, text = self.md(f)
        self.assertEqual(text.strip(), 'X')

    def test_separation_full_tint_is_dark(self):
        """!
        @brief Test: separation full tint is dark.
        """
        self.assertEqual(P._color([1.0], ('sep', 1)), (0.0, 0.0, 0.0))
        self.assertEqual(P._color([0.0], ('sep', 1)), (1.0, 1.0, 1.0))

    def test_annotation_state_array(self):
        """!
        @brief Test: annotation state array.
        """
        f = self.path('a.pdf')
        make_pdf(
            f,
            HELLO,
            [
                stream(
                    b'BT /F1 9 Tf 0 0 Td (field) Tj ET',
                    b'/Type /XObject /Subtype /Form /BBox [0 0 50 20] /Resources << /Font << /F1 5 0 R >> >>',
                )
            ],
            page=b'/Annots [<< /Subtype /Widget /Rect [72 600 122 620] /AS [/On] /AP << /N << /On 6 0 R >> >> >>]',
        )
        rc, text = self.md(f)
        self.assertIn('Hello world', text)

    def test_font_from_extgstate(self):
        """!
        @brief Test: font from extgstate.
        """
        f = self.path('a.pdf')
        make_pdf(
            f,
            b'/GS1 gs BT 72 700 Td (Via gs) Tj ET',
            [b'<< /Type /ExtGState /Font [5 0 R 12] >>'],
            resources=b'/ExtGState << /GS1 6 0 R >>',
        )
        rc, text = self.md(f)
        self.assertIn('Via gs', text)


class Layout(Base):
    """!
    @brief Degenerate geometry and image data.
    """

    def test_far_off_page_text(self):
        """!
        @brief Test: far off page text.
        """
        f = self.path('a.pdf')
        lines = b''.join(
            b'BT /F1 10 Tf 72 %d Td (line %d of text) Tj ET\n' % (700 - 14 * i, i)
            for i in range(10)
        )
        make_pdf(f, lines + b'BT /F1 10 Tf 1000000000 100 Td (X) Tj ET')
        t = time.time()
        rc, text = self.md(f)
        self.assertEqual(rc, 0)
        self.assertIn('line 9 of text', text)
        self.assertLess(time.time() - t, 10)

    def test_infinite_coordinate_keeps_page(self):
        """!
        @brief Test: infinite coordinate keeps page.
        """
        f = self.path('a.pdf')
        make_pdf(f, HELLO + b'BT /F1 10 Tf 1' + b'0' * 400 + b'.0 100 Td (X) Tj ET')
        rc, text = self.md(f)
        self.assertIn('Hello world', text)

    def test_declared_image_size_far_above_data(self):
        """!
        @brief Test: declared image size far above data.
        """
        f = self.path('a.pdf')
        img = zlib.compress(b'\0' * 100)
        make_pdf(
            f,
            b'q 200 0 0 200 100 100 cm /Im1 Do Q',
            [
                stream(
                    img,
                    b'/Type /XObject /Subtype /Image /Width 20000 /Height 20000 /ColorSpace /DeviceRGB '
                    b'/BitsPerComponent 8 /Filter /FlateDecode',
                )
            ],
            resources=b'/XObject << /Im1 6 0 R >>',
        )
        doc = P.Document(_readb(f))
        self.assertIsNone(P.image_file(doc, doc.objects[6], {}))

    def test_indexed_palette_fits_bit_depth(self):
        """!
        @brief Test: indexed palette fits bit depth.
        """
        lookup = bytes(range(256)) * 3
        doc = P.Document(b'%PDF-1.4\n')
        s = P.Stream(
            {
                'Width': 8,
                'Height': 1,
                'BitsPerComponent': 1,
                'ColorSpace': ['Indexed', 'DeviceRGB', 255, lookup],
            },
            b'\xaa',
        )
        ext, png = P.image_file(doc, s, {})
        i = png.index(b'PLTE')
        self.assertEqual(int.from_bytes(png[i - 4 : i], 'big'), 2 * 3)

    def test_cluster_is_fast(self):
        """!
        @brief Test: cluster is fast.
        """
        items = [
            ((i % 100 * 8, i // 100 * 8, i % 100 * 8 + 1, i // 100 * 8 + 1), i) for i in range(8000)
        ]
        t = time.time()
        self.assertEqual(len(P.cluster(items, 6.0)), 8000)
        self.assertLess(time.time() - t, 2)

    def test_cluster_merges_through_grown_box(self):
        """!
        @brief Test: cluster merges through grown box.
        """
        items = [((0, 0, 10, 10), 'a'), ((100, 0, 110, 10), 'b'), ((5, 5, 105, 8), 'c')]
        self.assertEqual(len(P.cluster(items, 1.0)), 1)

    def test_svg_drops_characters_xml_forbids(self):
        """!
        @brief Test: svg drops characters xml forbids.
        """
        self.assertEqual(P._xml('a￾b￿c\x01d'), 'abcd')
        ET.fromstring('<t>%s</t>' % P._xml('x￿<&>'))


class Markdown(Base):
    """!
    @brief Markdown escaping, checked as text and, with pandoc, as rendered HTML.
    """

    def writer(self, *texts, mono=False, levels=None):
        """!
        @brief Feed one line per text through a MarkdownWriter, 40 points apart.
        @param texts line texts
        @param mono True for monospaced lines
        @param levels heading levels by font size
        @return Markdown text
        """
        w = P.MarkdownWriter(levels or {})
        y = 700.0
        for t in texts:
            cs = []
            x = 72.0
            for ch in t:
                c = P.Char()
                c.x, c.y, c.x2, c.size, c.text = x, y, x + 5, 10.0, ch
                c.bold = c.italic = False
                c.mono, c.serif, c.color, c.upright = mono, False, (0, 0, 0), True
                c.u, c.v, c.invisible, c.est = (1.0, 0.0), (0.0, 1.0), False, False
                cs.append(c)
                x += 5
            ln = P.make_line(cs)
            w.add_line(ln)
            y -= 40  # separate paragraphs
        return w.text()

    def html(self, md):
        """!
        @brief Render Markdown with pandoc (CommonMark); skip the test without pandoc.
        @param md Markdown text
        @return HTML
        """
        if not shutil.which('pandoc'):
            self.skipTest('pandoc not installed')
        return subprocess.run(
            ['pandoc', '-f', 'commonmark', '-t', 'html'],
            input=md,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout

    def test_block_syntax_at_paragraph_start(self):
        """!
        @brief Test: block syntax at paragraph start.
        """
        for t in ('~~~~~~~~', '---', '[note]: http://x.example', '# x', '> q', '+ y', '= z'):
            h = self.html(self.writer(t))
            self.assertTrue(h.strip().startswith('<p>'), (t, h))

    def test_inline_link_entity_and_processing_instruction(self):
        """!
        @brief Test: inline link entity and processing instruction.
        """
        h = self.html(self.writer('see [here](javascript:alert(1)) and &copy; and <?php x ?>'))
        self.assertNotIn('<a ', h)
        self.assertIn('&amp;copy;', h)
        self.assertIn('&lt;?php', h)

    def test_code_fence_longer_than_content(self):
        """!
        @brief Test: code fence longer than content.
        """
        md = self.writer('x = 1', '```', '# not code', mono=True)
        h = self.html(md)
        self.assertNotIn('<h1', h)

    def test_heading_keeps_trailing_hash(self):
        """!
        @brief Test: heading keeps trailing hash.
        """
        md = '# ' + P._esc('Version #')
        md = P.re.sub(r'(#+)$', lambda m: '\\#' * len(m.group(1)), md)
        self.assertIn('Version #', self.html(md))

    def test_list_item_body_is_escaped(self):
        """!
        @brief Test: list item body is escaped.
        """
        h = self.html(self.writer('• # of items'))
        self.assertIn('<li>', h)
        self.assertNotIn('<h1', h)

    def test_emphasis_next_to_punctuation(self):
        """!
        @brief Test: emphasis next to punctuation.
        """
        md = P._emphasis([('Note:', True, False), ('Do this', False, False)])
        self.assertIn(
            '<strong>Note:</strong>',
            self.html(md).replace('<b>', '<strong>').replace('</b>', '</strong>'),
        )

    def test_underscores(self):
        """!
        @brief Test: underscores.
        """
        self.assertEqual(P._esc('__init__'), '\\_\\_init\\_\\_')
        self.assertEqual(P._esc('snake_case'), 'snake_case')


class CommandLine(Base):
    """!
    @brief Options, exit codes, output paths and split mode.
    """

    def test_huge_page_range(self):
        """!
        @brief Test: huge page range.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        rc, text = self.md(f, '--pages', '1-999999999999')
        self.assertEqual(rc, 0)
        self.assertIn('Hello', text)

    def test_invalid_page_selections(self):
        """!
        @brief Test: invalid page selections.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        for sel in ('5-2', '0', '1_0', '２', ',', 'a'):
            rc, _, _ = self.cli('-q', f, '-o', self.path('o.md'), '--pages', sel)
            self.assertEqual(rc, 1, sel)

    def test_selection_outside_document(self):
        """!
        @brief Test: selection outside document.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        rc, _, err = self.cli('-q', '--split-pages', f, '-o', self.path('o'), '--pages', '50')
        self.assertEqual(rc, 1)
        self.assertIn('no selected page', err)

    def test_usage_errors_exit_1(self):
        """!
        @brief Test: usage errors exit 1.
        """
        rc, _, _ = self.cli('--no-such-option')
        self.assertEqual(rc, 1)
        rc, _, _ = self.cli('--toc-depth', 'x', 'a.pdf')
        self.assertEqual(rc, 1)

    def test_split_options_need_split_pages(self):
        """!
        @brief Test: --toc-depth and --flat without --split-pages exit 1, also at the default depth.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        for opts in (['--toc-depth', '3'], ['--toc-depth', '0'], ['--flat']):
            rc, _, err = self.cli('-q', f, '-o', self.path('o.md'), *opts)
            self.assertEqual(rc, 1, opts)
            self.assertIn('need --split-pages', err)

    def test_output_equal_to_input(self):
        """!
        @brief Test: output equal to input.
        """
        f = self.path('noext')
        make_pdf(f)
        rc, _, err = self.cli('-q', '--split-pages', f)
        self.assertEqual(rc, 1)

    def test_missing_output_folder_is_created(self):
        """!
        @brief Test: missing output folder is created.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        rc, _, _ = self.cli('-q', f, '-o', self.path('new/dir/o.md'))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.path('new/dir/o.md')))

    def test_zero_area_cropbox_keeps_text(self):
        """!
        @brief Test: zero area cropbox keeps text.
        """
        f = self.path('a.pdf')
        make_pdf(f, page=b'/CropBox [0 0 0 0]')
        rc, text = self.md(f)
        self.assertIn('Hello world', text)

    def test_other_drive_assets_become_file_uri(self):
        """!
        @brief Test: other drive assets become file uri.
        """
        ex = P.Exporter(None, self.dir, self.dir)
        real = P.os.path.relpath
        P.os.path.relpath = lambda *a: (_ for _ in ()).throw(ValueError('different drive'))
        try:
            self.assertTrue(ex._rel(self.path('x.png')).startswith('file:'))
        finally:
            P.os.path.relpath = real

    def outline_pdf(self, titles, dests=None):
        """!
        @brief Write a one-page PDF with a flat outline.
        @param titles PDF string objects for the titles, e.g. b"(Intro)"
        @param dests destination objects, default [3 0 R /Fit] for each
        @return path of the PDF
        """
        n = len(titles)
        items = []
        for i, t in enumerate(titles):
            d = dests[i] if dests else b'[3 0 R /Fit]'
            nxt = b' /Next %d 0 R' % (8 + i) if i + 1 < n else b''
            items.append(b'<< /Title ' + t + b' /Dest ' + d + nxt + b' >>')
        f = self.path('o.pdf')
        make_pdf(f, HELLO, [b'<< /First 7 0 R >>'] + items, catalog=b'/Outlines 6 0 R')
        return f

    def test_index_link_text_is_escaped(self):
        """!
        @brief Test: index link text is escaped.
        """
        f = self.outline_pdf([b'(Intro] [x)', b'(A\\nB\\n\\n# C)', b'(R&D)'])
        rc, _, _ = self.cli('-q', '--split-pages', f, '-o', self.path('o'))
        index = _read(self.path('o/index.md'))
        self.assertIn('Intro\\] \\[x', index)
        self.assertNotIn('\n# C', index)
        self.assertEqual(P._link('01 R&D/a b.md'), '01%20R%26D/a%20b.md')

    def test_destination_outside_document(self):
        """!
        @brief Test: destination outside document.
        """
        f = self.outline_pdf(
            [b'(Chapter)', b'(Bogus)', b'(Far)'], [b'[3 0 R /Fit]', b'[-7 /Fit]', b'[99 /Fit]']
        )
        doc = P.Document(_readb(f))
        pages = doc.pages()
        outline = doc.outline({id(p): n for n, (p, _) in enumerate(pages, 1)})
        self.assertEqual([e[1] for e in outline], [1, None, None])

    def test_path_length_limit(self):
        """!
        @brief Test: path length limit.
        """
        long = b'(' + b'x' * 200 + b')'
        f = self.path('d.pdf')
        # five nested levels of 200-character titles
        objs = [b'<< /First 7 0 R >>']
        for k in range(5):
            first = b' /First %d 0 R' % (8 + k) if k < 4 else b''
            objs.append(b'<< /Title ' + long + b' /Dest [3 0 R /Fit]' + first + b' >>')
        make_pdf(f, HELLO, objs, catalog=b'/Outlines 6 0 R')
        rc, _, _ = self.cli('-q', '--split-pages', '--toc-depth', '5', f, '-o', self.path('o'))
        self.assertEqual(rc, 0)
        longest = max(
            len(os.path.relpath(os.path.join(d, x), self.path('o')))
            for d, _, fs in os.walk(self.path('o'))
            for x in fs
        )
        self.assertLessEqual(longest, P.MAX_REL_PATH)

    def test_no_network_or_subprocess(self):
        """!
        @brief Test: no network or subprocess.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        hook = (
            'import sys, runpy, os\n'
            'def h(e, a):\n'
            '    if e.startswith(("socket.", "urllib.", "http.", "subprocess.", "os.system", "os.exec")):\n'
            '        os._exit(99)\n'
            'sys.addaudithook(h)\n'
            'sys.argv = sys.argv[1:]\n'
            'runpy.run_path(sys.argv[0], run_name="__main__")\n'
        )
        hook_file = self.path('hook.py')
        _write(hook_file, hook.encode())
        r = subprocess.run(
            [
                sys.executable,
                '-I',
                hook_file,
                SCRIPT,
                '-q',
                '--split-pages',
                f,
                '-o',
                self.path('o'),
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr)


class Contents(Base):
    """!
    @brief Printed table of contents detection.
    """

    def toc_pdf(self, toc_lines, pages):
        """!
        @brief Write a PDF: one contents page, then one page per (heading, body) pair.
        @param toc_lines (indent level, title, printed page) for each contents line
        @param pages (heading, body text) for each following page
        @return path of the PDF
        """
        n = 1 + len(pages)
        kids = b' '.join(b'%d 0 R' % (3 + 2 * i) for i in range(n))
        objs = [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [' + kids + b'] /Count %d >>' % n,
        ]
        contents = [
            b'BT /F1 16 Tf 72 740 Td (Contents) Tj ET\n'
            + b''.join(
                b'BT /F1 11 Tf %d %d Td (%s) Tj ET BT /F1 11 Tf 520 %d Td (%s) Tj ET\n'
                % (72 + 18 * lvl, 700 - 18 * i, t, 700 - 18 * i, p)
                for i, (lvl, t, p) in enumerate(toc_lines)
            )
        ]
        for head, body in pages:
            contents.append(
                b'BT /F1 16 Tf 72 740 Td (%s) Tj ET BT /F1 11 Tf 72 700 Td (%s) Tj ET'
                % (head, body)
            )
        font = 3 + 2 * n
        for i, c in enumerate(contents):
            objs.append(
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R '
                b'/Resources << /Font << /F1 %d 0 R >> >> >>' % (4 + 2 * i, font)
            )
            objs.append(stream(c))
        objs.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
        out = b'%PDF-1.4\n'
        offs = []
        for i, o in enumerate(objs):
            offs.append(len(out))
            out += b'%d 0 obj\n' % (i + 1) + o + b'\nendobj\n'
        x = len(out)
        out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(
            b'%010d 00000 n \n' % o for o in offs
        )
        out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, x)
        f = self.path('toc.pdf')
        _write(f, out)
        return f

    def folders(self, f):
        """!
        @brief Convert f with --split-pages and list the page files.
        @param f PDF file
        @return sorted relative paths of page_NNNN.md files
        """
        rc, _, _ = self.cli('-q', '--split-pages', f, '-o', self.path('o'))
        self.assertEqual(rc, 0)
        return sorted(
            os.path.relpath(os.path.join(d, x), self.path('o')).replace(os.sep, '/')
            for d, _, fs in os.walk(self.path('o'))
            for x in fs
            if x.startswith('page_')
        )

    def test_indented_contents_with_offset(self):
        """!
        @brief Test: indented contents with offset.
        """
        f = self.toc_pdf(
            [
                (0, b'Getting Started', b'1'),
                (1, b'Installing Tools', b'1'),
                (0, b'Working Files', b'2'),
                (1, b'Opening Files', b'2'),
                (0, b'Reference Part', b'3'),
                (0, b'Index Pages', b'4'),
            ],
            [
                (b'Getting Started', b'Installing Tools text'),
                (b'Working Files', b'Opening Files'),
                (b'Reference Part', b'body'),
                (b'Index Pages', b'body'),
            ],
        )
        got = self.folders(f)
        self.assertIn('02 Getting Started/01 Installing Tools/page_0002.md', got)
        self.assertIn('03 Working Files/01 Opening Files/page_0003.md', got)

    def test_number_table_is_not_contents(self):
        """!
        @brief Test: number table is not contents.
        """
        f = self.toc_pdf(
            [
                (0, b'2000', b'7'),
                (0, b'2001', b'107'),
                (0, b'2002', b'17'),
                (0, b'2003', b'5'),
                (0, b'2004', b'9'),
            ],
            [(b'Annual figures', b'x')] * 3,
        )
        self.assertEqual(
            self.folders(f), ['page_0001.md', 'page_0002.md', 'page_0003.md', 'page_0004.md']
        )

    def test_words_are_not_roman_page_numbers(self):
        """!
        @brief Test: words are not roman page numbers.
        """
        f = self.toc_pdf(
            [
                (0, b'Soup of the day', b'mild'),
                (0, b'Main course', b'civil'),
                (0, b'Dessert plate', b'did'),
                (0, b'Coffee and tea', b'lid'),
            ],
            [(b'Menu', b'x')] * 2,
        )
        self.assertEqual(self.folders(f), ['page_0001.md', 'page_0002.md', 'page_0003.md'])


class OutputSafety(Base):
    """!
    @brief Output files never overwrite the input; writes are atomic; problems give exit 3.
    """

    def link(self, kind, src, dst):
        """!
        @brief Create a symbolic or hard link, or skip the test where that is not possible.
        @param kind 'sym' or 'hard'
        @param src existing file
        @param dst link path
        """
        try:
            if kind == 'sym':
                os.symlink(src, dst)
            else:
                os.link(src, dst)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest('cannot create a %s link: %s' % (kind, exc))

    def image_pdf(self):
        """!
        @brief Write a one-page PDF with text and a 2x2 gray image.
        @return path of the PDF
        """
        f = self.path('a.pdf')
        make_pdf(
            f,
            HELLO + b'q 200 0 0 200 100 100 cm /Im1 Do Q\n',
            [
                stream(
                    b'\x00\xff\xff\x00',
                    b'/Type /XObject /Subtype /Image /Width 2 /Height 2 '
                    b'/ColorSpace /DeviceGray /BitsPerComponent 8',
                )
            ],
            resources=b'/XObject << /Im1 6 0 R >>',
        )
        return f

    def run_main(self, *args):
        """!
        @brief Run P.main in this process.
        @param args command-line arguments
        @return (exit code, stderr text)
        """
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = P.main(list(args))
        return rc, err.getvalue()

    def test_symlink_output_to_input_is_refused(self):
        """!
        @brief Test: an output file that is a symbolic link to the input is refused.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        before = _readb(f)
        out = self.path('out.md')
        self.link('sym', f, out)
        rc, _, err = self.cli('-q', f, '-o', out)
        self.assertEqual(rc, 1)
        self.assertIn('refers to the input file', err)
        self.assertEqual(_readb(f), before)

    def test_hard_link_output_to_input_is_refused(self):
        """!
        @brief Test: an output file that is a hard link to the input is refused.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        before = _readb(f)
        out = self.path('out.md')
        self.link('hard', f, out)
        rc, _, err = self.cli('-q', f, '-o', out)
        self.assertEqual(rc, 1)
        self.assertIn('refers to the input file', err)
        self.assertEqual(_readb(f), before)

    def test_convert_refuses_alias_of_input(self):
        """!
        @brief Test: convert() called directly with an alias of the input raises UsageError.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        before = _readb(f)
        out = self.path('out.md')
        self.link('hard', f, out)
        with self.assertRaises(P.UsageError):
            P.convert(f, out, self.path('assets'))
        with self.assertRaises(P.UsageError):
            P.convert(f, os.path.join(self.path('x'), '..', 'a.pdf'), self.path('assets'))
        self.assertEqual(_readb(f), before)

    def test_split_page_and_asset_aliases_keep_input(self):
        """!
        @brief Test: with --split-pages, page and figure files that link to the input do
        not change it.
        """
        f = self.image_pdf()
        before = _readb(f)
        o = self.path('o')
        rc, _, _ = self.cli('-q', '--split-pages', f, '-o', o)
        self.assertEqual(rc, 0)
        assets = os.listdir(os.path.join(o, '_assets'))
        self.assertEqual(len(assets), 1)
        # a page file that links to the input: refused before anything is written
        shutil.rmtree(o)
        os.makedirs(o)
        self.link('sym', f, os.path.join(o, 'page_0001.md'))
        rc, _, err = self.cli('-q', '--split-pages', f, '-o', o)
        self.assertEqual(rc, 1)
        self.assertIn('refers to the input file', err)
        self.assertFalse(os.path.exists(os.path.join(o, 'index.md')))
        self.assertEqual(_readb(f), before)
        # a figure file that links to the input
        shutil.rmtree(o)
        os.makedirs(os.path.join(o, '_assets'))
        self.link('sym', f, os.path.join(o, '_assets', assets[0]))
        rc, _, err = self.cli('-q', '--split-pages', f, '-o', o)
        self.assertEqual(rc, 1)
        self.assertIn('refers to the input file', err)
        self.assertEqual(_readb(f), before)
        # index.md as a hard link to the input
        shutil.rmtree(o)
        os.makedirs(o)
        self.link('hard', f, os.path.join(o, 'index.md'))
        with self.assertRaises(P.UsageError):
            P.convert_split(f, o)
        self.assertEqual(_readb(f), before)

    def test_failed_write_keeps_old_file(self):
        """!
        @brief Test: a write that fails half-way keeps the old output and leaves no
        temporary file.
        """
        f = self.image_pdf()
        out = self.path('out.md')
        _write(out, b'old content\n')
        real = os.fdopen

        class Full:
            """!
            @brief File wrapper that writes half the data, then fails like a full disk.
            """

            def __init__(self, fh):
                """!
                @brief Wrap an open file.
                @param fh file object
                """
                ## the wrapped file
                self.fh = fh

            def __enter__(self):
                """!
                @brief Enter the context.
                @return self
                """
                return self

            def __exit__(self, *exc):
                """!
                @brief Close the wrapped file.
                @param exc exception information
                """
                self.fh.close()

            def write(self, data):
                """!
                @brief Write half the data, then raise ENOSPC.
                @param data bytes to write
                """
                self.fh.write(data[: len(data) // 2])
                raise OSError(errno.ENOSPC, 'No space left on device')

        with mock.patch.object(P.os, 'fdopen', lambda *a, **k: Full(real(*a, **k))):
            rc, err = self.run_main('-q', f, '-o', out)
        self.assertEqual(rc, 1)
        self.assertIn('No space left', err)
        self.assertEqual(_readb(out), b'old content\n')
        left = [n for _, _, fs in os.walk(self.path('')) for n in fs if n.endswith('.tmp')]
        self.assertEqual(left, [])
        # a normal run replaces the file
        rc, err = self.run_main('-q', f, '-o', out)
        self.assertEqual(rc, 0, err)
        self.assertIn('Hello world', _read(out))

    def test_page_error_exits_3_with_output(self):
        """!
        @brief Test: a page that fails in layout gives exit 3, the Markdown file, and the
        problem on stderr, also with -q and with --split-pages.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        out = self.path('out.md')

        def boom(*a, **k):
            """!
            @brief Fail like a layout bug.
            @param a ignored
            @param k ignored
            """
            raise ValueError('broken layout')

        with mock.patch.object(P, 'layout_page', boom):
            rc, err = self.run_main('-q', f, '-o', out)
            self.assertEqual(rc, 3)
            self.assertTrue(os.path.exists(out))
            self.assertIn('page 1:', err)
            self.assertIn('broken layout', err)
            rc, err = self.run_main('-q', '--split-pages', f, '-o', self.path('o'))
            self.assertEqual(rc, 3)
            self.assertTrue(os.path.exists(self.path('o/index.md')))
            self.assertIn('page 1:', err)
        # the next run starts with no problems
        rc, err = self.run_main('-q', f, '-o', out)
        self.assertEqual((rc, err), (0, ''))

    def test_failure_after_partial_output_exits_2(self):
        """!
        @brief Test: an exception after some files were written exits with code 2.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        real = P._write_atomic

        def fail_index(path, data):
            """!
            @brief Write page files, fail on index.md.
            @param path target path
            @param data file contents
            """
            if path.endswith('index.md'):
                raise RuntimeError('index failed')
            real(path, data)

        with mock.patch.object(P, '_write_atomic', fail_index):
            rc, err = self.run_main('-q', '--split-pages', f, '-o', self.path('o'))
        self.assertEqual(rc, 2)
        self.assertIn('index failed', err)
        self.assertTrue(os.path.exists(self.path('o/page_0001.md')))

    def test_clean_file_exits_0_silently(self):
        """!
        @brief Test: a clean file exits 0 with empty stderr under -q.
        """
        f = self.path('a.pdf')
        make_pdf(f)
        rc, _, err = self.cli('-q', f, '-o', self.path('out.md'))
        self.assertEqual((rc, err), (0, ''))
        rc, _, err = self.cli('-q', '--split-pages', f, '-o', self.path('o'))
        self.assertEqual((rc, err), (0, ''))


if __name__ == '__main__':
    unittest.main()
