# Changelog of pdf2md

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A release is tagged `pdf2md-v<version>`.

## [Unreleased]

### Added

- `pdf2md.py`: PDF to Markdown converter, single file, Python standard library only.
- `test_pdf2md.py`: 83 regression tests.
- `Doxyfile`: Doxygen configuration for the API documentation.
- Exit code 3 with one stderr line per problem (page, kind, cause), also with `-q`, when the output is incomplete.
- Cross-reference chain reading from `startxref` (tables, streams, `/XRefStm`, `/Prev`) with generation numbers and free entries; file scan as fallback.
- Code spaces of predefined composite-font CMaps (RKSJ, EUC, GBK, GBK2K, Big Five, UHC, UCS2, UTF16, UTF32, one-byte).
- SVG export with the placement matrix for rotated, mirrored or sheared raster images.
- Per-component image `/Decode` for gray, RGB, CMYK, Lab and Indexed `[2^bits-1 0]`.
- Limits `MAX_DOC_DECODED`, `MAX_LZW_TABLE`, `MAX_IMAGE_SIDE`, `MAX_LAB_MEMO`, `MAX_PAGE_CHARS`, `MAX_GRID_CELLS`, `MAX_FONT_TEXT`, `MAX_CMAP_DEST`, `MAX_SHOW_CODES`, `MAX_PROBLEMS`.

### Fixed

- Output paths that are the input file or a symbolic or hard link to it are rejected, also in `convert()` and `convert_split()`. Output files are written atomically.
- Figures are dropped as repeats only inside the top or bottom 10 % band and only with identical content. Different full-page scans are kept.
- Shapes with very large boxes no longer fill the clustering grid.
- `MAX_DECODED` is enforced after every filter stage and inside every expanding decoder.
- "trailer <<" in page text or stream data is not taken for the file trailer.
- `--toc-depth` without `--split-pages` exits with code 1 at any value.
