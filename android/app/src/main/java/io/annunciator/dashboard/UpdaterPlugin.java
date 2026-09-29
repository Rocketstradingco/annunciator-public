package io.annunciator.dashboard;

import android.Manifest;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Environment;
import android.provider.MediaStore;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSArray;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;
import androidx.core.content.FileProvider;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

@CapacitorPlugin(name = "Updater")
public class UpdaterPlugin extends Plugin {
    @PluginMethod
    public void version(PluginCall call) {
        try {
            PackageInfo info = getContext().getPackageManager().getPackageInfo(getContext().getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
            JSObject result = new JSObject();
            result.put("versionCode", code);
            result.put("versionName", info.versionName);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Could not read app version", error);
        }
    }

    // Background alerts: remember the server addresses for the worker and
    // schedule or cancel it. Android 13+ asks for notification permission here.
    @PluginMethod
    public void configureAlerts(PluginCall call) {
        boolean enabled = Boolean.TRUE.equals(call.getBoolean("enabled", true));
        StringBuilder servers = new StringBuilder();
        JSArray list = call.getArray("servers", new JSArray());
        for (int i = 0; i < list.length(); i++) {
            String base = list.optString(i, "");
            if (base.startsWith("http://") || base.startsWith("https://")) servers.append(base).append('\n');
        }
        getContext().getSharedPreferences(AlertsWorker.PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean("enabled", enabled)
                .putString("servers", servers.toString())
                .apply();
        if (enabled && Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(getContext(), Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(getActivity(), new String[] {Manifest.permission.POST_NOTIFICATIONS}, 7);
        }
        AlertsWorker.schedule(getContext(), enabled);
        call.resolve();
    }

    // Back on the Overview screen: go to the background like the home button,
    // so reopening the app lands where it was instead of restarting.
    @PluginMethod
    public void minimize(PluginCall call) {
        getBridge().executeOnMainThread(() -> {
            getActivity().moveTaskToBack(true);
            call.resolve();
        });
    }

    @PluginMethod
    public void install(PluginCall call) {
        String address = call.getString("url", "");
        if (!(address.startsWith("https://") || address.startsWith("http://"))) {
            call.reject("Invalid update address");
            return;
        }
        new Thread(() -> downloadAndInstall(call, address), "annunciator-update").start();
    }

    private void downloadAndInstall(PluginCall call, String address) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(address).openConnection();
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(30000);
            connection.setInstanceFollowRedirects(false);
            if (connection.getResponseCode() != 200) throw new Exception("Download returned HTTP " + connection.getResponseCode());
            File folder = new File(getContext().getCacheDir(), "updates");
            if (!folder.exists() && !folder.mkdirs()) throw new Exception("Could not create update cache");
            File apk = new File(folder, "annunciator.apk");
            long total = 0;
            try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(apk)) {
                byte[] block = new byte[32768];
                int size;
                while ((size = input.read(block)) != -1) {
                    total += size;
                    if (total > 100_000_000) throw new Exception("Update is too large");
                    output.write(block, 0, size);
                }
            }
            if (total < 1000) throw new Exception("Update download is incomplete");
            Uri uri = FileProvider.getUriForFile(getContext(), getContext().getPackageName() + ".fileprovider", apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            getBridge().executeOnMainThread(() -> {
                try {
                    getActivity().startActivity(intent);
                    call.resolve();
                } catch (Exception error) {
                    call.reject("Could not open Android installer", error);
                }
            });
        } catch (Exception error) {
            call.reject("Could not download update: " + error.getMessage(), error);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }
}
