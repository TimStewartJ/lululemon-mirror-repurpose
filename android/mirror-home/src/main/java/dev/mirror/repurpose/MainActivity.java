package dev.mirror.repurpose;

import android.annotation.SuppressLint;
import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
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
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
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
    private static final int CAMERA_PERMISSION_REQUEST = 40;
    private static final long STATUS_REFRESH_INTERVAL_MS = 5000L;
    private static final long DASHBOARD_RETRY_INTERVAL_MS = 60_000L;
    private static final String OFFLINE_DASHBOARD_URL =
            "http://127.0.0.1:8787/dashboard/offline.html";
    private static final String BUILT_IN_DASHBOARD_URL =
            "http://127.0.0.1:8787/dashboard/custom.html";

    private final Handler statusHandler = new Handler(Looper.getMainLooper());
    private final Handler dashboardHandler = new Handler(Looper.getMainLooper());
    private final Runnable dashboardRetry = new Runnable() {
        @Override
        public void run() {
            if (webDashboard != null
                    && dashboardOffline
                    && !loadedDashboardUrl.isEmpty()) {
                webDashboard.loadUrl(loadedDashboardUrl);
            }
        }
    };
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
            } else if (AutomationManager.ACTION_STATE_CHANGED.equals(intent.getAction())) {
                updateSleepVisibility();
                requestCameraPermissionIfNeeded();
            } else if (WifiDirectOnboarding.ACTION_STATE_CHANGED.equals(intent.getAction())) {
                renderDashboard();
            }
        }
    };

    private ConfigStore configStore;
    private AutomationManager automation;
    private PairingManager pairingManager;
    private MediaPlaybackManager media;
    private FrameLayout root;
    private View dashboardView;
    private WebView webDashboard;
    private PlayerView playerView;
    private View sleepOverlay;
    private TextView nativeStatus;
    private String loadedDashboardUrl = "";
    private boolean dashboardOffline;
    private boolean cameraPermissionRequested;

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
        automation = AutomationManager.getInstance(this);
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

        sleepOverlay = new View(this);
        sleepOverlay.setBackgroundColor(Color.BLACK);
        sleepOverlay.setVisibility(View.GONE);
        root.addView(
                sleepOverlay,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));

        IntentFilter filter = new IntentFilter();
        filter.addAction(ControlServerService.ACTION_CONFIGURATION_CHANGED);
        filter.addAction(MediaPlaybackManager.ACTION_MEDIA_STATE_CHANGED);
        filter.addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION);
        filter.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        filter.addAction(AutomationManager.ACTION_STATE_CHANGED);
        filter.addAction(WifiDirectOnboarding.ACTION_STATE_CHANGED);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            registerReceiver(stateReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(stateReceiver, filter);
        }
        startService(new Intent(this, ControlServerService.class));

        renderDashboard();
        updateMediaVisibility();
        updateSleepVisibility();
        requestCameraPermissionIfNeeded();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (root != null) {
            renderDashboard();
        }
        statusHandler.removeCallbacks(statusRefresh);
        statusHandler.post(statusRefresh);
        if (dashboardOffline) {
            dashboardHandler.removeCallbacks(dashboardRetry);
            dashboardHandler.postDelayed(dashboardRetry, DASHBOARD_RETRY_INTERVAL_MS);
        }
        updateSleepVisibility();
        requestCameraPermissionIfNeeded();
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST) {
            automation.refreshMotionDetection();
        }
    }

    @Override
    protected void onPause() {
        statusHandler.removeCallbacks(statusRefresh);
        dashboardHandler.removeCallbacks(dashboardRetry);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        statusHandler.removeCallbacks(statusRefresh);
        dashboardHandler.removeCallbacks(dashboardRetry);
        unregisterReceiver(stateReceiver);
        media.detach(playerView);
        destroyWebDashboard();
        super.onDestroy();
    }

    private void renderDashboard() {
        String dashboardUrl = configStore.getDashboardUrl();
        if (!dashboardUrl.isEmpty()) {
            renderWebDashboard(dashboardUrl);
        } else if (pairingManager.isPaired() && !currentIpAddress().isEmpty()) {
            renderWebDashboard(BUILT_IN_DASHBOARD_URL);
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
        clock.setTimeZone(configStore.getEffectiveTimeZoneId());
        if (configStore.isClock24Hour()) {
            clock.setFormat12Hour("HH:mm");
            clock.setFormat24Hour("HH:mm");
        } else {
            clock.setFormat12Hour("h:mm");
            clock.setFormat24Hour("h:mm");
        }
        clock.setTextColor(Color.WHITE);
        clock.setTextSize(72);
        dashboard.addView(clock);

        TextClock date = new TextClock(this);
        date.setTimeZone(configStore.getEffectiveTimeZoneId());
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, MMMM d");
        date.setTextColor(Color.LTGRAY);
        date.setTextSize(26);
        dashboard.addView(date);

        String address = currentIpAddress();
        if (!address.isEmpty()) {
            String setupUrl = "http://" + address + ":" + ControlServerService.PORT + "/";
            ImageView qrCode = createQrCode(
                    setupUrl,
                    360,
                    "Scan to open Mirror Home controls");
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
        } else {
            addWifiDirectSetup(dashboard);
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
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        webDashboard.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                if (loadedDashboardUrl.equals(url)) {
                    dashboardOffline = false;
                    dashboardHandler.removeCallbacks(dashboardRetry);
                }
            }

            @Override
            public void onReceivedError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceError error) {
                if (request.isForMainFrame()) {
                    showOfflineDashboard();
                }
            }

            @Override
            public void onReceivedHttpError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceResponse errorResponse) {
                if (request.isForMainFrame()
                        && errorResponse.getStatusCode() >= 400) {
                    showOfflineDashboard();
                }
            }
        });
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
        dashboardHandler.removeCallbacks(dashboardRetry);
        dashboardOffline = false;
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

    private void showOfflineDashboard() {
        if (webDashboard == null || dashboardOffline) {
            return;
        }
        dashboardOffline = true;
        webDashboard.loadUrl(OFFLINE_DASHBOARD_URL);
        dashboardHandler.removeCallbacks(dashboardRetry);
        dashboardHandler.postDelayed(dashboardRetry, DASHBOARD_RETRY_INTERVAL_MS);
    }

    private void updateMediaVisibility() {
        playerView.setVisibility(media.isPresentationActive() ? View.VISIBLE : View.GONE);
    }

    private void updateSleepVisibility() {
        if (sleepOverlay != null) {
            sleepOverlay.setVisibility(automation.isSleeping() ? View.VISIBLE : View.GONE);
            sleepOverlay.bringToFront();
        }
        WindowManager.LayoutParams attributes = getWindow().getAttributes();
        float brightness = automation.isSleeping()
                ? 0f
                : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        if (Float.compare(attributes.screenBrightness, brightness) != 0) {
            attributes.screenBrightness = brightness;
            getWindow().setAttributes(attributes);
        }
    }

    private void requestCameraPermissionIfNeeded() {
        if (android.os.Build.VERSION.SDK_INT < 23
                || !configStore.isMotionEnabled()) {
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            automation.refreshMotionDetection();
            return;
        }
        if (cameraPermissionRequested) {
            return;
        }
        cameraPermissionRequested = true;
        requestPermissions(
                new String[]{Manifest.permission.CAMERA},
                CAMERA_PERMISSION_REQUEST);
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
            WifiDirectOnboarding.Snapshot setup =
                    WifiDirectOnboarding.getInstance(this).snapshot();
            if (setup.active && !setup.networkName.isEmpty()) {
                text.append("\nSetup network: ").append(setup.networkName);
                text.append("\nSetup page: http://")
                        .append(setup.address)
                        .append(":")
                        .append(ControlServerService.PORT);
            }
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

    private void addWifiDirectSetup(LinearLayout dashboard) {
        WifiDirectOnboarding.Snapshot setup =
                WifiDirectOnboarding.getInstance(this).snapshot();
        if (!setup.active
                || setup.networkName.isEmpty()
                || setup.passphrase.isEmpty()) {
            return;
        }

        TextView heading = new TextView(this);
        heading.setText("First-time wireless setup");
        heading.setTextColor(Color.WHITE);
        heading.setTextSize(22);
        heading.setGravity(Gravity.CENTER);
        heading.setPadding(0, 32, 0, 12);
        dashboard.addView(heading);

        LinearLayout codes = new LinearLayout(this);
        codes.setOrientation(LinearLayout.HORIZONTAL);
        codes.setGravity(Gravity.CENTER);
        addSetupCode(
                codes,
                WifiDirectOnboarding.wifiQrPayload(
                        setup.networkName,
                        setup.passphrase),
                "1. Join Mirror Setup Wi-Fi");
        addSetupCode(
                codes,
                "http://" + setup.address + ":" + ControlServerService.PORT + "/",
                "2. Open setup");
        dashboard.addView(codes);
    }

    private void addSetupCode(LinearLayout row, String value, String label) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(Gravity.CENTER);
        column.setPadding(16, 0, 16, 0);

        ImageView code = createQrCode(value, 280, label);
        column.addView(code, new LinearLayout.LayoutParams(304, 304));

        TextView hint = new TextView(this);
        hint.setText(label);
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(16);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, 10, 0, 0);
        column.addView(hint);
        row.addView(column);
    }

    private ImageView createQrCode(String value, int size, String description) {
        ImageView code = new ImageView(this);
        code.setImageBitmap(QrCodeRenderer.render(value, size));
        code.setContentDescription(description);
        code.setBackgroundColor(Color.WHITE);
        code.setPadding(12, 12, 12, 12);
        return code;
    }
}
