# Changelog

## 2.1
- **Next in the queue:** the Now playing result shows `Next: ...` for the songs you queued through the plugin. No extra program needed.
- **Playlists:** `bar playlist` lists your foobar2000 playlists; play one now, shuffled, or next. Matching playlists also appear in normal searches.
- **Faster index:** rescans only read new or changed files (in parallel), and the index file is smaller and loads faster. The first scan after updating still reads everything once.
- New settings: `index_threads`, `playlist_queue_limit`.

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
