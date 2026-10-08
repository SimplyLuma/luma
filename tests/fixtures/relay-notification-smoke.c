/* SPDX-License-Identifier: Apache-2.0 */

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <shellapi.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, WCHAR *command_line, int show)
{
    NOTIFYICONDATAW notification = {0};
    WNDCLASSEXW window_class = {0};
    HWND window;

    window_class.cbSize = sizeof(window_class);
    window_class.hInstance = instance;
    window_class.lpfnWndProc = DefWindowProcW;
    window_class.lpszClassName = L"LumaRelayNotificationSmoke";
    if (!RegisterClassExW(&window_class)) return 1;
    window = CreateWindowExW(0, window_class.lpszClassName, L"Relay notification smoke",
                             WS_OVERLAPPED, 0, 0, 1, 1, NULL, NULL, instance, NULL);
    if (!window) return 2;

    notification.cbSize = sizeof(notification);
    notification.hWnd = window;
    notification.uID = 1;
    notification.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    notification.uCallbackMessage = WM_APP + 1;
    notification.hIcon = LoadIconW(NULL, IDI_APPLICATION);
    lstrcpynW(notification.szTip, L"Relay notification acceptance test",
             ARRAYSIZE(notification.szTip));
    if (!Shell_NotifyIconW(NIM_ADD, &notification)) return 3;

    notification.uFlags = NIF_INFO;
    notification.dwInfoFlags = NIIF_INFO;
    lstrcpynW(notification.szInfoTitle, L"Relay Test", ARRAYSIZE(notification.szInfoTitle));
    lstrcpynW(notification.szInfo,
             L"This notification crossed Relay's private native boundary.",
             ARRAYSIZE(notification.szInfo));
    if (!Shell_NotifyIconW(NIM_MODIFY, &notification)) return 4;
    Sleep(1000);
    lstrcpynW(notification.szInfo,
             L"Relay replaced the existing native notification by identity.",
             ARRAYSIZE(notification.szInfo));
    if (!Shell_NotifyIconW(NIM_MODIFY, &notification)) return 5;
    Sleep(8000);
    Shell_NotifyIconW(NIM_DELETE, &notification);
    DestroyWindow(window);
    return 0;
}
