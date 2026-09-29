package io.annunciator.dashboard;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Checks the server for new monitoring events in the background and turns problems
 * (and recoveries) into notifications. Android runs periodic work at most every
 * 15 minutes, so an alert can trail the event by up to that long.
 */
public class AlertsWorker extends Worker {
    static final String PREFS = "annunciator-alerts";
    static final String CHANNEL = "annunciator-alerts";
    static final String WORK = "annunciator-alerts";

    public AlertsWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context, boolean enabled) {
        WorkManager work = WorkManager.getInstance(context);
        if (!enabled) {
            work.cancelUniqueWork(WORK);
            return;
        }
        Constraints constraints = new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(AlertsWorker.class, 15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build();
        work.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE, request);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context context = getApplicationContext();
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (!prefs.getBoolean("enabled", true)) return Result.success();
        long since = prefs.getLong("last", 0);
        for (String base : prefs.getString("servers", "").split("\n")) {
            if (base.isEmpty()) continue;
            try {
                JSONObject reply = fetch(base + "/api/events?since=" + since);
                long latest = reply.optLong("latest", since);
                // First contact only sets the baseline; old events are not news.
                if (since > 0) {
                    JSONArray events = reply.getJSONArray("events");
                    for (int i = 0; i < events.length(); i++) notifyFor(context, events.getJSONObject(i));
                }
                prefs.edit().putLong("last", Math.max(since, latest)).apply();
                return Result.success();
            } catch (Exception ignored) {
                // Unreachable on this route (unreachable network): try the next one.
            }
        }
        return Result.success();
    }

    private static JSONObject fetch(String address) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(8000);
        try (InputStream input = connection.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] block = new byte[8192];
            int size;
            while ((size = input.read(block)) != -1) out.write(block, 0, size);
            return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
        } finally {
            connection.disconnect();
        }
    }

    private static void notifyFor(Context context, JSONObject event) {
        String severity = event.optString("severity");
        String kind = event.optString("kind");
        boolean recovery = "ok".equals(severity) && ("machine".equals(kind) || "service".equals(kind));
        if (!"crit".equals(severity) && !"warn".equals(severity) && !recovery) return;
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ensureChannel(context);
        Intent open = new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent tap = PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        NotificationCompat.Builder note = new NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_monitor)
                .setContentTitle(event.optString("title"))
                .setContentText(event.optString("text"))
                .setStyle(new NotificationCompat.BigTextStyle().bigText(event.optString("text")))
                .setPriority("crit".equals(severity) ? NotificationCompat.PRIORITY_HIGH : NotificationCompat.PRIORITY_DEFAULT)
                .setColor("crit".equals(severity) ? 0xFFFF6A7C : "warn".equals(severity) ? 0xFFF3B15C : 0xFF4EE0A2)
                .setWhen((long) (event.optDouble("ts") * 1000))
                .setShowWhen(true)
                .setContentIntent(tap)
                .setAutoCancel(true);
        NotificationManagerCompat.from(context).notify((int) (event.optLong("id") % Integer.MAX_VALUE), note.build());
    }

    private static void ensureChannel(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager.getNotificationChannel(CHANNEL) != null) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL, "System alerts", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("Machines or services going down, disks filling up, machines asking to join");
        manager.createNotificationChannel(channel);
    }
}
