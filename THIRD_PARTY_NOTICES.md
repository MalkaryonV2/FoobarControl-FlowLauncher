# Third-party software

The `lib/` folder bundles two pure-Python libraries so the plugin works with
nothing to install. They keep their own licenses:

| Library | Version | License | Notes |
|---|---|---|---|
| [mutagen](https://github.com/quodlibet/mutagen) | 1.48.1 | GPL-2.0-or-later | Reads audio tags and embedded covers. Unmodified; license text in `lib/mutagen-1.48.1.dist-info/`. |
| [flowlauncher](https://pypi.org/project/flowlauncher/) | 0.2.0 | MIT | Flow Launcher's Python JSON-RPC helper. License in `lib/flowlauncher-0.2.0.dist-info/LICENSE`. |

Because mutagen is GPL-licensed and ships unmodified inside the release zip,
its source is the `lib/mutagen/` folder itself, and you may replace it with
any other copy of mutagen (`pip install mutagen --target lib`).
