#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute the prepared Mutter scale fallback with its real function body.

Only its external DPI/global-setting providers are stubbed. Saved display
configurations never use this fallback; explicit global overrides and the
handheld DPI path must keep their existing behavior.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1]).read_text()
    start = source.index("float\nmeta_monitor_calculate_mode_scale (")
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    function = source[start:end]
    harness = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef void MetaMonitor;
typedef void MetaMonitorMode;
typedef void MetaBackend;
typedef void MetaSettings;
typedef int MetaMonitorScalesConstraint;
static bool explicit_scale;
static int configured_scale, dpi_calls;
static MetaBackend *meta_monitor_get_backend(MetaMonitor *m) { return m; }
static MetaSettings *meta_backend_get_settings(MetaBackend *b) { return b; }
static bool meta_settings_get_global_scaling_factor(MetaSettings *s, int *scale)
{ (void)s; *scale = configured_scale; return explicit_scale; }
static const char *g_getenv(const char *key) { return getenv(key); }
static int g_strcmp0(const char *a, const char *b)
{ return a && b ? strcmp(a,b) : a ? 1 : b ? -1 : 0; }
static float calculate_scale(MetaMonitor *m, MetaMonitorMode *mode, int constraint)
{ (void)m; (void)mode; (void)constraint; dpi_calls++; return 1.75f; }
'''
    harness += function
    harness += r'''
static void expect(float expected, int calls, const char *message)
{
  dpi_calls = 0;
  float actual = meta_monitor_calculate_mode_scale(NULL, NULL, 0);
  if (actual != expected || dpi_calls != calls) {
    fprintf(stderr, "FAIL %s: scale=%g DPI calls=%d\n", message, actual, dpi_calls);
    exit(1);
  }
  printf("PASS %s\n", message);
}
int main(void)
{
  explicit_scale = false; unsetenv("LUMA_DEVICE_CLASS");
  expect(1.f, 0, "new desktop without a device hint starts at 100 percent");
  setenv("LUMA_DEVICE_CLASS", "desktop", 1);
  expect(1.f, 0, "desktop profile starts at 100 percent");
  explicit_scale = true; configured_scale = 2;
  expect(2.f, 0, "explicit desktop global scale survives");
  explicit_scale = false; setenv("LUMA_DEVICE_CLASS", "handheld", 1);
  expect(1.75f, 1, "handheld retains automatic DPI scale");
  explicit_scale = true; configured_scale = 3;
  expect(3.f, 0, "explicit handheld global scale survives");
  return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="luma-scale-test-") as directory:
        path = Path(directory)
        (path / "test.c").write_text(harness)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                        "-Wall", "-Werror", str(path / "test.c"), "-o", str(path / "test")], check=True)
        subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    main()
