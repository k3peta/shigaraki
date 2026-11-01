#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include "launcher-copy.h"

#define OWN_EDITION_MUTEX L"Local\\ShigarakiHaasPreview.Desktop.Running"
#define OTHER_EDITION_MUTEX L"Local\\Shigaraki.Desktop.Running"

/* References, not mutex ownership: Python retains its own handles after handoff. */
static int marker_present(const wchar_t *name) {
    HANDLE handle = OpenMutexW(SYNCHRONIZE, FALSE, name);
    if (handle) { CloseHandle(handle); return 1; }
    return GetLastError() == ERROR_FILE_NOT_FOUND ? 0 : -1;
}

static int acquire_usage(HANDLE *active, HANDLE *running) {
    int other = marker_present(OTHER_EDITION_MUTEX);
    if (other) return other > 0 ? 2 : 1;
    SetLastError(0);
    *active = CreateMutexW(NULL, FALSE, L"Local\\Shigaraki.Dictation.Active");
    BOOL existed = GetLastError() == ERROR_ALREADY_EXISTS;
    if (!*active) return 1;
    if (existed) {
        int own = marker_present(OWN_EDITION_MUTEX);
        if (own <= 0) {
            CloseHandle(*active); *active = NULL;
            return own < 0 ? 1 : 2;
        }
    }
    *running = CreateMutexW(NULL, FALSE, OWN_EDITION_MUTEX);
    if (!*running) { CloseHandle(*active); *active = NULL; return 1; }
    return 0;
}


/* A GUI entry point only. Inference remains in the existing isolated runtime. */
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    (void)instance; (void)previous; (void)show;
    wchar_t root[32768], python[32768], script[32768], command[65536];
    DWORD length = GetModuleFileNameW(NULL, root, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = L'\0';
    while (*arguments == L' ') arguments++;
    BOOL preparing = wcscmp(arguments, L"--prepare") == 0;
    BOOL installed = wcscmp(arguments, L"--installed") == 0;
    if (*arguments && !preparing && !installed) return 2;
    if (swprintf(python, 32768, L"%ls\\runtime\\pythonw.exe", root) < 0 ||
        swprintf(script, 32768, L"%ls\\%ls", root, preparing ? L"install.py" : L"launch.py") < 0 ||
        swprintf(command, 65536, L"\"%ls\" \"%ls\"%ls", python, script,
                 preparing ? L"" : installed ? L" run --installed" : L" run") < 0) return 1;
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, COPY_MISSING, COPY_NAME, MB_OK | MB_ICONERROR);
        return 1;
    }
    HANDLE active = NULL, running = NULL;
    int guard = acquire_usage(&active, &running);
    if (guard) {
        MessageBoxW(NULL, guard == 2 ? COPY_OTHER_RUNNING : COPY_FAILED, COPY_NAME,
                    MB_OK | (guard == 2 ? MB_ICONINFORMATION : MB_ICONERROR));
        return guard;
    }
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1");
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, root, &startup, &process)) {
        MessageBoxW(NULL, COPY_FAILED, COPY_NAME, MB_OK | MB_ICONERROR);
        CloseHandle(running);
        CloseHandle(active);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hProcess);
    CloseHandle(running);
    CloseHandle(active);
    return (int)exit_code;
}
