#!/usr/bin/env python3
"""!
@file coverage_readme.py
@brief Measure test coverage of every tool and write the table in README.md.

A tool is a folder tools/<name>/ with the script <name>.py and the test file
test_<name>.py. The tests run under coverage.py with branch coverage. Child
processes started with subprocess are measured as well (coverage.py 7.10 or
later, option patch = subprocess). The table between the markers
<!-- coverage:start --> and <!-- coverage:end --> in README.md lists one row per
tool with the combined line and branch coverage of <name>.py, rounded to whole
percent.

@code{.sh}
python scripts/coverage_readme.py            # measure and rewrite README.md
python scripts/coverage_readme.py --check    # measure, exit 1 if README.md differs
python scripts/coverage_readme.py pdf2md     # measure one tool, keep the other rows
@endcode

@par Exit codes
- 0: README.md is current (--check) or was written.
- 1: README.md differs from the measurement (--check), a marker is missing, a
  named tool does not exist, or a test run failed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

## repository root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
## README.md that holds the table
README = os.path.join(ROOT, 'README.md')
## folder that holds one subfolder per tool
TOOLS = os.path.join(ROOT, 'tools')
## first line of the generated block
START = '<!-- coverage:start -->'
## last line of the generated block
END = '<!-- coverage:end -->'
## pattern of one table row: tool name and percentage
_ROW_RE = re.compile(r'^\| \[(?P<name>[^\]]+)\]\([^)]*\) \| (?P<pct>\d+) % \|$')


def tools():
    """!
    @brief List the tool folders that have a script and a test file.
    @return sorted tool names
    """
    names = []
    for name in sorted(os.listdir(TOOLS)):
        d = os.path.join(TOOLS, name)
        if os.path.isfile(os.path.join(d, name + '.py')) and os.path.isfile(
            os.path.join(d, 'test_%s.py' % name)
        ):
            names.append(name)
    return names


def measure(name):
    """!
    @brief Run the tests of one tool under coverage.py.
    @param name tool name
    @return combined line and branch coverage of tools/<name>/<name>.py in percent
    @throw RuntimeError when the tests fail
    """
    d = os.path.join(TOOLS, name)
    with tempfile.TemporaryDirectory(prefix='cov_%s_' % name) as tmp:
        rc = os.path.join(tmp, 'coveragerc')
        with open(rc, 'w', encoding='utf-8') as fh:
            fh.write(
                '[run]\nbranch = true\nparallel = true\npatch = subprocess\n'
                'data_file = %s\n' % os.path.join(tmp, '.coverage').replace('\\', '/')
            )
        run = [sys.executable, '-m', 'coverage']
        r = subprocess.run(
            run + ['run', '--rcfile=' + rc, '-m', 'unittest', '-q', 'test_' + name], cwd=d
        )
        if r.returncode:
            raise RuntimeError('tests of %s failed (exit code %d)' % (name, r.returncode))
        subprocess.run(run + ['combine', '--rcfile=' + rc, '-q'], cwd=d, check=True)
        out = os.path.join(tmp, 'coverage.json')
        subprocess.run(
            run + ['json', '--rcfile=' + rc, '-q', '-o', out, '--include=*%s.py' % name],
            cwd=d,
            check=True,
        )
        with open(out, encoding='utf-8') as fh:
            data = json.load(fh)
    target = os.path.normcase(os.path.join(d, name + '.py'))
    for path, entry in data['files'].items():
        if os.path.normcase(os.path.join(d, path)) == target:
            return entry['summary']['percent_covered']
    raise RuntimeError('no coverage data for %s.py' % name)


def table(rows):
    """!
    @brief Format the generated block.
    @param rows {tool name: percent}
    @return lines from START to END
    """
    lines = [START, '| Tool | Coverage |', '| --- | ---: |']
    for name in sorted(rows):
        lines.append('| [%s](tools/%s/README.md) | %d %% |' % (name, name, rows[name]))
    return lines + [END]


def main(argv=None):
    """!
    @brief Command-line entry point.
    @param argv argument list; None uses sys.argv
    @return exit status
    """
    ap = argparse.ArgumentParser(description='Write tool coverage into README.md.')
    ap.add_argument('names', nargs='*', help='tools to measure (default: all)')
    ap.add_argument('--check', action='store_true', help='exit 1 if README.md would change')
    args = ap.parse_args(argv)

    with open(README, encoding='utf-8') as fh:
        text = fh.read()
    lines = text.split('\n')
    if START not in lines or END not in lines:
        print('coverage: README.md lacks %s or %s' % (START, END), file=sys.stderr)
        return 1
    a, b = lines.index(START), lines.index(END)
    rows = {}
    for ln in lines[a + 1 : b]:
        m = _ROW_RE.match(ln)
        if m:
            rows[m.group('name')] = int(m.group('pct'))
    known = tools()
    rows = {k: v for k, v in rows.items() if k in known}
    for name in args.names or known:
        if name not in known:
            print('coverage: no tool %s with %s.py and test_%s.py' % ((name,) * 3), file=sys.stderr)
            return 1
        try:
            rows[name] = int(measure(name) + 0.5)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print('coverage: %s' % exc, file=sys.stderr)
            return 1
    new = '\n'.join(lines[:a] + table(rows) + lines[b + 1 :])
    for name in sorted(rows):
        print('coverage: %s %d %%' % (name, rows[name]))
    if new == text:
        return 0
    if args.check:
        print(
            'coverage: README.md is out of date; run python scripts/coverage_readme.py',
            file=sys.stderr,
        )
        return 1
    with open(README, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(new)
    print('coverage: wrote README.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
