<div align="center">

<img src="icon.png" width="96" alt="Foobar Control icon">

# Foobar Control

**Control foobar2000 and play songs, albums and artists from your own music library, straight from [Flow Launcher](https://www.flowlauncher.com/).**

Type `bar`, start typing a name, press Enter. Includes **Radio** and **Similar** mixes built from your own library.

[Download](../../releases/latest) · [Install](#install) · [Usage](#using-it) · [Settings](#settings) · [Troubleshooting](#troubleshooting)

</div>

---

## Features

- **Now playing, first in the list:** typing `bar` shows the song playing in foobar2000 at the top, with its title, artist, album and **cover**. Enter pauses / resumes; Shift+Enter gives Next, Previous, Radio and Similar for that song.
- **Next in the queue:** the Now playing result also shows the next song you queued through the plugin.
- **Your foobar2000 playlists:** `bar playlist` lists them all; pick one to play it, play it shuffled, or queue it next.
- **Fast rescans:** only new or changed files are re-read, so updating the index takes seconds.
- **Playback controls:** pause / resume, next, previous, stop.
- **Instant library search** for songs, albums and artists, with cover art or artist pictures on every result.
- **Forgiving search:** ignores capitals, accents and punctuation (`beyonce halo` finds "Beyoncé - Halo") and tolerates a small typo in longer searches.
- **Play now** or **Play next**, using foobar's playback queue, so your playlist is never modified.
- **Radio** and **Similar:** queue a mix of songs like the one you're hearing, built from your library's tags.
- **Starts foobar2000 for you** if it's closed, then plays your choice.
- **Zero setup:** finds foobar2000 and your Music folder by itself, and bundles its two small libraries.

## Requirements

- Windows
- [Flow Launcher](https://www.flowlauncher.com/)
- [foobar2000](https://www.foobar2000.org/)
- Python 3.10 or newer (Flow Launcher can set one up for you, or install it from python.org and set its path in Flow's settings)

Nothing else to install: [mutagen](https://github.com/quodlibet/mutagen) and the Flow Launcher Python helper are bundled in the `lib` folder.

## Install

1. Download **`FoobarControl-x.y.z.zip`** from the [latest release](../../releases/latest).
2. **With Flow Launcher's own installer:** install the zip as it is, without unzipping it, then restart Flow Launcher.

   **Manually:** unzip it into a new folder named `FoobarControl` inside `%APPDATA%\FlowLauncher\Plugins\` (portable Flow Launcher: its `UserData\Plugins` folder), so that `plugin.json` ends up directly in that folder, then restart Flow Launcher.
3. Type `bar`.

> Do **not** use GitHub's green "Code → Download ZIP" button: that zip has the plugin one folder too deep. Use the file from the Releases page.

### First run

There is nothing to configure. The plugin finds foobar2000 and your Windows Music folder by itself, then scans your music in the background the first time you search. You'll see *"Indexing your music library..."* until it's done (a few minutes for a very large library); after that, results are instant. The library is rescanned automatically every 24 hours.

## Using it

| You type | You get |
|---|---|
| `bar` | **The song playing now (with its cover)**, then Pause / Resume, Next, Previous, Stop, Rescan, Radio, Similar, Random, always in that order |
| `bar now` (or `np`, `playing`) | Only the song playing now |
| `bar playlist` | Every foobar2000 playlist, as suggestions |
| `bar playlist chill` | Only the playlists whose name contains "chill" |
| `bar pause`, `bar next`... | The matching command |
| `bar <name>` | Matching songs, albums and artists, each with its cover or picture |
| `bar radio` | A radio mix based on the song playing right now |
| `bar similar` | Songs similar to the song playing right now |
| `bar rescan` | Rescan your music folder (after adding music) |

On the **Now playing** result, **Enter** pauses / resumes. The song and its cover are found by matching foobar's window title with your indexed library, so the song must be inside `music_dir` and foobar's title must contain its title (the default does). A song outside the library is still shown, with a default icon.

Press **Enter** on a result, then choose **Play now** or **Play next (after the current track)**. For a **song** there are two more buttons: **Radio** and **Similar**, which play that song now and then queue songs like it. Shift+Enter (or the right arrow) on a result shows the same choices directly.

Playback uses foobar's playback queue, so your playlist is never modified and foobar returns to it when the queued tracks end.

Covers come from the tags in your files. Artists use an embedded artist picture, a foobar artist image, or an `artist.jpg` in the artist's folder. When a result has none, it shows a default icon.

## Next in the queue

The Now playing result adds `Next: Artist - Title` when you have queued songs. foobar2000 cannot report its queue to other programs without an extra component, so the plugin keeps its own record of **what it queued** (Play next, albums, playlists, Radio, Similar) and forgets each song as it starts playing. This means:

- Songs you add to foobar's queue by hand are not known to the plugin.
- If you open `bar` rarely, the record catches up the next time the current song is one of the queued ones.
- The record is emptied when foobar2000 is closed (its queue is empty again on the next start) and after 12 hours.

## Playlists

`bar playlist` shows the playlists foobar2000 has saved, each with its track count and the cover of its first songs. Press Enter, then choose **Play now**, **Play now, shuffled** or **Play next**. Playlists are queued like albums (the playlist itself is never changed), at most `playlist_queue_limit` songs (500). A playlist whose name matches what you type also shows up in a normal `bar <name>` search.

The plugin reads foobar's own playlist files (`.fpl`) in its profile folder (`%APPDATA%\foobar2000-v2\playlists-v2.0`, or the `profile` folder of a portable install). That format is closed, so the plugin only takes out the file paths and the names from foobar's playlist index. Things to know:

- foobar saves its playlists when it closes and after changes, so a playlist you just created may show up a little later.
- A playlist whose name can't be read is shown as "Playlist (starts with ...)".
- Songs that no longer exist on disk are skipped; auto-playlists and playlists of streams or CDs are not supported.

## Radio and Similar

These are built by the plugin from **your own library's tags**; they are not the Gatefold feature of the same name (that one runs inside Gatefold's own window and can't be started from outside).

- A song counts as related when it has the **same main artist or a shared genre** with the starting song. Same album and a close release year rank it higher. Songs without artist and genre tags can't be matched.
- **Similar** queues the best matches (20 by default).
- **Radio** queues a weighted random mix (40 by default, at most 4 songs per artist), so it changes every time.
- For `bar radio` / `bar similar` the plugin works out the current song from foobar's window title (it must contain the song title, as foobar's default title does). If it can't tell, it says so; search the song and use its Radio / Similar buttons instead.

If your foobar has its own commands for this, open *Preferences > Keyboard Shortcuts* in foobar, find the command's name, and put it in `radio_command` / `similar_command` in `config.json`. The plugin then runs that command instead of building a mix itself.

## The keyword

The default keyword is `bar`, and `Bar` and `BAR` work too on a fresh install. Flow Launcher remembers each plugin's keywords, so if you updated an existing install, set them once: *Flow Launcher Settings > Plugins > Foobar Control > Action keyword*, and enter `bar Bar BAR` (separate keywords with spaces). You can use any keyword you like there; the plugin follows it when it re-opens the search after you press Enter. If it ever re-opens with the wrong keyword, put the one you use in `action_keyword` in `config.json`.

## If foobar2000 is closed

Picking a song, album, artist, Radio or Similar while foobar2000 is closed starts foobar2000, waits for it to finish starting (`startup_delay`, 3 seconds), then plays your choice. If foobar2000 is set to resume its last song when it starts, you may hear that song for a moment first.

When foobar2000 is closed and reopened it can resume a different song of your playlist than the one you were hearing: songs played from the playback queue aren't part of a playlist, and foobar2000 rebuilds its shuffle order on every restart. That is foobar2000's own behaviour. To stop it, untick *"Save playback state when closing foobar2000 and resume"* in foobar2000's *Preferences > Playback*; foobar2000 then starts silent and only plays what you choose.

## Settings

Optional. Open `config.json` in the plugin folder (it is created on first run).

| Setting | Meaning |
|---|---|
| `foobar_path` | Empty = automatic. Set it only if foobar2000 is installed somewhere unusual. |
| `music_dir` | Empty = your Windows Music folder. Set it if your music is elsewhere, e.g. `D:\\Music` (use double backslashes). |
| `extensions` | File types that are scanned (`.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, `.wav`, `.wma`, `.aac`, `.ape`, `.wv`). |
| `typo_threshold` | 0.85 by default. Raise it up to 1.0 to be stricter. |
| `ask_play_mode` | `false` = Enter plays immediately, without the Now / Play next choice. |
| `max_results`, `max_per_type` | Most results shown in total (15) and per type: songs, albums, artists (6). |
| `radio_size`, `similar_size`, `radio_per_artist` | How many songs Radio and Similar queue (40, 20) and the most songs of one artist in a Radio mix (4). |
| `radio_command`, `similar_command` | Optional foobar command names to use for Radio / Similar instead (see above). |
| `action_keyword` | Empty = follow Flow Launcher's setting. |
| `startup_delay`, `startup_timeout` | When foobar2000 is closed: seconds to let it finish starting before it is sent commands (3), and the longest wait for its window (30). Raise `startup_delay` if your foobar2000 is slow to start. |
| `command_timeout` | Longest wait, in seconds, for one foobar command to be accepted (8). |
| `cover_mode` | `temp` (default) shows covers through a temp folder emptied on every search. `data_uri` writes nothing at all but needs a recent Flow Launcher. |
| `data_uri_max_kb` | With `data_uri`, covers bigger than this are skipped (400). |
| `queue_command` | The foobar context-menu command used to queue tracks (`Add to playback queue`). |
| `cache_hours` | How often the library is rescanned automatically (24). Rescans only read new or changed files. |
| `index_threads` | Files read at the same time during a scan (8). |
| `playlist_queue_limit` | Most songs queued when playing a foobar playlist (500). |

> `config.json` and the library index live in the plugin folder. If you update by replacing that folder, keep your `config.json` (the index just rebuilds itself).

## Custom icons

Every icon is a PNG in the `icons` folder. Replace a file with your own, keeping the same name, and restart Flow Launcher: `playpause`, `next`, `previous`, `stop`, `rescan`, `shuffle` (Radio), `shuffle_filled` (Similar), and `song`, `album`, `artist` (shown when a result has no cover). The names are listed at the top of `main.py`.

## Troubleshooting

- **"foobar2000 not found":** press Enter on the result to open `config.json` and set `foobar_path`.
- **"No music found":** set `music_dir` in `config.json`.
- **Songs don't play or aren't queued:** check that `queue_command` matches the "Add to playback queue" entry in foobar's right-click menu.
- **Radio / Similar says it can't tell which song is playing:** foobar's window title doesn't contain the song. Search the song and use its Radio / Similar buttons.
- **Added new music:** type `bar rescan` (only the new files are read). For a complete rebuild, delete `index_cache.json` in the plugin folder.
- **`bar playlist` shows nothing or odd names:** the playlists are read from foobar's profile folder (the error message names the folder it looked in). Please [open an issue](../../issues/new/choose) with the first lines of `index.txt` from that folder.
- **Nothing happens when you type `bar`:** restart Flow Launcher, and check that Python is set up in Flow's settings.
- **Any other error** is shown in the results and saved in `error.log` in the plugin folder. Please include it when [opening an issue](../../issues/new/choose).

## Privacy

Everything runs locally. The plugin reads your music folder to build its index (`index_cache.json`, kept in the plugin folder) and sends nothing anywhere.

## Building from source

```
python scripts/build_zip.py
```

This writes `dist/FoobarControl-<version>.zip`, with `plugin.json` at its root, ready to install. Pushing a tag such as `v2.1` (matching the `Version` in `plugin.json`) makes GitHub Actions build the zip and attach it to a release automatically.

To work on the plugin, clone the repo into `%APPDATA%\FlowLauncher\Plugins\FoobarControl` and restart Flow Launcher after each change.

## License

[MIT](LICENSE), © Malka'. The bundled libraries keep their own licenses, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
