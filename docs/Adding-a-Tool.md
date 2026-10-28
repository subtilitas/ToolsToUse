# Adding a tool

A tool named `<name>` (lower case letters, digits and `_`) needs these files in `tools/<name>/`:

| File | Content | Checked by |
| --- | --- | --- |
| `<name>.py` | The tool. One file, Python standard library only, `main(argv=None)` that returns the exit status. | CI lint (black, ruff) |
| `test_<name>.py` | unittest suite. Runs from the tool folder with `python -m unittest test_<name>`. | CI test, CI coverage |
| `Doxyfile` | Doxygen configuration. `OUTPUT_DIRECTORY = doxygen`, `WARN_LOGFILE = doxygen_warnings.txt`. | Docs |
| `README.md` | What the tool does, constraints, usage, options, exit codes, limitations. | Docs (wiki page `<name>`) |
| `CHANGELOG.md` | Keep a Changelog format with a `## [Unreleased]` section. | Release |

`tools/pdf2md/` is the reference layout.

## Steps

1. Create the folder and the five files.
2. Add a row to the tool table in `README.md`.
3. Add the coverage row:

   ```sh
   python scripts/coverage_readme.py <name>
   ```

4. Add an entry under `## [Unreleased]` in the root `CHANGELOG.md`.
5. Run the checks:

   ```sh
   black --check .
   ruff check .
   (cd tools/<name> && python -m unittest -v test_<name>)
   (cd tools/<name> && doxygen Doxyfile && test ! -s doxygen_warnings.txt)
   python scripts/coverage_readme.py --check
   ```

## Conventions

- Formatting: black with line length 100 and single quotes kept (`pyproject.toml`).
- Lint: ruff rules F and E9.
- Doc comments: Doxygen `"""!` blocks with `@brief`, `@param`, `@return`.
- Tests build their input files at run time. No binary fixtures in the repository.
- Tests start the tool as a subprocess with `sys.executable -I`. Coverage of those runs is measured.
- `README.md` of a tool is the user documentation. The module docstring is the reference for the source.
  A change to one is copied into the other in the same commit.
