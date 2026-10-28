# ToolsToUse

A collection of standalone command-line tools.
Each tool is one Python file that runs with the Python standard library only.
Each tool lives in its own folder under `tools/` with its tests, documentation and changelog.

## Tools

| Tool | Does | Python |
| --- | --- | --- |
| [pdf2md](tools/pdf2md/README.md) | Converts text-based PDF files to Markdown, as one file or one file per page. | 3.8 or later |

## Test coverage

Combined line and branch coverage of each tool script, measured by CI on Python 3.13 with pandoc installed.
Child processes started by the tests are included.

<!-- coverage:start -->
| Tool | Coverage |
| --- | ---: |
| [pdf2md](tools/pdf2md/README.md) | 58 % |
<!-- coverage:end -->

## Layout

```text
.
├── tools/
│   └── <name>/
│       ├── <name>.py          tool, single file, standard library only
│       ├── test_<name>.py     unittest suite
│       ├── Doxyfile           Doxygen configuration
│       ├── README.md          user documentation, also the wiki page <name>
│       └── CHANGELOG.md       version history, source of the release notes
├── docs/                      repository-wide wiki pages
├── scripts/                   repository maintenance scripts
├── .github/workflows/         CI, Docs, Release
├── pyproject.toml             black and ruff configuration
├── requirements-dev.txt       pinned development tools
└── CHANGELOG.md               repository-level changes
```

A tool folder is self-contained.
Copying it elsewhere keeps the tool, its tests and its documentation working.

## Development

```sh
pip install -r requirements-dev.txt
black --check .
ruff check .
(cd tools/pdf2md && python -m unittest -v test_pdf2md)
python scripts/coverage_readme.py            # rewrite the coverage table
python scripts/coverage_readme.py --check    # exit 1 if the table is out of date
```

[Adding a tool](docs/Adding-a-Tool.md) lists the files a new tool needs.
[Workflows](docs/Workflows.md) describes CI, documentation and releases.

## License

MIT. See [LICENSE](LICENSE).
