"""Foobar Control - Flow Launcher plugin: control foobar2000 and play your music library.

Author: Malka'
"""
import sys
import os
import re
import random
import json
import time
import base64
import hashlib
import difflib
import subprocess
import unicodedata
import traceback
import tempfile
import uuid
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))

from flowlauncher import FlowLauncher  # noqa: E402

try:
    import mutagen  # optional, used for tags and embedded covers
    from mutagen.flac import Picture
except Exception:  # pragma: no cover
    mutagen = None
    Picture = None

CONFIG_PATH = HERE / "config.json"
CACHE_PATH = HERE / "index_cache.json"
TEMP_DIR = Path(tempfile.gettempdir()) / "FoobarControlCovers"
LOCK_PATH = HERE / "index.lock"
NOW_CACHE_PATH = HERE / "now_playing_cache.json"
QUEUE_PATH = HERE / "queue_state.json"   # what the plugin queued in foobar (for "Next:")
PLAYLIST_WORDS = ("playlist", "playlists")
DEBUG_PATH = HERE / "playlist_debug.txt"
# Flow Launcher sorts results by Score and also promotes the ones you pick often. These big gaps keep
# the choices in a fixed order: Play now, Play next, then the rest.
SC_NOW, SC_NEXT, SC_THIRD, SC_FOURTH, SC_FIFTH = 1000000, 900000, 800000, 700000, 600000
NOW_NAMES = ("now", "np", "playing", "current", "nowplaying")  # `bar now` also shows the current song
INDEX_VERSION = 3  # bump when the index gains fields (it is rebuilt in the background)
ERROR_LOG = HERE / "error.log"
ICON = "icon.png"

ICON_PLAYPAUSE = "icons/playpause.png"
ICON_NEXT = "icons/next.png"
ICON_PREVIOUS = "icons/previous.png"
ICON_STOP = "icons/stop.png"
ICON_RESCAN = "icons/rescan.png"
# shown when a result has no cover / picture of its own
ICON_SONG = "icons/song.png"
ICON_ALBUM = "icons/album.png"
ICON_ARTIST = "icons/artist.png"
ICON_RADIO = "icons/shuffle.png"
ICON_SIMILAR = "icons/shuffle_filled.png"

ALBUM_ART_NAMES = ["cover", "folder", "front", "album", "albumart"]
ARTIST_ART_NAMES = ["artist", "artist_picture", "band", "photo"]
FOLDER_ART_NAMES = ["folder", "poster", "cover"]
IMG_EXTS = (".jpg", ".jpeg", ".png")

