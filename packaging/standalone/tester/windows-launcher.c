/* SPDX-License-Identifier: Apache-2.0
 *
 * <App>.exe for a `luma port windows` folder. Generic: it knows nothing about the app.
 * Sets PATH / PYTHONHOME / PYTHONPATH relative to itself and runs
 *     bin\pythonw.exe -m luma_port_boot [args...]
 * (luma_port_boot reads app\luma-port-app.json for the module and env).
 * Build: clang --target=x86_64-w64-mingw32 -municode -mwindows -O2 luma-launcher.c
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>

#define BUF 32768

static void fail(const wchar_t *what)
{
  wchar_t msg[1024];
  DWORD err = GetLastError();
  _snwprintf(msg, 1024, L"%ls (error %lu).\n\nKeep the whole folder together and run it from where you unzipped it.",
             what, (unsigned long) err);
  MessageBoxW(NULL, msg, L"Luma port launcher", MB_ICONERROR | MB_OK);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show)
{
  (void) inst; (void) prev; (void) show;
  static wchar_t root[MAX_PATH * 4], buf[BUF], python[MAX_PATH * 4], cmd[BUF], sysroot[MAX_PATH];
  DWORD n = GetModuleFileNameW(NULL, root, MAX_PATH * 4);
  if (n == 0 || n >= MAX_PATH * 4) { fail(L"Cannot find where the launcher is"); return 1; }
  wchar_t *slash = wcsrchr(root, L'\\');
  if (slash) *slash = 0;

  if (!GetEnvironmentVariableW(L"SystemRoot", sysroot, MAX_PATH)) wcscpy(sysroot, L"C:\\Windows");
  _snwprintf(buf, BUF, L"%ls\\bin;%ls\\System32;%ls", root, sysroot, sysroot);
  SetEnvironmentVariableW(L"PATH", buf);
  SetEnvironmentVariableW(L"PYTHONHOME", root);
  _snwprintf(buf, BUF, L"%ls\\app", root);
  SetEnvironmentVariableW(L"PYTHONPATH", buf);
  SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");
  SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
  SetEnvironmentVariableW(L"LUMA_PORT_ROOT", root);

  _snwprintf(python, MAX_PATH * 4, L"%ls\\bin\\pythonw.exe", root);
  if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) { fail(L"bin\\pythonw.exe is missing"); return 1; }
  _snwprintf(cmd, BUF, L"\"%ls\" -m luma_port_boot %ls", python, args ? args : L"");

  STARTUPINFOW si = { .cb = sizeof si };
  PROCESS_INFORMATION pi;
  if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, root, &si, &pi)) {
    fail(L"Could not start Python");
    return 1;
  }
  WaitForSingleObject(pi.hProcess, INFINITE);
  DWORD code = 1;
  GetExitCodeProcess(pi.hProcess, &code);
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  return (int) code;
}
