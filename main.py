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
INDEX_VERSION = 2  # bump when the index gains fields (it is rebuilt in the background)
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


def touch_lock():
    try:
        os.utime(str(LOCK_PATH), None)
    except OSError:
        pass


def build_index(cfg):
    exts = {e.lower() for e in cfg["extensions"]}
    tracks = []
    for root, _dirs, files in os.walk(cfg["music_dir"]):
        touch_lock()
        for name in files:
            if os.path.splitext(name)[1].lower() in exts:
                p = os.path.join(root, name)
                title, artist, album, track, disc, genre, year = read_tags(p)
                tracks.append({
                    "id": short_id(p),
                    "path": p,
                    "dir": root,
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "track": track,
                    "disc": disc,
                    "hay": norm(" ".join([title, artist, album, Path(p).stem])),
                    "ntitle": norm(title),
                    "genre": genre,
                    "year": year,
                    "nartist": primary_artist(artist),
                    "genres": split_genres(genre),
                    "nalbum": norm(album),
                })
                if len(tracks) % 50 == 0:
                    touch_lock()
    data = {"version": INDEX_VERSION, "built": time.time(), "music_dir": cfg["music_dir"], "tracks": tracks}
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
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
        if q == "":
            return self.command_items("")

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
        if not results:
            results.append(self.item("No match", "Nothing found for '%s'. Type '%s rescan' if you added music." % (q, action_keyword()),
                                     "noop", []))
        return results

    def mode_step(self, state):
        kind, _, eid = state.partition(":")
        clear_temp()
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
            self.item("Play now", label, "play", [kind, eid, "now"], icon=icon),
            self.item("Play next (after the current track)", label, "play", [kind, eid, "next"], icon=icon),
        ] + (self.mix_buttons(eid) if kind == "song" else [])

    def context_menu(self, data):
        kind, eid = data[0], data[1]
        return [
            self.item("Play now", "Skip to it right away", "play", [kind, eid, "now"]),
            self.item("Play next (after the current track)", "Add to the playback queue only",
                      "play", [kind, eid, "next"]),
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
        data = get_index(self.cfg)
        files = [t["path"] for t in resolve(data["tracks"], kind, eid)]
        if not files:
            return
        self.ensure_foobar()
        for i in range(0, len(files), 40):
            self.foobar("/context_command:" + self.cfg["queue_command"], *files[i:i + 40])
        if mode == "now":
            self.foobar("/next")

    def mix_buttons(self, sid):
        return [
            self.item("Radio", "Play this song now, then queue a radio mix like it",
                      "mix", ["radio", sid, "now"], icon=icon_path(ICON_RADIO)),
            self.item("Similar", "Play this song now, then queue similar songs",
                      "mix", ["similar", sid, "now"], icon=icon_path(ICON_SIMILAR)),
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

    def queue_files(self, files):
        for i in range(0, len(files), 40):
            self.foobar("/context_command:" + self.cfg["queue_command"], *files[i:i + 40])

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
        files = [t["path"] for t in build_mix(kind, seed, data["tracks"], self.cfg)]
        if mode == "now":
            files.insert(0, seed["path"])
        if not files:
            return
        self.queue_files(files)
        if mode == "now":
            self.foobar("/next")

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
