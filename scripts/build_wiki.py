#!/usr/bin/env python3
"""!
@file build_wiki.py
@brief Build the GitHub wiki pages from the Markdown files of the repository.

Page sources:
- README.md becomes Home.md.
- docs/<Page>.md becomes <Page>.md.
- tools/<name>/README.md becomes <name>.md.
- tools/<name>/CHANGELOG.md becomes <name>-Changelog.md.
- _Sidebar.md is generated and lists every page.

Relative links between these files are rewritten to wiki page names. Relative
links to any other file point to that file in the repository on GitHub. Each
page ends with a line that names its source file.

The output directory is emptied first, except for the folder .git, so a clone
of the wiki repository can be used as the output directory.

@code{.sh}
python scripts/build_wiki.py build/wiki
python scripts/build_wiki.py build/wiki --blob-url https://github.com/owner/repo/blob/main
@endcode

Without --blob-url the URL is taken from the environment variables
GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_REF_NAME that GitHub Actions sets.
"""

import argparse
import os
import posixpath
import re
import shutil
import sys

## repository root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
## pattern of an inline Markdown link or image target
_LINK_RE = re.compile(r'(\]\()([^)\s]+)(\))')


def sources():
    """!
    @brief Map repository paths of page sources to wiki page names.
    @return {repository path with / separators: page name}, in sidebar order
    """
    pages = {'README.md': 'Home'}
    docs = os.path.join(ROOT, 'docs')
    if os.path.isdir(docs):
        for f in sorted(os.listdir(docs)):
            if f.endswith('.md'):
                pages['docs/' + f] = f[:-3]
    tools = os.path.join(ROOT, 'tools')
    for name in sorted(os.listdir(tools)):
        if os.path.isfile(os.path.join(tools, name, 'README.md')):
            pages['tools/%s/README.md' % name] = name
        if os.path.isfile(os.path.join(tools, name, 'CHANGELOG.md')):
            pages['tools/%s/CHANGELOG.md' % name] = name + '-Changelog'
    return pages


def rewrite(text, src, pages, blob):
    """!
    @brief Rewrite relative links of one page.
    @param text Markdown text
    @param src repository path of the page source
    @param pages {repository path: page name}
    @param blob URL prefix of repository files
    @return Markdown text with wiki links
    """

    def fix(m):
        target = m.group(2)
        if re.match(r'[a-z][a-z0-9+.-]*:|#', target, re.I):
            return m.group(0)
        path, _, anchor = target.partition('#')
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(src), path))
        if rel in pages:
            new = pages[rel]
        else:
            new = '%s/%s' % (blob, rel)
        return m.group(1) + new + ('#' + anchor if anchor else '') + m.group(3)

    return _LINK_RE.sub(fix, text)


def main(argv=None):
    """!
    @brief Command-line entry point.
    @param argv argument list; None uses sys.argv
    @return exit status
    """
    ap = argparse.ArgumentParser(description='Build GitHub wiki pages.')
    ap.add_argument('out', help='output directory')
    ap.add_argument('--blob-url', help='URL prefix of repository files')
    args = ap.parse_args(argv)
    blob = args.blob_url
    if not blob:
        env = os.environ
        if not env.get('GITHUB_REPOSITORY'):
            print('build_wiki: give --blob-url outside GitHub Actions', file=sys.stderr)
            return 1
        blob = '%s/%s/blob/%s' % (
            env.get('GITHUB_SERVER_URL', 'https://github.com'),
            env['GITHUB_REPOSITORY'],
            env.get('GITHUB_REF_NAME', 'main'),
        )
    blob = blob.rstrip('/')

    os.makedirs(args.out, exist_ok=True)
    for f in os.listdir(args.out):
        p = os.path.join(args.out, f)
        if f == '.git':
            continue
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)

    pages = sources()
    for src, page in pages.items():
        with open(os.path.join(ROOT, src), encoding='utf-8') as fh:
            text = rewrite(fh.read(), src, pages, blob)
        text = text.rstrip('\n') + '\n\n---\nSource: [%s](%s/%s)\n' % (src, blob, src)
        with open(os.path.join(args.out, page + '.md'), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)

    side = ['**[Home](Home)**', '']
    docs = [p for s, p in pages.items() if s.startswith('docs/')]
    if docs:
        side += ['**Repository**', ''] + ['- [%s](%s)' % (p.replace('-', ' '), p) for p in docs]
        side.append('')
    side += ['**Tools**', '']
    for src, page in pages.items():
        if src.startswith('tools/') and src.endswith('/README.md'):
            side.append('- [%s](%s)' % (page, page))
            if page + '-Changelog' in pages.values():
                side.append('  - [Changelog](%s-Changelog)' % page)
    with open(os.path.join(args.out, '_Sidebar.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(side) + '\n')
    print('build_wiki: wrote %d pages to %s' % (len(pages), args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
