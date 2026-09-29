# Customization and distribution

## Names, colors and icons

Use the wizard or branding in your local config for name/subtitle/accent. Runtime
branding changes the header, title and overview label. Settings selects light/dark/
system per device. Incident colors retain their meaning.

web/config.js optionally supplies public default Android server URLs. Leave blank
for a reusable build. Never place a key/token there. For your own branded build,
change appName in capacitor.config.json, app labels in Android strings.xml, static
startup labels in web/index.html and names in web/manifest.webmanifest.

Replace web/icons and Android mipmaps for icon branding. Optional
assets/make_icons.py generates the grid icon and requires Pillow. Keep font OFL
notices beside the bundled fonts.

## Android identity and versions

The copy uses io.annunciator.dashboard, a separate generic starter identity.
If choosing another ID, change capacitor.config.json appId, Gradle namespace and
applicationId, Java package declarations/folders, Android package/scheme strings,
and the identity tests together. FileProvider uses ${applicationId} automatically.
Browser storage/cache also uses a public prefix.

package.json is the only version source. Increase versionCode for upgrades.
Build in this folder with npm ci, npm run sync and android/gradlew assembleDebug.
Use your own ANDROID_HOME/ANDROID_SDK_ROOT or ignored android/local.properties.
Install the platforms/build tools listed by the Gradle files.

Debug APKs are for testing. For distribution, generate your own Android signing
key outside this tree, supply it using local Gradle environment/properties, configure
a release signing block and run assembleRelease. Keep that key for future upgrades.
No signing key was copied. Do not offer unsigned files as installable releases.

Test notifications, Back, insets and installation on a real phone before claiming
those work. Source packaging is tools/package_public.py, which does not publish.
This source uses the MIT license; retain the license and bundled font notices.
A new Git repo/remote may be created separately; history/remotes from another app stay out.
