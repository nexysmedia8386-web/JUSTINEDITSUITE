import os
import sys
import shutil
import glob

if sys.platform == "win32":
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ensure_media_env():
    """Ensure ffmpeg and ffprobe are available in PATH, especially on Windows
    where newly installed tools or GUI apps (e.g. Adobe Premiere CEP)
    might have inherited an outdated PATH without ffprobe/ffmpeg.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    candidates = []

    # 0. Portable ffmpeg that nxcore.doctor downloads when none is installed
    if sys.platform == "win32":
        own = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JustinEditSuite", "ffmpeg", "bin")
    elif sys.platform == "darwin":
        own = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "JustinEditSuite", "ffmpeg", "bin")
    else:
        own = os.path.join(os.path.expanduser("~"), ".local", "share", "JustinEditSuite", "ffmpeg", "bin")
    if os.path.isdir(own):
        candidates.append(own)

    # 1. Check Windows Registry for updated PATH
    if sys.platform == "win32":
        try:
            import winreg

            for root, subkey in [
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                (winreg.HKEY_CURRENT_USER, r"Environment"),
            ]:
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        val, _ = winreg.QueryValueEx(key, "Path")
                        for p in val.split(";"):
                            p = os.path.expandvars(p.strip())
                            if p and os.path.isdir(p):
                                candidates.append(p)
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Known common Windows installation paths
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            winget_pattern = os.path.join(
                local_app, "Microsoft", "WinGet", "Packages", "*ffmpeg*", "**", "bin"
            )
            for d in glob.glob(winget_pattern, recursive=True):
                if os.path.isdir(d):
                    candidates.append(d)

        for d in [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\ProgramData\chocolatey\bin",
        ]:
            if os.path.isdir(d):
                candidates.append(d)

    # 3. Check imageio_ffmpeg
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            d = os.path.dirname(exe)
            candidates.append(d)
    except Exception:
        pass

    # Add candidate directories to os.environ["PATH"]
    cur_path = os.environ.get("PATH", "")
    cur_dirs = set(os.path.normcase(os.path.normpath(p)) for p in cur_path.split(os.pathsep) if p)

    new_dirs = []
    for d in candidates:
        norm = os.path.normcase(os.path.normpath(d))
        if norm not in cur_dirs:
            cur_dirs.add(norm)
            new_dirs.append(d)

    if new_dirs:
        os.environ["PATH"] = os.pathsep.join(new_dirs) + os.pathsep + cur_path


ensure_media_env()
