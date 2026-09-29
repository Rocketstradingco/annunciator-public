# Android app

The app (`io.annunciator.dashboard`) is a Capacitor wrapper around the same
`web/` client a browser loads, plus three native pieces: background alerts, an
update installer and Back-button handling. It ships with **no server address
and no key**; each person enters their own.

## Finding the server

A browser uses the server that served the page. The app has no such origin, so
it reads addresses from **Settings**:

| Setting | Meaning |
| --- | --- |
| Server address | Your dashboard, e.g. `http://192.0.2.20:18160` on your LAN. |
| Fallback address | Optional second route to the same server, e.g. through a VPN or reverse proxy (`https://dashboard.example.com`). |

Each refresh tries the address that last worked first, then the other; the one
that answers is remembered on the device. If neither answers, the app keeps
showing the last reading with a banner saying how old it is.

For your own build you can pre-fill both in `web/config.js`
(`window.ANNUNCIATOR_CONFIG = { server: '', remoteServer: '' }`). Leave them
blank in anything you share, and never put a key there: the file is bundled
into the APK. The audit fails if `server` is not blank.

The app allows cleartext HTTP (`androidScheme: http`, `cleartext: true`)
because most home servers have no certificate. Use HTTPS through a reverse
proxy when the route leaves your own network ([Security](security.md)).

## Controls

Enter the control key in Settings; it is kept in the app's local storage on
that phone only. Destructive actions need a second tap within four seconds.

## Notifications

When alerts are enabled in Settings, the app hands its server addresses to
`AlertsWorker`, an Android WorkManager job that runs about every 15 minutes
(Android's minimum; the OS may delay it further) while a network is available:

1. It asks each address in turn for `GET /api/events?since=<last seen id>`.
2. The first successful contact only records the newest event ID, so old
   events do not arrive as a burst.
3. After that, each new `crit` or `warn` event, and each `ok` recovery of a
   machine or service, becomes a notification in the "System alerts" channel.
   Tapping one opens the app.

Android 13 and newer ask for notification permission when you enable alerts.
Turning alerts off cancels the job. An alert can trail the event by the
worker's interval; real-time paging is not a goal.

## Updates

You can serve your own builds to your phones:

1. Bump `version` and `versionCode` in `package.json` (the Gradle build reads
   both; `versionCode` must increase).
2. Build and sign the APK (below).
3. Copy it to `updates/annunciator.apk` on the server and write
   `updates/release.json`:

   ```json
   {"versionCode": 4, "versionName": "0.3.1", "notes": "What changed"}
   ```

In Settings, **Check for updates** calls `GET /api/update`. When
`versionCode` is greater than the installed one, the app offers the update;
installing downloads `GET /api/update/apk` into its private cache (plain
HTTP(S), no redirects, at most 100 MB) and opens Android's installer, which
asks you to confirm. Android only accepts an update signed with the same key
as the installed app, so keep your signing key.

`updates_dir` moves the folder; nothing is offered unless both files exist.

## Building

Requirements: Node.js 22+, JDK 21 and the Android SDK (platform 36 and the
build tools the Gradle files name). Point Gradle at the SDK with
`ANDROID_HOME`/`ANDROID_SDK_ROOT` or an ignored `android/local.properties`.

```sh
npm ci
npm run sync                              # copies web/ into the Android project
cd android && ./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
```

`make apk` does the same. A debug APK is for testing. To distribute, create
your own signing key outside this folder, add a release signing configuration
through Gradle properties or environment variables (never commit the key), and
run `./gradlew assembleRelease`.

The minimum Android version is 7.0 (API 24); the target is API 36.

## Using your own identity

The package ID `io.annunciator.dashboard` is this project's. If you publish a
fork, change it together in `capacitor.config.json` (`appId`), the Gradle
`namespace`/`applicationId`, the Java package folders and declarations, and the
identity checks in `tools/audit_public.py`. See [Customization](customization.md).

## Testing on a device

Emulators and `tools/ui_preview.py` cover layout. Notifications, the
installer, Back behaviour, insets and battery-saver scheduling need a real
phone; test them there before relying on them.
