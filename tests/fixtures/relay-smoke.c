#include <windows.h>

static LRESULT CALLBACK relay_window_proc(
    HWND window, UINT message, WPARAM wparam, LPARAM lparam
) {
    switch (message) {
    case WM_PAINT: {
        PAINTSTRUCT paint;
        RECT bounds;
        HDC device = BeginPaint(window, &paint);
        GetClientRect(window, &bounds);
        SetBkMode(device, TRANSPARENT);
        SetTextColor(device, RGB(31, 41, 55));
        DrawTextA(
            device,
            "Windows software is running through Luma Relay.",
            -1,
            &bounds,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE
        );
        EndPaint(window, &paint);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcA(window, message, wparam, lparam);
    }
}

int WINAPI WinMain(
    HINSTANCE instance, HINSTANCE previous, LPSTR command_line, int show
) {
    const char class_name[] = "LumaRelaySmokeWindow";
    WNDCLASSA window_class = {0};
    MSG message;
    HWND window;

    (void)previous;
    (void)command_line;
    window_class.lpfnWndProc = relay_window_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursor(NULL, IDC_ARROW);
    window_class.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    window_class.lpszClassName = class_name;
    if (!RegisterClassA(&window_class))
        return 1;

    window = CreateWindowExA(
        0,
        class_name,
        "Luma Relay verification",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        560,
        220,
        NULL,
        NULL,
        instance,
        NULL
    );
    if (!window)
        return 2;

    ShowWindow(window, show);
    UpdateWindow(window);
    while (GetMessageA(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageA(&message);
    }
    return (int)message.wParam;
}
