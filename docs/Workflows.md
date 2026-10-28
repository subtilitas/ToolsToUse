# Workflows

Three GitHub Actions workflows live in `.github/workflows/`.
All run on Node.js 24 actions: `actions/checkout@v6`, `actions/setup-python@v6`, `actions/upload-artifact@v7`.

## CI (`ci.yml`)

Runs on every push and pull request.

| Job | Runner | Python | Steps |
| --- | --- | --- | --- |
| lint | ubuntu-latest | 3.13 | `black --check .`, `ruff check .` |
| test | ubuntu-22.04 | 3.8 | unittest suite of every tool, with pandoc |
| test | ubuntu-latest | 3.13 | unittest suite of every tool, with pandoc |
| test | windows-latest | 3.13 | unittest suite of every tool, without pandoc |
| coverage | ubuntu-latest | 3.13 | `python scripts/coverage_readme.py --check` |

The coverage job fails when the table in `README.md` differs from the measurement.
The fix is to run `python scripts/coverage_readme.py` and commit `README.md`.
Results can differ by 1 % between Python versions and platforms.
The CI measurement on Python 3.13 with pandoc is the reference.

## Docs (`docs.yml`)

Runs on pushes to `main` and pull requests that touch `README.md`, `docs/`, `tools/` or the workflow, and on manual dispatch.

1. Builds the Doxygen HTML of every tool. Fails on a missing `Doxyfile` or a non-empty `doxygen_warnings.txt`.
   The HTML is uploaded as the artifact `doxygen-html`.
2. Builds the wiki pages with `scripts/build_wiki.py`.
3. On `main` only: pushes the pages to the repository wiki with the workflow token.

Wiki pages:

| Source | Wiki page |
| --- | --- |
| `README.md` | Home |
| `docs/<Page>.md` | `<Page>` |
| `tools/<name>/README.md` | `<name>` |
| `tools/<name>/CHANGELOG.md` | `<name>-Changelog` |

The wiki is generated. Edits made in the wiki are overwritten by the next run.

Current limitation: GitHub creates the wiki repository only after the first page is saved in the web interface.
Until then the push step fails with "Repository not found".
Saving one placeholder page under Wiki in the repository fixes it once.

## Release (`release.yml`)

Runs on tags of the form `<name>-v<major>.<minor>.<patch>`, for example `pdf2md-v1.0.0`.

1. `scripts/release_notes.py` checks the tag and reads the section `## [<version>]` from `tools/<name>/CHANGELOG.md`.
   A tag without that section fails.
2. Runs the tests of the tool.
3. Builds the Doxygen HTML. Fails on warnings.
4. Publishes a GitHub release with these files:

| File | Content |
| --- | --- |
| `<name>.py` | The tool. |
| `<name>-<version>.zip` | The tool, `README.md`, `CHANGELOG.md`, `LICENSE`. |
| `<name>-<version>-docs.zip` | Doxygen HTML. |

To release:

```sh
# in tools/<name>/CHANGELOG.md: rename "## [Unreleased]" to "## [1.0.0] - YYYY-MM-DD",
# add a new empty "## [Unreleased]" above it, commit
git tag pdf2md-v1.0.0
git push origin pdf2md-v1.0.0
```
