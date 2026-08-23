package dev.mirror.repurpose;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private final BroadcastReceiver configurationReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            render();
        }
    };

    private ConfigStore configStore;
    private PairingManager pairingManager;
    private LinearLayout root;
    private WebView dashboard;
    private String loadedDashboardUrl = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_FULLSCREEN
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);

        configStore = new ConfigStore(this);
        pairingManager = PairingManager.getInstance(this);
        startService(new Intent(this, ControlServerService.class));
        registerReceiver(
                configurationReceiver,
                new IntentFilter(ControlServerService.ACTION_CONFIGURATION_CHANGED));
        render();
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(configurationReceiver);
        if (dashboard != null) {
            dashboard.destroy();
            dashboard = null;
        }
        super.onDestroy();
    }

    private void render() {
        String dashboardUrl = configStore.getDashboardUrl();
        if (!dashboardUrl.isEmpty()) {
            renderWebDashboard(dashboardUrl);
            return;
        }
        renderNativeDashboard();
    }

    private void renderNativeDashboard() {
        destroyDashboard();
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.rgb(3, 12, 20));
        root.setPadding(48, 48, 48, 48);

        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm");
        clock.setFormat24Hour("HH:mm");
        clock.setTextColor(Color.WHITE);
        clock.setTextSize(72);
        root.addView(clock);

        TextClock date = new TextClock(this);
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, MMMM d");
        date.setTextColor(Color.LTGRAY);
        date.setTextSize(26);
        root.addView(date);

        TextView status = new TextView(this);
        status.setText(buildStatusText());
        status.setTextColor(Color.rgb(80, 235, 255));
        status.setTextSize(20);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, 48, 0, 0);
        root.addView(status);

        setContentView(root);
    }

    private void renderWebDashboard(String dashboardUrl) {
        if (dashboard != null && dashboardUrl.equals(loadedDashboardUrl)) {
            return;
        }
        destroyDashboard();
        dashboard = new WebView(this);
        WebSettings settings = dashboard.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        }
        dashboard.setWebViewClient(new WebViewClient());
        dashboard.setBackgroundColor(Color.BLACK);
        setContentView(dashboard);
        loadedDashboardUrl = dashboardUrl;
        dashboard.loadUrl(dashboardUrl);
    }

    private void destroyDashboard() {
        if (dashboard != null) {
            dashboard.stopLoading();
            dashboard.destroy();
            dashboard = null;
        }
        loadedDashboardUrl = "";
    }

    private String buildStatusText() {
        StringBuilder text = new StringBuilder();
        text.append(getString(R.string.bootstrap_status));
        text.append("\n\nPairing code: ").append(pairingManager.currentCode());
        text.append("\nUSB setup: http://127.0.0.1:")
                .append(ControlServerService.PORT);

        WifiManager wifiManager =
                (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = wifiManager == null ? null : wifiManager.getConnectionInfo();
        if (info != null && info.getNetworkId() >= 0) {
            text.append("\nWi-Fi: ").append(WifiProvisioner.cleanSsid(info.getSSID()));
            String address = WifiProvisioner.ipAddress(info.getIpAddress());
            if (!address.isEmpty()) {
                text.append("\nLAN setup: http://")
                        .append(address)
                        .append(":")
                        .append(ControlServerService.PORT);
            }
        } else {
            text.append("\nWi-Fi: not connected");
        }
        return text.toString();
    }
}
