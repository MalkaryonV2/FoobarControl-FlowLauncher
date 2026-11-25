# Changelog

## 2.1.1
- **Now playing detection fixed:** it no longer needs "[foobar2000]" in foobar's window title, so custom title formats work, as does foobar hidden in the tray. Dialogs and Windows helper windows are ignored. `bar now debug` writes a diagnostic file.
- **Less disturbance during playback:** starting a song never triggers a library scan, and scans run at the lowest CPU / disk priority with 2 threads (was 8).
- Removed the now-playing cache file (the index is read directly).

## 2.1
- **Next in the queue:** the Now playing result shows `Next: ...` for the songs you queued through the plugin. No extra program needed.
- **Playlists:** `bar playlist` lists your foobar2000 playlists; play one now, shuffled, or next. Matching playlists also appear in normal searches.
- **Faster index:** rescans only read new or changed files (in parallel), and the index file is smaller and loads faster. The first scan after updating still reads everything once.
- **"Play now" fix:** with Smart Shuffle (or older queued songs) foobar could start a random song and leave the chosen one next. The plugin now waits for the queue to register, checks what is playing and presses Next again if needed.
- **Fixed order:** "Play now" is always first and "Play next" second, even if you use another choice more often.
- Playlists: wider search for foobar's profile folder, a fallback for other path formats, playlists with unreadable content are still listed, and `bar playlist debug` writes a diagnostic file.
- New settings: `index_threads`, `playlist_queue_limit`, `queue_settle_delay`, `play_now_retries`, `play_verify_timeout`.

## 2.0
- **Now playing result:** typing `bar` (or `bar now`) shows the current foobar2000 song first, with title, artist, album and its cover. Enter pauses / resumes; Shift+Enter shows Next, Previous, Radio and Similar for it.
- Shows "Nothing playing" or "foobar2000 is not running" when relevant, and still shows songs that are not in your library.
- The current song is remembered between searches, so the library index is only reloaded when the song changes.
- New command: Random track (`bar random`).
- Author: Malka'.

## 1.1.1
- First public, shareable package: the zip now has `plugin.json` at its root, so Flow Launcher can install it as it is.
- No personal data in the package: the library index (`index_cache.json`) and `config.json` are created on your machine on first run.
- Added README, license, third-party notices and automated release builds.
