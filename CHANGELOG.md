# Changelog

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
