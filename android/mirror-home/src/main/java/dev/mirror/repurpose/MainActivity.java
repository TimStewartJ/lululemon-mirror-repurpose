package dev.mirror.repurpose;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import androidx.annotation.OptIn;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.ui.AspectRatioFrameLayout;
import androidx.media3.ui.PlayerView;

@OptIn(markerClass = UnstableApi.class)
public final class MainActivity extends Activity {
    private static final long STATUS_REFRESH_INTERVAL_MS = 5000L;

    private final Handler statusHandler = new Handler(Looper.getMainLooper());
    private final Runnable statusRefresh = new Runnable() {
        @Override
        public void run() {
            if (nativeStatus != null) {
                nativeStatus.setText(buildStatusText());
            }
            statusHandler.postDelayed(this, STATUS_REFRESH_INTERVAL_MS);
        }
    };
    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (ControlServerService.ACTION_CONFIGURATION_CHANGED.equals(intent.getAction())) {
                renderDashboard();
            } else if (WifiManager.NETWORK_STATE_CHANGED_ACTION.equals(intent.getAction())
                    || WifiManager.WIFI_STATE_CHANGED_ACTION.equals(intent.getAction())) {
                renderDashboard();
            } else if (MediaPlaybackManager.ACTION_MEDIA_STATE_CHANGED.equals(intent.getAction())) {
                updateMediaVisibility();
            }
        }
    };

    private ConfigStore configStore;
    private PairingManager pairingManager;
    private MediaPlaybackManager media;
    private FrameLayout root;
    private View dashboardView;
    private WebView webDashboard;
    private PlayerView playerView;
    private TextView nativeStatus;
    private String loadedDashboardUrl = "";

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
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
        media = MediaPlaybackManager.getInstance(this);

        root = new FrameLayout(this);
        setContentView(root);

        playerView = new PlayerView(this);
        playerView.setBackgroundColor(Color.BLACK);
        playerView.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        playerView.setVisibility(View.GONE);
        root.addView(
                playerView,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
        media.attach(playerView);

        IntentFilter filter = new IntentFilter();
        filter.addAction(ControlServerService.ACTION_CONFIGURATION_CHANGED);
        filter.addAction(MediaPlaybackManager.ACTION_MEDIA_STATE_CHANGED);
        filter.addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION);
        filter.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            registerReceiver(stateReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(stateReceiver, filter);
        }
        startService(new Intent(this, ControlServerService.class));

        renderDashboard();
        updateMediaVisibility();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (root != null) {
            renderDashboard();
        }
        statusHandler.removeCallbacks(statusRefresh);
        statusHandler.post(statusRefresh);
    }

    @Override
    protected void onPause() {
        statusHandler.removeCallbacks(statusRefresh);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        statusHandler.removeCallbacks(statusRefresh);
        unregisterReceiver(stateReceiver);
        media.detach(playerView);
        destroyWebDashboard();
        super.onDestroy();
    }

    private void renderDashboard() {
        String dashboardUrl = configStore.getDashboardUrl();
        if (!dashboardUrl.isEmpty()) {
            renderWebDashboard(dashboardUrl);
        } else {
            renderNativeDashboard();
        }
    }

    private void renderNativeDashboard() {
        if (dashboardView != null && webDashboard == null) {
            if (dashboardView instanceof LinearLayout) {
                refreshNativeDashboard((LinearLayout) dashboardView);
            }
            return;
        }
        removeDashboardView();

        LinearLayout dashboard = new LinearLayout(this);
        dashboard.setOrientation(LinearLayout.VERTICAL);
        dashboard.setGravity(Gravity.CENTER);
        dashboard.setBackgroundColor(Color.rgb(3, 12, 20));
        dashboard.setPadding(48, 48, 48, 48);
        refreshNativeDashboard(dashboard);
        setDashboardView(dashboard);
    }

    private void refreshNativeDashboard(LinearLayout dashboard) {
        dashboard.removeAllViews();

        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm");
        clock.setFormat24Hour("HH:mm");
        clock.setTextColor(Color.WHITE);
        clock.setTextSize(72);
        dashboard.addView(clock);

        TextClock date = new TextClock(this);
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, MMMM d");
        date.setTextColor(Color.LTGRAY);
        date.setTextSize(26);
        dashboard.addView(date);

        String address = currentIpAddress();
        if (!address.isEmpty()) {
            String setupUrl = "http://" + address + ":" + ControlServerService.PORT + "/";
            ImageView qrCode = new ImageView(this);
            qrCode.setImageBitmap(QrCodeRenderer.render(setupUrl, 360));
            qrCode.setContentDescription("Scan to open Mirror Home controls");
            qrCode.setBackgroundColor(Color.WHITE);
            qrCode.setPadding(12, 12, 12, 12);
            LinearLayout.LayoutParams qrLayout = new LinearLayout.LayoutParams(384, 384);
            qrLayout.setMargins(0, 42, 0, 12);
            qrCode.setLayoutParams(qrLayout);
            dashboard.addView(qrCode);

            TextView qrHint = new TextView(this);
            qrHint.setText("Scan to control this Mirror");
            qrHint.setTextColor(Color.WHITE);
            qrHint.setTextSize(18);
            qrHint.setGravity(Gravity.CENTER);
            dashboard.addView(qrHint);
        }

        TextView status = new TextView(this);
        status.setText(buildStatusText());
        status.setTextColor(Color.rgb(80, 235, 255));
        status.setTextSize(20);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, 48, 0, 0);
        dashboard.addView(status);
        nativeStatus = status;
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void renderWebDashboard(String dashboardUrl) {
        if (webDashboard != null && dashboardUrl.equals(loadedDashboardUrl)) {
            return;
        }
        removeDashboardView();

        webDashboard = new WebView(this);
        WebSettings settings = webDashboard.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        webDashboard.setWebViewClient(new WebViewClient());
        webDashboard.setBackgroundColor(Color.BLACK);
        loadedDashboardUrl = dashboardUrl;
        setDashboardView(webDashboard);
        webDashboard.loadUrl(dashboardUrl);
    }

    private void setDashboardView(View view) {
        dashboardView = view;
        root.addView(
                view,
                0,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void removeDashboardView() {
        nativeStatus = null;
        if (dashboardView != null) {
            root.removeView(dashboardView);
            dashboardView = null;
        }
        destroyWebDashboard();
        loadedDashboardUrl = "";
    }

    private void destroyWebDashboard() {
        if (webDashboard != null) {
            webDashboard.stopLoading();
            webDashboard.destroy();
            webDashboard = null;
        }
    }

    private void updateMediaVisibility() {
        playerView.setVisibility(media.isPresentationActive() ? View.VISIBLE : View.GONE);
    }

    private String buildStatusText() {
        StringBuilder text = new StringBuilder();
        text.append(getString(R.string.bootstrap_status));
        text.append("\n\nPairing code: ").append(pairingManager.currentCode());
        text.append("\nUSB setup: http://127.0.0.1:")
                .append(ControlServerService.PORT);
        text.append("\nFCast receiver: port ").append(FCastServer.PORT);

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

    private String currentIpAddress() {
        WifiManager wifiManager =
                (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = wifiManager == null ? null : wifiManager.getConnectionInfo();
        return info == null || info.getNetworkId() < 0
                ? ""
                : WifiProvisioner.ipAddress(info.getIpAddress());
    }
}
