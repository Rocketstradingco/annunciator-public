# Customization

## Name, subtitle and colour

Set `branding.name`, `branding.subtitle` and `branding.accent` (`#RRGGBB`) in
`config/local.json`, or answer the wizard's first questions. They change the
header, the page title and the Overview label for every client. Status colours
(up, warning, down) keep their meaning regardless of the accent.

Each device chooses System, Light or Dark in Settings.

## Behaviour

Intervals, timeouts, history lengths, alert thresholds and the client's refresh
rate are configuration keys; see the [configuration reference](configuration.md).
Common changes:

```jsonc
{
  "interval_s": 30,                                   // probe less often
  "thresholds": { "disk_warn": 0.85, "disk_crit": 0.95 },
  "telemetry": { "interval_s": 60, "history": 120 },  // two hours of CPU history
  "ui": { "poll_s": 10 }
}
```

## Icons and fonts

Replace `web/icons/` and the Android mipmaps under
`android/app/src/main/res/` for your own icon, or edit and run
`tools/make_icons.py` (needs Pillow). The bundled fonts are under the SIL Open
Font License; keep their licence files beside them.

## Your own Android build

`web/config.js` can pre-fill server addresses for a build you give to your own
household. Leave it blank in anything you share and never put a key in it.

To publish your own app rather than a build of this one, choose your own
package ID and change it everywhere at once: `capacitor.config.json`
(`appId`), `android/app/build.gradle` (`namespace`, `applicationId`), the Java
package declarations and folder (`android/app/src/main/java/...`), the app name
in `android/app/src/main/res/values/strings.xml` and the identity checks in
`tools/audit_public.py`. The FileProvider authority follows the application ID
automatically. Change `appName` and the names in `web/manifest.webmanifest` and
`web/index.html` for the display name.

`package.json` is the single version source: the server, the service worker
cache name and the Android `versionName`/`versionCode` all read it. Keep
`pyproject.toml`'s version in step (a test checks this).

Signing, building and serving updates: [Android](android.md).

## Licence

The source is MIT licensed (`LICENSE`); bundled fonts keep their OFL notices.
