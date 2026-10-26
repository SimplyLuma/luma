#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run native statistics regressions against a patched libcamera source tree.

Pass a configured source tree from the isolated runtime builder. No camera,
installed library, device state, or distribution package is modified.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source_tree', type=Path)
parser.add_argument('--build-directory', type=Path)
args = parser.parse_args()
source = args.source_tree.resolve()
build = args.build_directory or source / 'output'
statistics = (source / 'src/libcamera/software_isp/swstats_cpu.cpp').read_text()
start = statistics.index('static constexpr unsigned int kAwbSaturationThreshold')
end = statistics.index('void SwStatsCpu::statsBGGR8Line0')
with tempfile.TemporaryDirectory(prefix='luma-camera-stats-', dir=os.environ.get('DEV_ARTIFACT_DIR')) as directory:
    output = Path(directory)
    (output / 'statistics-macros.h').write_text(statistics[start:end])
    start = statistics.index('void SwStatsCpu::finishFrame(')
    end = statistics.index('\n/**', start)
    finish = statistics[start:end].replace('void SwStatsCpu::finishFrame(', 'void Sampler::finishFrame(')
    (output / 'finish-statistics.h').write_text(finish)
    subprocess.run(['g++', '-DLIBCAMERA_BASE_PRIVATE', '-std=c++20', '-O2',
                    '-I' + str(output), '-I' + str(source / 'include'),
                    '-I' + str(build / 'include'),
                    str(Path(__file__).with_name('unclipped-stats.cpp')),
                    '-o', str(output / 'statistics-test')], check=True)
    subprocess.run([str(output / 'statistics-test')], check=True)
print('PASS unclipped AWB sums, saturation boundaries, packed scales and unchanged metering')
