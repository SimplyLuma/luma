/* SPDX-License-Identifier: Apache-2.0
 * lumaui-conform: load the capture harness into a C/C++ GTK app.
 * LD_PRELOAD=lumaui-conform-preload.so with LUMAUI_CONFORM_DUMP set: before main(), start
 * Python, import lumaui_conform_harness from LUMAUI_CONFORM_HARNESS_DIR and install() it
 * (it pins settings and adds a GLib timeout on the default main context, which the app's
 * own main loop then runs), then release the GIL for the app. Children do not inherit it. */
#include <Python.h>
#include <stdlib.h>

__attribute__((constructor)) static void lumaui_conform_preload(void) {
  const char *dir = getenv("LUMAUI_CONFORM_HARNESS_DIR");
  unsetenv("LD_PRELOAD");
  if (!getenv("LUMAUI_CONFORM_DUMP") || !dir || Py_IsInitialized())
    return;
  Py_InitializeEx(0);
  PyObject *sys_path = PySys_GetObject("path");
  PyObject *entry = PyUnicode_FromString(dir);
  PyList_Insert(sys_path, 0, entry);
  Py_DECREF(entry);
  if (PyRun_SimpleString("import lumaui_conform_harness as h; h.install()") != 0)
    fprintf(stderr, "lumaui-conform: the harness did not load\n");
  PyEval_SaveThread();
}
