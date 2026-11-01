"""One isolated video task, so cancellation can stop native AI and FFmpeg work."""
import os
import re
import sys
import threading

def watch_parent():
    """Stop the worker and its children if the studio launcher is closed."""
    parent_pid = int(os.environ.get('STUDIO_PARENT_PID', '0'))
    if not parent_pid:
        return
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import subprocess
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if handle:
            while kernel.WaitForSingleObject(handle, 1000) == 0x102:
                pass
            kernel.CloseHandle(handle)
        subprocess.run(['taskkill', '/PID', str(os.getpid()), '/T', '/F'], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        os._exit(1)
    else:
        import signal
        import time
        while os.getppid() == parent_pid:
            time.sleep(1)
        os.killpg(os.getpgrp(), signal.SIGTERM)

if __name__ == '__main__':
    if len(sys.argv) != 3 or not re.fullmatch(r'[a-f0-9]{32}', sys.argv[1]) or sys.argv[2] not in {'process', 'export', 'english'}:
        raise SystemExit('Invalid video task.')
    os.environ['STUDIO_WORKER'] = '1'
    threading.Thread(target=watch_parent, daemon=True).start()
    from server import get_job, process_video, export_video, generate_english
    get_job(sys.argv[1])
    {'export': export_video, 'process': process_video, 'english': generate_english}[sys.argv[2]](sys.argv[1])
