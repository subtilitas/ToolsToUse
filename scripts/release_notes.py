#!/usr/bin/env python3
"""!
@file release_notes.py
@brief Check a release tag and print its release notes from the tool changelog.

A release tag has the form <name>-v<major>.<minor>.<patch>, for example
pdf2md-v1.0.0. The tool folder tools/<name>/ must exist, and
tools/<name>/CHANGELOG.md must have a section that starts with
"## [<major>.<minor>.<patch>]". The body of that section is printed to stdout.
The body ends at the next "## " heading or at the first link reference
definition ("[1.0.0]: https://..."), whichever comes first. Lines inside fenced
code blocks never end it.

@code{.sh}
python scripts/release_notes.py pdf2md-v1.0.0
@endcode

@par Exit codes
- 0: notes printed.
- 1: malformed tag, unknown tool, or no changelog section for the version.
"""

import os
import re
import sys

## repository root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
## pattern of a link reference definition, which ends a section body
_LINKREF_RE = re.compile(r'^ {0,3}\[[^\]]+\]:\s')
## pattern of a fence line that opens or closes a code block
_FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
## pattern of a release tag
TAG_RE = re.compile(r'^(?P<name>[a-z0-9][a-z0-9_]*)-v(?P<version>\d+\.\d+\.\d+)$')


def notes(tag):
    """!
    @brief Return tool name and release notes for a tag.
    @param tag release tag
    @return (tool name, notes text)
    @throw ValueError with a message when the tag or changelog does not fit
    """
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError('tag %s does not match <name>-v<major>.<minor>.<patch>' % tag)
    name, version = m.group('name'), m.group('version')
    log = os.path.join(ROOT, 'tools', name, 'CHANGELOG.md')
    if not os.path.isfile(log):
        raise ValueError('no tools/%s/CHANGELOG.md' % name)
    with open(log, encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    head = '## [%s]' % version
    for i, ln in enumerate(lines):
        if ln.startswith(head):
            body = []
            fence = None
            for nxt in lines[i + 1 :]:
                f = _FENCE_RE.match(nxt)
                if fence:
                    if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
                        fence = None
                elif f:
                    fence = f.group(1)
                elif nxt.startswith('## ') or _LINKREF_RE.match(nxt):
                    break
                body.append(nxt)
            return name, '\n'.join(body).strip() + '\n'
    raise ValueError('tools/%s/CHANGELOG.md has no section %s' % (name, head))


def main(argv=None):
    """!
    @brief Command-line entry point.
    @param argv argument list; None uses sys.argv
    @return exit status
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: release_notes.py <name>-v<major>.<minor>.<patch>', file=sys.stderr)
        return 1
    try:
        _, text = notes(argv[0])
    except ValueError as exc:
        print('release_notes: %s' % exc, file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