DEFAULT_CONFIG = {
    "foobar_path": "",   # empty = find foobar2000.exe automatically
    "music_dir": "",     # empty = your Windows Music folder
    "extensions": [".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".wma", ".aac", ".ape", ".wv"],
    "cache_hours": 24,
    "queue_command": "Add to playback queue",
    "max_results": 15,
    "max_per_type": 6,
    "typo_threshold": 0.85,
    "ask_play_mode": True,
    "cover_mode": "temp",
    "data_uri_max_kb": 400,
    "radio_size": 40,         # songs queued by Radio
    "similar_size": 20,       # songs queued by Similar
    "radio_per_artist": 4,    # max songs of one artist in a Radio mix
    "radio_command": "",      # optional: name of a foobar command to use instead (see README)
    "similar_command": "",
    "action_keyword": "",     # optional: force the keyword used by the Enter-then-choose step
    "startup_delay": 3,       # seconds to let foobar2000 finish starting before sending it commands
    "startup_timeout": 30,    # longest wait for foobar2000's window after starting it
    "command_timeout": 8,     # longest wait for one foobar command to be accepted
    "index_threads": 8,       # files whose tags are read at the same time during a scan
    "playlist_queue_limit": 500,  # most songs queued when you play a foobar playlist
    "queue_settle_delay": 0.4,    # seconds to let foobar register the queue before "Play now" skips to it
    "play_now_retries": 2,        # extra "next" presses if foobar (e.g. Smart Shuffle) played another song first
    "play_verify_timeout": 2.0,   # seconds to wait for the chosen song to show in foobar's title
}
SETTINGS = dict(DEFAULT_CONFIG)


def _isfile(p):
    try:
        return bool(p) and os.path.isfile(p)
    except Exception:
        return False


def _registry_foobar_candidates():
    out = []
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\foobar2000.exe",
                                        0, winreg.KEY_READ | view) as k:
                        out.append(str(winreg.QueryValueEx(k, "")[0]).strip('"'))
                except OSError:
                    pass
                try:
                    u = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                                       0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with u:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(u, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(u, name) as k:
                                if not str(winreg.QueryValueEx(k, "DisplayName")[0]).lower().startswith("foobar2000"):
                                    continue
                                for val in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    try:
                                        v = str(winreg.QueryValueEx(k, val)[0]).split(",")[0].strip().strip('"')
                                    except OSError:
                                        continue
                                    if v:
                                        out.append(v if v.lower().endswith("foobar2000.exe")
                                                   else os.path.join(v if os.path.isdir(v) else os.path.dirname(v),
                                                                     "foobar2000.exe"))
                        except OSError:
                            continue
    except Exception:
        pass
    return out


def find_foobar():
    """Find foobar2000.exe with no configuration: usual install folders, then the
    Windows registry, then PATH. Returns '' if nothing is found."""
    env = os.environ.get
    paths = [os.path.join(b, "foobar2000", "foobar2000.exe")
             for b in (env("ProgramFiles"), env("ProgramW6432"), env("ProgramFiles(x86)")) if b]
    local = env("LOCALAPPDATA")
    if local:
        paths += [os.path.join(local, "foobar2000", "foobar2000.exe"),
                  os.path.join(local, "Programs", "foobar2000", "foobar2000.exe")]
    paths += [os.path.join(str(Path.home()), "foobar2000", "foobar2000.exe"),
              r"C:\foobar2000\foobar2000.exe", r"D:\foobar2000\foobar2000.exe"]
    for p in paths:
        if _isfile(p):
            return p
    if os.name == "nt":
        for p in _registry_foobar_candidates():
            if _isfile(p):
                return p
    return shutil.which("foobar2000") or shutil.which("foobar2000.exe") or ""


def resolve_foobar(configured):
    """The configured path if it works, otherwise the detected one (remembered in config.json)."""
    if _isfile(configured):
        return configured
    found = find_foobar()
    if found:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            data["foobar_path"] = found
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass
    return found


def default_music_dir():
    """The real Windows Music folder (follows OneDrive / relocated folders)."""
    if os.name == "nt":
        try:
            import ctypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                            ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

            g = uuid.UUID("4BD8D571-6D19-48D3-BE97-422220080E43")  # FOLDERID_Music
            guid = GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return path
        except Exception:
            pass
    return str(Path.home() / "Music")


def action_keyword():
    """The keyword set in Flow Launcher (read from Flow's own settings), default 'bar'."""
    if SETTINGS.get("action_keyword"):
        return SETTINGS["action_keyword"]
    kw = "bar"
    try:
        meta = json.loads((HERE / "plugin.json").read_text(encoding="utf-8-sig"))
        first = [k for k in (meta.get("ActionKeywords") or []) if k and k != "*"]
        kw, pid = (first[0] if first else meta.get("ActionKeyword")) or kw, meta.get("ID", "")
        settings = json.loads((HERE.parent.parent / "Settings" / "Settings.json").read_text(encoding="utf-8-sig"))
        plugins = settings.get("PluginSettings", {}).get("Plugins", {})
        entry = plugins.get(pid)
        if entry is None:
            entry = next((v for v in plugins.values() if isinstance(v, dict) and v.get("ID") == pid), None)
        keys = [k for k in (entry or {}).get("ActionKeywords", []) if k and k != "*"]
        if keys:
            kw = keys[0]
    except Exception:
        pass
    return kw


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    else:
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        except Exception:
            pass
    cfg["foobar_path"] = resolve_foobar(cfg.get("foobar_path", ""))
    if not cfg.get("music_dir"):
        cfg["music_dir"] = default_music_dir()
    return cfg


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("'", "").replace("’", "").replace("`", "")
    text = "".join(c if (c.isalnum() or c.isspace()) else " " for c in text)
    return " ".join(text.split())


def clean_artist_name(name):
    """Strips 'feat.', 'ft.', 'vs.', and secondary artists so 'Black Coffee feat. Bucie' -> 'Black Coffee'."""
    name = re.split(r'\s+(?:feat\.?|ft\.?|featuring|vs\.?|with)\s+', name, flags=re.IGNORECASE)[0]
    return name.strip()


def icon_path(rel):
    p = HERE / rel
    return str(p) if p.exists() else ICON


def short_id(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


def read_tags(path):
    title = artist = album = genre = ""
    track = disc = year = 0
    if mutagen is not None:
        try:
            f = mutagen.File(path, easy=True)
            if f is not None and f.tags:
                title = (f.tags.get("title") or [""])[0]
                artist = (f.tags.get("artist") or [""])[0]
                album = (f.tags.get("album") or [""])[0]
                t = (f.tags.get("tracknumber") or ["0"])[0].split("/")[0]
                d = (f.tags.get("discnumber") or ["0"])[0].split("/")[0]
                track = int(t) if t.isdigit() else 0
                disc = int(d) if d.isdigit() else 0
                genre = ", ".join(f.tags.get("genre") or [])
                date = ((f.tags.get("date") or [""])[0] or "")[:4]
                year = int(date) if date.isdigit() else 0
        except Exception:
            pass
    if not title:
        title = Path(path).stem
    return title, artist, album, track, disc, genre, year


def primary_artist(name):
    return norm(clean_artist_name(name or ""))


def split_genres(genre):
    return [g for g in (norm(x) for x in re.split(r"[;,/|]+", genre or "")) if g]


def feats(t):
    """(main artist, genres, album, year) of a track, normalised; works on older indexes too."""
    f = t.get("_f")
    if f is None:
        na = t.get("nartist")
        if na is None:
            na = primary_artist(t.get("artist", ""))
        gs = t.get("genres")
        if gs is None:
            gs = split_genres(t.get("genre", ""))
        nal = t.get("nalbum")
        if nal is None:
            nal = norm(t.get("album", ""))
        f = t["_f"] = (na, set(gs), nal, t.get("year", 0) or 0)
    return f


def similarity(seed, c):
    """0 = unrelated. A song must share the main artist or a genre with the seed;
    same album and nearby release year then make it count more."""
    sa, sg, sal, sy = feats(seed)
    ca, cg, cal, cy = feats(c)
    score = 0.0
    if sa and sa == ca:
        score += 4.0
    if sg and cg:
        score += 3.0 * min(len(sg & cg), 2)
    if not score:
        return 0.0
    if sal and sal == cal:
        score += 1.0
    if sy and cy:
        d = abs(sy - cy)
        score += 1.5 if d <= 2 else 1.0 if d <= 5 else 0.5 if d <= 10 else 0.0
    return score


def build_mix(kind, seed, tracks, cfg):
    """Songs for Similar (best matches first) or Radio (a weighted random mix with variety)."""
    scored = []
    for c in tracks:
        if c["path"] != seed["path"]:
            s = similarity(seed, c)
            if s > 0:
                scored.append((s, c))
    if not scored:
        return []
    if kind == "similar":
        random.shuffle(scored)                  # ties come out in random order
        scored.sort(key=lambda x: -x[0])
        return [c for _s, c in scored[: cfg["similar_size"]]]
    keyed = sorted(((random.random() ** (1.0 / s), c) for s, c in scored), key=lambda x: -x[0])
    out, per_artist = [], {}
    for _k, c in keyed:
        a = feats(c)[0]
        if a and per_artist.get(a, 0) >= cfg["radio_per_artist"]:
            continue
        per_artist[a] = per_artist.get(a, 0) + 1
        out.append(c)
        if len(out) >= cfg["radio_size"]:
            break
    return out


def foobar_windows():
    """(title, visible) for every top-level window of foobar2000.exe: [] when foobar isn't
    running, None when this can't be checked (not Windows)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        wins, exes = [], {}

        def on_window(hwnd, _lparam):
            try:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                exe = exes.get(pid.value)
                if exe is None:
                    exe = ""
                    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if h:
                        buf = ctypes.create_unicode_buffer(1024)
                        size = wintypes.DWORD(1024)
                        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                            exe = os.path.basename(buf.value).lower()
                        kernel32.CloseHandle(h)
                    exes[pid.value] = exe
                if exe == "foobar2000.exe":
                    n = user32.GetWindowTextLengthW(hwnd)
                    title = ""
                    if n > 0:
                        b = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(hwnd, b, n + 1)
                        title = b.value
                    wins.append((title, bool(user32.IsWindowVisible(hwnd))))
            except Exception:
                pass
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(on_window)
        user32.EnumWindows(callback, 0)
        return wins
    except Exception:
        return None


def now_playing_titles():
    """Titles of foobar2000's visible windows (its main window shows the playing song)."""
    return [t for t, visible in (foobar_windows() or []) if visible and t]


def foobar_running():
    """True / False, or None when it can't be checked."""
    w = foobar_windows()
    return None if w is None else bool(w)


def match_now_playing(titles, tracks):
    """Find the library track whose title (and artist) appears in foobar's window title."""
    best, best_score = None, 0
    for title in titles:
        nt = " %s " % norm(re.sub(r"\[foobar2000[^\]]*\]", " ", title, flags=re.I))
        if len(nt) < 5:
            continue
        for t in tracks:
            nti = t.get("ntitle") or norm(t.get("title", ""))
            if len(nti) < 2 or (" %s " % nti) not in nt:
                continue
            score = len(nti)
            na, _g, nal, _y = feats(t)
            if na and (" %s " % na) in nt:
                score += 100
            if nal and (" %s " % nal) in nt:
                score += 20
            if score > best_score:
                best, best_score = t, score
    return best


def playing_window_title():
    """(raw window title, cleaned 'Artist - Title') of the song foobar2000 is showing, or
    (None, None) when nothing is playing. A stopped foobar only shows 'foobar2000 v2.x', and
    other foobar windows (Preferences...) are ignored because they carry no '[foobar2000]' tag."""
    for title, visible in (foobar_windows() or []):
        if not (visible and title) or "[foobar2000" not in title.lower():
            continue
        clean = re.sub(r"\s*\[foobar2000[^\]]*\]", "", title, flags=re.I).strip()
        if clean and not norm(clean).startswith("foobar2000"):
            return title, clean
    return None, None


def load_now_cache(key):
    """The track found for this exact window title last time (saves loading the whole index)."""
    try:
        c = json.loads(NOW_CACHE_PATH.read_text(encoding="utf-8"))
        if c.get("key") == key and c.get("track") and os.path.isfile(c["track"].get("path", "")):
            return c["track"]
    except Exception:
        pass
    return None


def save_now_cache(key, track):
    try:
        clean = {k: v for k, v in track.items() if not k.startswith("_")}  # '_f' holds a set
        NOW_CACHE_PATH.write_text(json.dumps({"key": key, "track": clean}), encoding="utf-8")
    except Exception:
        pass


# ---- what the plugin has queued in foobar ("Next:" in the Now playing result) -------------------
# foobar2000 has no way to ask it for its queue from outside without an extra component, so the
# plugin keeps its own record of what IT queued, and drops entries as they start playing.

def load_queue():
    try:
        items = json.loads(QUEUE_PATH.read_text(encoding="utf-8")).get("items", [])
        return [i for i in items if time.time() - i.get("t", 0) < 12 * 3600]
    except Exception:
        return []


def save_queue(items):
    try:
        QUEUE_PATH.write_text(json.dumps({"items": items[-500:]}), encoding="utf-8")
    except Exception:
        pass


def clear_queue():
    try:
        QUEUE_PATH.unlink()
    except OSError:
        pass


def record_queue(entries):
    """entries: [(path, label)] just sent to foobar's playback queue."""
    now = time.time()
    save_queue(load_queue() + [{"p": os.path.normcase(p), "l": l, "t": now} for p, l in entries])


def pop_queue_until(path):
    """The song at `path` has started: forget it and everything queued before it."""
    items = load_queue()
    key = os.path.normcase(path)
    for i, it in enumerate(items):
        if it["p"] == key:
            items = items[i + 1:]
            save_queue(items)
            break
    return items


def track_label(t):
    return " - ".join(x for x in [t.get("artist", ""), t.get("title", "")] if x) or Path(t["path"]).stem


# ---- foobar2000's own playlists -------------------------------------------------------------------
# foobar saves each playlist as a .fpl file in its profile folder. The format is closed, so the
# plugin only picks the file paths out of it (they are stored as "file://..." text).

def foobar_profile_dirs(cfg):
    """Possible foobar2000 profile folders, the most likely first."""
    out = []
    fdir = os.path.dirname(cfg.get("foobar_path", "") or "")
    if fdir and os.path.exists(os.path.join(fdir, "portable_mode_enabled")):
        out += [os.path.join(fdir, "profile"), fdir]
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.getenv(var, "")
        if not base:
            continue
        out += [os.path.join(base, "foobar2000-v2"), os.path.join(base, "foobar2000")]
        try:  # any other folder starting with "foobar" (renamed or custom profiles)
            out += [os.path.join(base, d) for d in sorted(os.listdir(base)) if d.lower().startswith("foobar")]
        except OSError:
            pass
    if fdir:
        out += [os.path.join(fdir, "profile"), fdir]
    seen, res = set(), []
    for d in out:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            res.append(d)
    return res


def playlists_dir(cfg):
    """The folder holding the .fpl files (named playlists-v2.0, playlists-v1.4... by version)."""
    for prof in foobar_profile_dirs(cfg):
        best, best_time = "", -1
        try:
            subs = [d for d in os.listdir(prof) if d.lower().startswith("playlists")]
        except OSError:
            continue
        for d in subs + ["."]:
            full = os.path.normpath(os.path.join(prof, d))
            try:
                fpls = [f for f in os.listdir(full) if f.lower().endswith(".fpl")]
                if fpls:
                    t = max(os.path.getmtime(os.path.join(full, f)) for f in fpls)
                    if t > best_time:
                        best, best_time = full, t
            except OSError:
                continue
        if best:
            return best
    return ""


def playlist_names(pdir, stems):
    """{stem: name} read from foobar's playlist index (index.txt). Anything not understood
    simply has no name, and the plugin falls back to describing the playlist by its content."""
    names = {}
    for idx in ("index.txt",):
        try:
            lines = (Path(pdir) / idx).read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            low = line.lower()
            for stem in stems:
                if stem.lower() in low:
                    name = re.sub(re.escape(stem) + r"(\.fpl)?", "", line, flags=re.I)
                    name = name.strip(" \t|=:;,\"'[]{}()<>")
                    if name:
                        names[stem] = name
    return names


def playlist_files(path, cfg=None):
    """File paths stored in a .fpl, in playlist order (a file listed twice is kept once).
    foobar stores them as "file://..." text; if none are found, plain paths (C:\\... or \\\\server\\...)
    that end in a music extension are used instead."""
    cfg = cfg or SETTINGS
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    found = [m.group(1) for m in re.finditer(rb"file://([^\x00]+)", data)]
    if not found:
        exts = {e.lower() for e in cfg.get("extensions", [])}
        for m in re.finditer(rb"(?<![A-Za-z0-9])((?:[A-Za-z]:[\\/]|\\\\)[^\x00]+)", data):
            if os.path.splitext(m.group(1).decode("utf-8", "replace"))[1].lower() in exts:
                found.append(m.group(1))
    base = os.path.dirname(cfg.get("foobar_path", "") or "")
    out, seen = [], set()
    for raw in found:
        f = raw.decode("utf-8", "replace")
        if not os.path.isabs(f) and base:  # portable installs store paths relative to foobar
            f = os.path.normpath(os.path.join(base, f))
        k = os.path.normcase(f)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def list_playlists(cfg):
    """[{id, name, path}] for every saved foobar playlist, sorted by name."""
    pdir = playlists_dir(cfg)
    if not pdir:
        return []
    files = sorted(f for f in os.listdir(pdir) if f.lower().endswith(".fpl"))
    stems = [os.path.splitext(f)[0] for f in files]
    names = playlist_names(pdir, stems)
    out = []
    for f, stem in zip(files, stems):
        full = os.path.join(pdir, f)
        name = names.get(stem)
        if not name:
            first = playlist_files(full, cfg)[:1]
            name = ("Playlist (starts with %s)" % Path(first[0]).stem) if first else "Playlist " + stem[:8]
        out.append({"id": short_id("playlist:" + stem), "name": name, "path": full})
    out.sort(key=lambda x: norm(x["name"]))
    return out


def write_playlist_debug(cfg):
    """Writes playlist_debug.txt: what the plugin looked at, to find out why playlists are missing."""
    L = ["Foobar Control playlist diagnostic", time.strftime("%Y-%m-%d %H:%M:%S"), "",
         "foobar_path: %r" % cfg.get("foobar_path"),
         "APPDATA: %r" % os.getenv("APPDATA"), "LOCALAPPDATA: %r" % os.getenv("LOCALAPPDATA"), ""]
    for prof in foobar_profile_dirs(cfg):
        try:
            L.append("profile folder: %s -> %s" % (prof, sorted(os.listdir(prof))[:40]))
        except OSError as e:
            L.append("profile folder: %s -> %s" % (prof, e))
    pdir = playlists_dir(cfg)
    L += ["", "playlists folder used: %r" % pdir]
    if pdir:
        fpls = sorted(f for f in os.listdir(pdir) if f.lower().endswith(".fpl"))
        L.append("%d .fpl files, other files: %s" % (len(fpls), [f for f in os.listdir(pdir) if f not in fpls][:20]))
        try:
            L.append("index.txt (first lines): %r" % (Path(pdir, "index.txt").read_text(
                encoding="utf-8-sig", errors="replace").splitlines()[:12],))
        except OSError as e:
            L.append("index.txt: %s" % e)
        if fpls:
            full = os.path.join(pdir, fpls[0])
            data = Path(full).read_bytes()
            L += ["first .fpl: %s (%d bytes)" % (fpls[0], len(data)), "first 96 bytes (hex): " + data[:96].hex(),
                  "paths found in it: %d" % len(playlist_files(full, cfg)),
                  "readable strings: %r" % ([m.group(0).decode("latin-1")[:80]
                                             for m in re.finditer(rb"[\x20-\x7e]{8,}", data)][:8],)]
    DEBUG_PATH.write_text("\n".join(L), encoding="utf-8")
    return DEBUG_PATH


def touch_lock():
    try:
        os.utime(str(LOCK_PATH), None)
    except OSError:
        pass


def scan_files(cfg):
    """(path, folder, mtime, size) of every music file. scandir gives the size and date for
    free (no extra disk access on Windows), which is what makes a rescan fast."""
    exts = {e.lower() for e in cfg["extensions"]}
    stack = [cfg["music_dir"]]
    while stack:
        root = stack.pop()
        touch_lock()
        try:
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif os.path.splitext(e.name)[1].lower() in exts:
                            st = e.stat()
                            yield e.path, root, st.st_mtime_ns, st.st_size
                    except OSError:
                        continue
        except OSError:
            continue


def make_track(p, root, mtime, size):
    title, artist, album, track, disc, genre, year = read_tags(p)
    return {
        "id": short_id(p),
        "path": p,
        "dir": root,
        "title": title,
        "artist": artist,
        "album": album,
        "track": track,
        "disc": disc,
        "ntitle": norm(title),
        "genre": genre,
        "year": year,
        "nartist": primary_artist(artist),
        "genres": split_genres(genre),
        "nalbum": norm(album),
        "mtime": mtime,
        "size": size,
    }


def build_index(cfg):
    """Incremental: a file whose date and size did not change keeps its saved tags, only new or
    modified files are read (in parallel). The first scan reads everything; later ones take seconds."""
    old = {}
    try:
        prev = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if prev.get("music_dir") == cfg["music_dir"] and prev.get("version") == INDEX_VERSION:
            old = {t["path"]: t for t in prev.get("tracks", [])}
    except Exception:
        pass
    tracks, todo = [], []
    for p, root, mtime, size in scan_files(cfg):
        o = old.get(p)
        if o is not None and o.get("mtime") == mtime and o.get("size") == size:
            tracks.append(o)
        else:
            todo.append((p, root, mtime, size))
    if todo:
        workers = max(1, int(cfg.get("index_threads", 8)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, t in enumerate(pool.map(lambda a: make_track(*a), todo), 1):
                tracks.append(t)
                if i % 50 == 0:
                    touch_lock()
    tracks.sort(key=lambda t: t["path"])
    data = {"version": INDEX_VERSION, "built": time.time(), "music_dir": cfg["music_dir"], "tracks": tracks}
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")  # compact = loads faster
    os.replace(str(tmp), str(CACHE_PATH))  # atomic: a half-written index is never read
    return data


def lock_active():
    """A build is running if its lock file was touched in the last 2 minutes."""
    try:
        return (time.time() - LOCK_PATH.stat().st_mtime) < 120
    except OSError:
        return False


def start_background_build():
    """Scan the library in a separate process so a search never has to wait for it."""
    if lock_active():
        return
    try:
        LOCK_PATH.write_text(str(os.getpid()))
        flags = (0x00000008 | 0x08000000) if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, str(HERE / "main.py"), json.dumps({"method": "build_now", "parameters": []})],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True, cwd=str(HERE),
        )
    except Exception:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def get_index(cfg):
    """Returns the saved index at once (even an old one) and refreshes it in the
    background when it is missing, stale, or made for another music folder."""
    data = None
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = None
    if data is not None and data.get("music_dir") != cfg["music_dir"]:
        data = None
    if (data is None or (time.time() - data.get("built", 0)) > cfg["cache_hours"] * 3600
            or data.get("version") != INDEX_VERSION):
        start_background_build()
    if data is None:
        return {"tracks": [], "building": lock_active()}
    data["building"] = False
    return data


def embedded_pictures(path):
    out = []
    if mutagen is None:
        return out
    try:
        f = mutagen.File(path)
        if f is None:
            return out
        for p in (getattr(f, "pictures", None) or []):
            out.append((int(p.type), p.data))
        tags = getattr(f, "tags", None)
        if tags:
            for k in list(tags.keys()):
                if str(k).startswith("APIC"):
                    fr = tags[k]
                    out.append((int(fr.type), fr.data))
            if "covr" in tags:
                for c in tags["covr"]:
                    out.append((3, bytes(c)))
            if Picture is not None and "metadata_block_picture" in tags:
                for b in tags["metadata_block_picture"]:
                    try:
                        p = Picture(base64.b64decode(b))
                        out.append((int(p.type), p.data))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def pick(pictures, order, allow_any):
    for wanted in order:
        for ptype, data in pictures:
            if ptype == wanted and data:
                return data
    if allow_any:
        for ptype, data in pictures:
            if data and ptype not in (4, 5, 6, 7, 8, 10):
                return data
    return None


def clear_temp():
    try:
        for f in TEMP_DIR.iterdir():
            f.unlink()
    except Exception:
        pass


def image_ref(data):
    png = data[:4] == b"\x89PNG"
    if SETTINGS.get("cover_mode") == "data_uri":
        if len(data) > SETTINGS.get("data_uri_max_kb", 400) * 1024:
            return ""
        return "data:image/%s;base64,%s" % ("png" if png else "jpeg", base64.b64encode(data).decode("ascii"))
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMP_DIR / (hashlib.md5(data).hexdigest()[:16] + (".png" if png else ".jpg"))
    if not out.exists():
        out.write_bytes(data)
    return str(out)


def find_image(directory, bases):
    try:
        names = {n.lower(): n for n in os.listdir(directory)}
    except Exception:
        return ""
    for base in bases:
        for ext in IMG_EXTS:
            if base + ext in names:
                return os.path.join(directory, names[base + ext])
    return ""


def cover_for(t):
    try:
        data = pick(embedded_pictures(t["path"]), [3, 0, 1, 2], True)
        if data:
            ref = image_ref(data)
            if ref:
                return ref
    except Exception:
        pass
    return find_image(t["dir"], ALBUM_ART_NAMES)


def album_cover(obj):
    c = find_image(obj["tracks"][0]["dir"], ALBUM_ART_NAMES)
    if c:
        return c
    for t in obj["tracks"][:3]:
        c = cover_for(t)
        if c:
            return c
    return ""


def find_foobar_cached_artist_art(artist_name):
    """Searches foobar2000 profile / appdata folders for cached artist images."""
    appdata = os.getenv("APPDATA", "")
    if not appdata:
        return ""

    clean_name = clean_artist_name(artist_name)
    targets = [norm(artist_name), norm(clean_name)]

    foobar_dirs = [
        Path(appdata) / "foobar2000",
        Path(appdata) / "foobar2000-v2",
        Path(appdata) / "foobar2000" / "artist_images",
        Path(appdata) / "foobar2000-v2" / "artist_images",
    ]

    for fb_dir in foobar_dirs:
        if fb_dir.exists():
            for root, _, files in os.walk(fb_dir):
                for f in files:
                    stem, ext = os.path.splitext(f)
                    if ext.lower() in IMG_EXTS and norm(stem) in targets:
                        return os.path.join(root, f)
    return ""


def artist_local_cover(obj):
    """Artist picture from embedded tags, foobar cache, or local artist files."""
    # 1. Embedded artist pictures in ID3 tags (type 8, 7, 10)
    for t in obj["tracks"][:10]:
        data = pick(embedded_pictures(t["path"]), [8, 7, 10], False)
        if data:
            ref = image_ref(data)
            if ref:
                return ref

    # 2. Check Foobar2000 profile cache for artist artwork
    fb_cached = find_foobar_cached_artist_art(obj["name"])
    if fb_cached:
        return fb_cached

    # 3. Local image files on disk matching artist name or main artist without 'feat.'
    clean_name = clean_artist_name(obj["name"])
    targets = [norm(obj["name"]), norm(clean_name)]

    dirs = []
    for t in obj["tracks"][:20]:
        for d in (t["dir"], os.path.dirname(t["dir"])):
            if d and d not in dirs:
                dirs.append(d)

    for d in dirs:
        c = find_image(d, ARTIST_ART_NAMES + [clean_name, obj["name"]])
        if c:
            return c
        if norm(os.path.basename(d)) in targets:
            c = find_image(d, FOLDER_ART_NAMES)
            if c:
                return c

    return ""


def artist_covers(objs, cfg):
    return {o["id"]: artist_local_cover(o) for o in objs}


def build_entities(tracks):
    albums, artists = {}, {}
    for t in tracks:
        a = albums.setdefault(t["dir"], {
            "kind": "album", "id": short_id(t["dir"]),
            "name": t.get("album") or Path(t["dir"]).name,
            "artist": t.get("artist", ""), "tracks": [],
        })
        a["tracks"].append(t)
        if t.get("artist"):
            k = norm(t["artist"])
            r = artists.setdefault(k, {
                "kind": "artist", "id": short_id("artist:" + k),
                "name": t["artist"], "tracks": [],
            })
            r["tracks"].append(t)
    for a in albums.values():
        a["tracks"].sort(key=lambda t: (t.get("disc", 0), t.get("track", 0), t["path"].lower()))
    for r in artists.values():
        r["tracks"].sort(key=lambda t: (norm(t.get("album", "")), t.get("disc", 0), t.get("track", 0), t["path"].lower()))
    return albums, artists


def name_score(q, words, text, threshold):
    n = norm(text)
    if not n or not words:
        return 0.0
    if all(w in n for w in words):
        score = 1.0
        if n == q:
            score += 3.0
        elif n.startswith(q):
            score += 1.5
        elif q in n:
            score += 0.5
        return score
    qc, nc = q.replace(" ", ""), n.replace(" ", "")
    if qc and qc in nc:
        return 1.0
    if qc:
        best = 0.0
        for target in (nc, nc[: len(qc)]):
            sm = difflib.SequenceMatcher(None, qc, target)
            if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold:
                best = max(best, sm.ratio())
        if best >= threshold:
            return best
    return 0.0


def search(tracks, query, cfg):
    q = norm(query)
    words = q.split()
    th = cfg["typo_threshold"]
    albums, artists = build_entities(tracks)
    found = {"song": [], "album": [], "artist": []}

    for t in tracks:
        s = name_score(q, words, t["title"], th)
        if not s:
            s2 = name_score(q, words, " ".join([t["title"], t.get("artist", ""), t.get("album", "")]), th)
            s = s2 - 0.5 if s2 else 0.0
        if s > 0:
            found["song"].append((s, t))
    for a in albums.values():
        s = name_score(q, words, a["name"], th)
        if s:
            found["album"].append((s, a))
    for r in artists.values():
        s = name_score(q, words, r["name"], th)
        if s:
            found["artist"].append((s, r))

    merged = []
    for kind, lst in found.items():
        lst.sort(key=lambda x: -x[0])
        merged += [(s, kind, obj) for s, obj in lst[: cfg["max_per_type"]]]
    merged.sort(key=lambda x: -x[0])
    return merged[: cfg["max_results"]]


def resolve(tracks, kind, eid):
    if kind == "song":
        return [t for t in tracks if t["id"] == eid]
    albums, artists = build_entities(tracks)
    pool = albums if kind == "album" else artists
    for obj in pool.values():
        if obj["id"] == eid:
            return obj["tracks"]
    return []


COMMANDS = [
    ("Pause / Resume", "Toggle playback", "control", ["/playpause"], ICON_PLAYPAUSE,
     ["pause", "play", "resume", "toggle"]),
    ("Next track", "Skip to the next track", "control", ["/next"], ICON_NEXT,
     ["next", "skip", "forward"]),
    ("Previous track", "Go back to the previous track", "control", ["/prev"], ICON_PREVIOUS,
     ["previous", "prev", "back"]),
    ("Stop", "Stop playback", "control", ["/stop"], ICON_STOP,
     ["stop"]),
    ("Rescan music folder", "Rescan {dir}", "reindex", [], ICON_RESCAN,
     ["rescan", "reindex", "rebuild", "scan"]),
    ("Radio", "Queue a radio mix based on the current song", "Flow.Launcher.ChangeQuery", ["radio"], ICON_RADIO,
     ["radio"]),
    ("Similar", "Queue songs similar to the current song", "Flow.Launcher.ChangeQuery", ["similar"], ICON_SIMILAR,
     ["similar", "sim"]),
    ("Random track", "Play a random track from foobar's playlist", "control", ["/rand"], ICON_NEXT,
     ["random", "rand", "shuffle"]),
    ("Playlists", "Your foobar2000 playlists", "Flow.Launcher.ChangeQuery", ["playlist"], ICON_ALBUM,
     ["playlist", "playlists", "pl"]),
]


class FoobarBar(FlowLauncher):
    def __init__(self):
        self.cfg = load_config()
        SETTINGS.update(self.cfg)
        super().__init__()

    def item(self, title, sub, method, params, icon=ICON, context=None, keep_open=False, score=0):
        r = {
            "Title": title,
            "SubTitle": sub,
            "IcoPath": icon or ICON,
            "Score": score,
            "JsonRPCAction": {"method": method, "parameters": params, "dontHideAfterAction": keep_open},
        }
        if context:
            r["ContextData"] = context
        return r

    def pick_item(self, title, sub, kind, eid, icon, score=0):
        if self.cfg.get("ask_play_mode", True):
            return self.item(title, sub, "Flow.Launcher.ChangeQuery", ["%s >>%s:%s" % (action_keyword(), kind, eid), True],
                             icon=icon, context=[kind, eid], keep_open=True, score=score)
        return self.item(title, sub, "play", [kind, eid, "now"], icon=icon, context=[kind, eid], score=score)

    def command_items(self, q):
        out = []
        for i, (title, sub, method, params, icon, names) in enumerate(COMMANDS):
            if q == "" or any(n.startswith(q) for n in names):
                keep_open = method == "Flow.Launcher.ChangeQuery"
                if keep_open:  # opens "<keyword> radio" / "<keyword> similar"
                    params = ["%s %s" % (action_keyword(), params[0]), True]
                out.append(self.item(title, sub.replace("{dir}", self.cfg["music_dir"]), method, params,
                                     icon=icon_path(icon), keep_open=keep_open, score=100000 - i * 1000))
        return out

    def query(self, query):
        try:
            return self._query(query)
        except Exception as e:
            tb = traceback.format_exc()
            try:
                ERROR_LOG.write_text(tb, encoding="utf-8")
            except Exception:
                pass
            return [self.item("Foobar Control error: %s" % type(e).__name__,
                              (str(e) or tb.strip().splitlines()[-1])[:200] + "  (see error.log)",
                              "noop", [])]

    def _query(self, query):
        q = (query or "").strip()
        if not os.path.exists(self.cfg["foobar_path"]):
            return [self.item("foobar2000 not found",
                              "Not found automatically. Press Enter to open config.json and set foobar_path.",
                              "open_config", [])]
        if q.startswith(">>"):
            return self.mode_step(q[2:])
        if q == "" or (" " not in q and norm(q) in NOW_NAMES):
            clear_temp()
            now = self.now_playing_items()
            return now + (self.command_items("") if q == "" else [])

        first, _, rest = q.partition(" ")
        if first.lower() in PLAYLIST_WORDS:
            clear_temp()
            return self.playlist_items(rest.strip())

        results = self.command_items(norm(q)) if " " not in q else []

        clear_temp()
        data = get_index(self.cfg)
        if not data["tracks"]:
            if data.get("building"):
                note = self.item("Indexing your music library...",
                                 "First run only, it can take a few minutes. Search again in a moment.", "noop", [])
            else:
                note = self.item("No music found in " + self.cfg["music_dir"],
                                 "Press Enter to open config.json and set music_dir to your music folder.",
                                 "open_config", [])
            return results + [note]
        if norm(q) in ("radio", "similar"):
            results = [r for r in results if r["Title"] not in ("Radio", "Similar")]
            results = self.current_mix_items(norm(q), data["tracks"]) + results
        hits = search(data["tracks"], q, self.cfg)
        artist_pics = artist_covers([o for _s, k, o in hits if k == "artist"], self.cfg)
        for s, kind, obj in hits:
            if kind == "song":
                sub = "Song  -  " + " - ".join(x for x in [obj.get("artist", ""), obj.get("album", "")] if x)
                results.append(self.pick_item(obj["title"], sub, "song", obj["id"], cover_for(obj) or icon_path(ICON_SONG), int(s * 100)))
            elif kind == "album":
                sub = "Album  -  %s%d tracks" % ((obj["artist"] + "  -  ") if obj["artist"] else "", len(obj["tracks"]))
                results.append(self.pick_item(obj["name"], sub, "album", obj["id"], album_cover(obj) or icon_path(ICON_ALBUM), int(s * 100)))
            else:
                results.append(self.pick_item(obj["name"], "Artist  -  %d tracks" % len(obj["tracks"]),
                                              "artist", obj["id"], artist_pics.get(obj["id"]) or icon_path(ICON_ARTIST), int(s * 100)))
        results += self.playlist_matches(q)
        if not results:
            results.append(self.item("No match", "Nothing found for '%s'. Type '%s rescan' if you added music." % (q, action_keyword()),
                                     "noop", []))
        return results

    def mode_step(self, state):
        kind, _, eid = state.partition(":")
        clear_temp()
        if kind == "playlist":
            return self.playlist_mode_step(eid)
        tracks = resolve(get_index(self.cfg)["tracks"], kind, eid)
        if not tracks:
            return [self.item("Not found", "Try '%s rescan'" % action_keyword(), "noop", [])]
        first = tracks[0]
        if kind == "song":
            label, icon = "Song: " + first["title"], cover_for(first)
        elif kind == "album":
            label = "Album: " + (first.get("album") or Path(first["dir"]).name)
            icon = album_cover({"tracks": tracks})
        else:
            label = "Artist: " + first.get("artist", "")
            icon = artist_local_cover({"id": short_id("artist:" + norm(first.get("artist", ""))),
                                       "name": first.get("artist", ""), "tracks": tracks})
        icon = icon or icon_path({"song": ICON_SONG, "album": ICON_ALBUM}.get(kind, ICON_ARTIST))
        return [
            self.item("Play now", label, "play", [kind, eid, "now"], icon=icon, score=SC_NOW),
            self.item("Play next (after the current track)", label, "play", [kind, eid, "next"], icon=icon,
                      score=SC_NEXT),
        ] + (self.mix_buttons(eid) if kind == "song" else [])

    def current_track(self, key):
        """The library track matching foobar's window title (cached by title, so the library
        index is only loaded when the song changes)."""
        t = load_now_cache(key)
        if t is not None:
            return t
        t = match_now_playing([key], get_index(self.cfg)["tracks"])
        if t is not None:
            save_now_cache(key, t)
        return t

    def now_playing_items(self):
        """The 'Now playing' result shown first for `bar`: song, artist, album and its cover.
        Enter = pause / resume. Shift+Enter = next, previous, Radio, Similar."""
        running = foobar_running()
        if running is None:  # not Windows: can't read foobar's window
            return []
        if running is False:
            clear_queue()
            return [self.item("foobar2000 is not running", "Pick a song below and it will start for you.",
                              "noop", [], icon=icon_path(ICON_SONG), score=300000)]
        raw, clean = playing_window_title()
        if raw is None:
            return [self.item("Nothing playing", "Press Enter to start playback.",
                              "control", ["/playpause"], icon=icon_path(ICON_PLAYPAUSE), score=300000)]
        t = self.current_track(raw)
        if t is None:  # playing something that isn't in the indexed library
            return [self.item(clean, "Now playing  -  press Enter to pause / resume",
                              "control", ["/playpause"], icon=icon_path(ICON_SONG), score=300000)]
        sub = "Now playing  -  " + (" - ".join(x for x in [t.get("artist", ""), t.get("album", "")] if x)
                                    or "press Enter to pause / resume")
        upcoming = pop_queue_until(t["path"])  # this song has started: drop it from the queue record
        if upcoming:
            sub += "   |   Next: " + upcoming[0]["l"]
        return [self.item(t["title"], sub, "control", ["/playpause"],
                          icon=cover_for(t) or icon_path(ICON_SONG), context=["now", t["id"]], score=300000)]

    def context_menu(self, data):
        kind, eid = data[0], data[1]
        if kind == "now":  # menu of the 'Now playing' result
            return [
                self.item("Pause / Resume", "Toggle playback", "control", ["/playpause"], icon=icon_path(ICON_PLAYPAUSE),
                          score=SC_NOW),
                self.item("Next track", "Skip to the next track", "control", ["/next"], icon=icon_path(ICON_NEXT),
                          score=SC_NEXT),
                self.item("Previous track", "Go back to the previous track", "control", ["/prev"],
                          icon=icon_path(ICON_PREVIOUS), score=SC_THIRD),
                self.item("Radio", "Queue a radio mix like this song", "mix", ["radio", eid, "current"],
                          icon=icon_path(ICON_RADIO), score=SC_FOURTH),
                self.item("Similar", "Queue songs similar to this song", "mix", ["similar", eid, "current"],
                          icon=icon_path(ICON_SIMILAR), score=SC_FIFTH),
            ]
        if kind == "playlist":
            return self.playlist_buttons(eid, "Playlist")
        return [
            self.item("Play now", "Skip to it right away", "play", [kind, eid, "now"], score=SC_NOW),
            self.item("Play next (after the current track)", "Add to the playback queue only",
                      "play", [kind, eid, "next"], score=SC_NEXT),
        ] + (self.mix_buttons(eid) if kind == "song" else [])

    def foobar(self, *args):
        # When foobar2000 isn't running, this very process BECOMES the player and only exits when
        # you close foobar: waiting for it (as before) blocked every command sent after it.
        p = subprocess.Popen([self.cfg["foobar_path"], *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        try:
            p.wait(timeout=float(self.cfg.get("command_timeout", 8)))
        except subprocess.TimeoutExpired:
            pass

    def ensure_foobar(self):
        """If foobar2000 isn't running, start it and wait until it is ready, so the commands
        that follow aren't lost while it starts up (and resumes its last song)."""
        if foobar_running() is not False:  # running, or can't tell (not Windows)
            return
        clear_queue()  # a fresh foobar starts with an empty queue
        try:
            subprocess.Popen([self.cfg["foobar_path"]], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        except Exception:
            return
        deadline = time.time() + float(self.cfg.get("startup_timeout", 30))
        while time.time() < deadline:
            time.sleep(0.25)
            if foobar_running():
                break
        time.sleep(float(self.cfg.get("startup_delay", 3)))

    def control(self, flag):
        self.foobar(flag)

    def play(self, kind, eid, mode):
        """mode: now | next | shuffle (shuffle = play now, in random order)."""
        if kind == "playlist":
            entries = self.playlist_entries(eid, shuffle=(mode == "shuffle"))
        else:
            entries = [(t["path"], track_label(t), t.get("ntitle") or norm(t.get("title", "")))
                       for t in resolve(get_index(self.cfg)["tracks"], kind, eid)]
        if not entries:
            return
        self.ensure_foobar()
        prior = len(load_queue())
        self.queue_files(entries)
        if mode in ("now", "shuffle"):
            self.play_now(entries[0][2], prior)

    def wait_playing(self, expect, timeout):
        """'ok' once foobar's title shows the expected song, 'wrong' if it shows something
        else when time is up, 'unknown' if foobar's title can't be read."""
        deadline = time.time() + timeout
        while True:
            raw, clean = playing_window_title()
            if raw is None:
                return "unknown"
            if (" %s " % expect) in (" %s " % norm(clean)):
                return "ok"
            if time.time() >= deadline:
                return "wrong"
            time.sleep(0.15)

    def play_now(self, expect, prior):
        """Skip to the song that was just queued. foobar2000 needs a moment to register the
        queue, and with Smart Shuffle (or older queued songs) the first Next can land on another
        song, so the plugin checks what is playing and presses Next again if needed."""
        time.sleep(float(self.cfg.get("queue_settle_delay", 0.4)))
        self.foobar("/next")
        if not expect or len(expect) < 2:
            return
        retries = min(int(self.cfg.get("play_now_retries", 2)), 1 + prior)
        for attempt in range(retries + 1):
            if self.wait_playing(expect, float(self.cfg.get("play_verify_timeout", 2.0))) != "wrong":
                return
            if attempt < retries:
                self.foobar("/next")

    def playlist_entries(self, pid, shuffle=False):
        """[(path, label, normalised title)] of a foobar playlist, only songs that still exist."""
        pl = next((x for x in list_playlists(self.cfg) if x["id"] == pid), None)
        if pl is None:
            return []
        files = [f for f in playlist_files(pl["path"], self.cfg) if os.path.isfile(f)]
        if shuffle:
            random.shuffle(files)
        by_path = {os.path.normcase(t["path"]): t for t in get_index(self.cfg)["tracks"]}
        out = []
        for f in files[: int(self.cfg.get("playlist_queue_limit", 500))]:
            t = by_path.get(os.path.normcase(f))
            out.append((f, track_label(t) if t else Path(f).stem,
                        (t.get("ntitle") or norm(t.get("title", ""))) if t else ""))
        return out

    def playlist_cover(self, files):
        for f in files[:3]:
            c = cover_for({"path": f, "dir": os.path.dirname(f)})
            if c:
                return c
        return ""

    def playlist_items(self, flt):
        """`bar playlist [name]`: every foobar2000 playlist, each a suggestion."""
        pls = list_playlists(self.cfg)
        if not pls or flt.lower() == "debug":
            where = playlists_dir(self.cfg) or ", ".join(foobar_profile_dirs(self.cfg)) or "foobar2000's profile folder"
            return [self.item("No foobar2000 playlists found" if not pls else "Playlist diagnostic",
                              "Looked in %s. Press Enter to open a diagnostic file you can send to the developer." % where,
                              "open_debug", [], icon=icon_path(ICON_ALBUM))]
        words = norm(flt).split()
        out = []
        for i, pl in enumerate(pls):
            if words and not all(w in norm(pl["name"]) for w in words):
                continue
            files = playlist_files(pl["path"], self.cfg)
            out.append(self.pick_item(pl["name"], "Playlist  -  %d track%s" % (len(files), "" if len(files) == 1 else "s"), "playlist", pl["id"],
                                      self.playlist_cover(files) or icon_path(ICON_ALBUM), 50000 - i))
        return out or [self.item("No playlist matches", "Nothing named '%s' in foobar2000." % flt, "noop", [])]

    def playlist_matches(self, q):
        """Playlists whose name matches a normal search (a few, below songs / albums / artists)."""
        words = norm(q).split()
        if not words:
            return []
        out = []
        for pl in list_playlists(self.cfg):
            if all(w in norm(pl["name"]) for w in words):
                out.append(self.pick_item(pl["name"], "Playlist", "playlist", pl["id"], icon_path(ICON_ALBUM), 90))
            if len(out) >= 3:
                break
        return out

    def playlist_buttons(self, pid, label, icon=None):
        icon = icon or icon_path(ICON_ALBUM)
        return [
            self.item("Play now", label, "play", ["playlist", pid, "now"], icon=icon, score=SC_NOW),
            self.item("Play next (after the current track)", label, "play", ["playlist", pid, "next"], icon=icon,
                      score=SC_NEXT),
            self.item("Play now, shuffled", label, "play", ["playlist", pid, "shuffle"], icon=icon_path(ICON_RADIO),
                      score=SC_THIRD),
        ]

    def playlist_mode_step(self, pid):
        pl = next((x for x in list_playlists(self.cfg) if x["id"] == pid), None)
        if pl is None:
            return [self.item("Playlist not found", "foobar may have renamed or deleted it.", "noop", [])]
        icon = self.playlist_cover(playlist_files(pl["path"], self.cfg)) or icon_path(ICON_ALBUM)
        return self.playlist_buttons(pid, "Playlist: " + pl["name"], icon)

    def mix_buttons(self, sid):
        return [
            self.item("Radio", "Play this song now, then queue a radio mix like it",
                      "mix", ["radio", sid, "now"], icon=icon_path(ICON_RADIO), score=SC_THIRD),
            self.item("Similar", "Play this song now, then queue similar songs",
                      "mix", ["similar", sid, "now"], icon=icon_path(ICON_SIMILAR), score=SC_FOURTH),
        ]

    def current_mix_items(self, kind, tracks):
        """The result shown for 'radio' / 'similar': based on the song playing right now."""
        label = "Radio" if kind == "radio" else "Similar"
        icon = icon_path(ICON_RADIO if kind == "radio" else ICON_SIMILAR)
        seed = match_now_playing(now_playing_titles(), tracks)
        if seed is None:
            return [self.item("%s: can't tell which song is playing" % label,
                              "Play a song in foobar2000 first, or search a song and use its Radio / Similar buttons.",
                              "noop", [], icon=icon, score=200000)]
        who = " - ".join(x for x in [seed.get("artist", ""), seed["title"]] if x)
        own_command = (self.cfg.get(kind + "_command") or "").strip()
        n = 1 if own_command else sum(1 for c in tracks if c["path"] != seed["path"] and similarity(seed, c) > 0)
        if not n:
            return [self.item("%s: no songs like %s" % (label, who),
                              "This song has no artist or genre tag to compare with.",
                              "noop", [], icon=icon, score=200000)]
        if own_command:
            sub = "Run foobar's '%s' on the current song" % own_command
        else:
            size = self.cfg["radio_size"] if kind == "radio" else self.cfg["similar_size"]
            sub = "Queue %d songs after the current track (%d match)" % (min(n, size), n)
        return [self.item("%s: %s" % (label, who), sub, "mix", [kind, seed["id"], "current"],
                          icon=cover_for(seed) or icon, score=200000)]

    def queue_files(self, entries):
        """entries: [(path, label)]. Adds them to foobar's playback queue and remembers them."""
        files = [e[0] for e in entries]
        for i in range(0, len(files), 40):
            self.foobar("/context_command:" + self.cfg["queue_command"], *files[i:i + 40])
        record_queue([(e[0], e[1]) for e in entries])

    def mix(self, kind, sid, mode):
        """kind: radio | similar. mode: 'current' = the song is already playing, only queue
        the mix; 'now' = play this song first, then the mix."""
        data = get_index(self.cfg)
        seed = next((t for t in data["tracks"] if t["id"] == sid), None)
        if seed is None:
            return
        self.ensure_foobar()
        command = (self.cfg.get(kind + "_command") or "").strip()
        if command:  # a foobar command of your own, see the README
            if mode == "current":
                self.foobar("/playing_command:" + command)
            else:
                self.foobar("/context_command:" + command, seed["path"])
            return
        picks = build_mix(kind, seed, data["tracks"], self.cfg)
        if mode == "now":
            picks.insert(0, seed)
        files = [(t["path"], track_label(t), t.get("ntitle") or norm(t.get("title", ""))) for t in picks]
        if not files:
            return
        prior = len(load_queue())
        self.queue_files(files)
        if mode == "now":
            self.play_now(files[0][2], prior)

    def reindex(self):
        start_background_build()

    def build_now(self):  # runs in the detached background process
        try:
            build_index(self.cfg)
        except Exception:
            try:
                ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
            except Exception:
                pass
        finally:
            try:
                LOCK_PATH.unlink()
            except OSError:
                pass

    def open_debug(self):
        try:
            os.startfile(str(write_playlist_debug(self.cfg)))
        except Exception:
            pass

    def open_config(self):
        load_config()
        try:
            os.startfile(str(CONFIG_PATH))
        except Exception:
            pass

    def noop(self):
        pass


if __name__ == "__main__":
    FoobarBar()
