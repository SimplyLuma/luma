#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute Blueprint's production concurrency prelude and its negative control.

Both sources are compiled with the pinned Android Go compiler. The upstream
source stays untouched; scratch contains only the copied policy source/test.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def prelude(source):
    start = source.index('func RunBlueprint(')
    start = source.index('\n', start)
    end = source.index('\n\tif args.NoGC', start)
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('blueprint', type=Path)
    parser.add_argument('go', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    original = (args.blueprint / 'bootstrap/command.go').read_text()
    with tempfile.TemporaryDirectory(prefix='luma-blueprint-budget-') as directory:
        work = Path(directory)
        (work / 'bootstrap').mkdir()
        source = work / 'bootstrap/command.go'
        source.write_text(original)
        subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + str(work),
                        str(root / 'patches/android-blueprint/0001-honor-host-go-concurrency.patch')],
                       cwd=work, check=True)
        env = dict(os.environ, GO111MODULE='off', GOENV='off', CGO_ENABLED='0',
                   GOMAXPROCS='2', GOCACHE=str(work / 'cache'), GOPATH=str(work / 'gopath'),
                   GOPROXY='off', GOSUMDB='off')
        for label, contents, expected in (('patched', source.read_text(), 0),
                                          ('unpatched', original, 42)):
            unit = work / (label + '.go')
            unit.write_text('package main\nimport("os";"runtime")\nfunc configure(){' +
                            prelude(contents) + '\n}\n' + r'''
func main() {
    if runtime.NumCPU() < 3 { os.Exit(77) }
    os.Setenv("GOMAXPROCS", "2")
    runtime.GOMAXPROCS(2)
    configure()
    if runtime.GOMAXPROCS(0) != 2 { os.Exit(42) }
    os.Unsetenv("GOMAXPROCS")
    runtime.GOMAXPROCS(1)
    configure()
    if runtime.GOMAXPROCS(0) != runtime.NumCPU() { os.Exit(43) }
}
''')
            executable = work / label
            subprocess.run([str(args.go.resolve()), 'build', '-o', str(executable), str(unit)],
                           env=env, cwd=work, check=True, timeout=120)
            result = subprocess.run([str(executable)], env=env, timeout=15)
            if result.returncode != expected:
                raise SystemExit(f'{label}: expected {expected}, got {result.returncode}')
        print('PASS: actual Blueprint concurrency prelude honors explicit budget and default; upstream control rejected')


if __name__ == '__main__':
    main()
