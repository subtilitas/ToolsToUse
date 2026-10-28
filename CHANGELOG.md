# Changelog

Repository-level changes: folder layout, scripts, workflows.
Each tool keeps its own changelog in `tools/<name>/CHANGELOG.md`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Folder layout with one folder per tool in `tools/`.
- Tool `pdf2md`.
- `scripts/coverage_readme.py`: writes the coverage table in `README.md`; `--check` fails on drift.
- `scripts/build_wiki.py`: builds the GitHub wiki pages from the Markdown files.
- `scripts/release_notes.py`: checks a release tag and prints its changelog section.
- Workflows `CI` (lint, tests, coverage check), `Docs` (Doxygen, wiki) and `Release` (on tags `<name>-v<version>`).
