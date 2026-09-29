package io.annunciator.dashboard;

import android.os.Bundle;
import androidx.activity.OnBackPressedCallback;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(UpdaterPlugin.class);
        super.onCreate(savedInstanceState);
        // Back belongs to the web UI: it closes the open page or sheet, and on
        // Overview it asks Updater.minimize() to background the app.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (bridge != null) bridge.triggerWindowJSEvent("annunciatorback");
                else moveTaskToBack(true);
            }
        });
    }
}
