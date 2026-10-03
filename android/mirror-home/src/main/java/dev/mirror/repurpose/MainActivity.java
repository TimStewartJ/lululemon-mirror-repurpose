package dev.mirror.repurpose;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.annotation.SuppressLint;
import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.animation.AccelerateDecelerateInterpolator;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
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

import java.io.File;

@OptIn(markerClass = UnstableApi.class)
public final class MainActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 40;
    private static final long STATUS_REFRESH_INTERVAL_MS = 5000L;
    private static final long DASHBOARD_RETRY_INTERVAL_MS = 60_000L;
    private static final long SCHEDULE_CHECK_MAX_MS = 30_000L;
    private static final long SCHEDULE_CHECK_MIN_MS = 1_000L;
    private static final long AMBIENT_FADE_OUT_MS = 900L;
    private static final long AMBIENT_FADE_IN_DELAY_MS = 350L;
    private static final long AMBIENT_FADE_IN_MS = 1400L;
    private static final long VOICE_CAPTION_MS = 2_500L;
    private static final String OFFLINE_DASHBOARD_URL =
            "http://127.0.0.1:8787/dashboard/offline.html";
    private static final String BUILT_IN_DASHBOARD_URL =
            "http://127.0.0.1:8787/dashboard/custom.html";
    private static final int TEXT_COLOR = Color.rgb(245, 242, 236);
    /** What was understood, before the answer: the same white, further back. */
    private static final int CAPTION_HEARD_COLOR = Color.argb(190, 245, 242, 236);

    private final Handler statusHandler = new Handler(Looper.getMainLooper());
    private final Handler dashboardHandler = new Handler(Looper.getMainLooper());
    private final Handler scheduleHandler = new Handler(Looper.getMainLooper());
    private final Runnable scheduleCheck = new Runnable() {
        @Override
        public void run() {
            if (root != null && !backgroundVideos.effectiveId().equals(appliedAmbientId)) {
                renderDashboard();
            }
            long next = backgroundVideos.nextScheduleChangeMillis();
            long delay = next < 0
                    ? SCHEDULE_CHECK_MAX_MS
                    : Math.max(SCHEDULE_CHECK_MIN_MS, Math.min(
                            SCHEDULE_CHECK_MAX_MS,
                            next - System.currentTimeMillis() + 250L));
            scheduleHandler.postDelayed(this, delay);
        }
    };
    private final Runnable dashboardRetry = new Runnable() {
        @Override
        public void run() {
            if (webDashboard != null
                    && dashboardOffline
                    && !loadedDashboardUrl.isEmpty()) {
                DashboardDiagnostics.record("retrying", loadedDashboardUrl, "");
                webDashboard.loadUrl(loadedDashboardUrl);
            }
        }
    };
    private final Runnable statusRefresh = new Runnable() {
        @Override
        public void run() {
            if (nativeStatus != null
                    && !configStore.getEffectiveTimeZoneId().equals(nativeClockZoneId)) {
                // A daylight-saving change: rebuild the clocks on the new offset.
                renderDashboard();
            }
            if (nativeStatus != null) {
                nativeStatus.setText(buildStatusText());
            }
            if (nativeCode != null) {
                nativeCode.setText(groupedPairingCode());
            }
            if (displayFade == null && displayVisibility == 1f) {
                // Releases a held wake override once a brightness retry succeeds.
                applyDisplayVisibility(1f);
            }
            statusHandler.postDelayed(this, STATUS_REFRESH_INTERVAL_MS);
        }
    };
    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (ControlServerService.ACTION_CONFIGURATION_CHANGED.equals(intent.getAction())) {
                renderDashboard();
                restartScheduleCheck();
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
    private final Handler voiceHandler = new Handler(Looper.getMainLooper());
    private final ScreenCapture.Source screenSource = this::drawGlass;
    private final GlassCaption.Glass captionGlass = this::showVoiceEvent;
    private ValueAnimator captionPulse;
    private final Runnable hideVoiceCaption = new Runnable() {
        @Override
        public void run() {
            stopCaptionPulse();
            voiceCaption.animate()
                    .alpha(0f)
                    .setDuration(400L)
                    .withEndAction(() -> voiceCaption.setVisibility(View.GONE));
        }
    };

    private ConfigStore configStore;
    private AutomationManager automation;
    private BackgroundVideoLibrary backgroundVideos;
    private PairingManager pairingManager;
    private MediaPlaybackManager media;
    private FrameLayout root;
    private View dashboardView;
    private WebView webDashboard;
    private PlayerView ambientVideoView;
    private View ambientCurtain;
    private PlayerView playerView;
    private View sleepOverlay;
    private TextView voiceCaption;
    private TextView nativeStatus;
    private TextView nativeCode;
    private String nativeClockZoneId = "";
    private String loadedDashboardUrl = "";
    private String appliedAmbientId = "";
    private File appliedAmbientFile;
    private boolean ambientSwitchPending;
    private boolean dashboardOffline;
    private boolean cameraPermissionRequested;
    private boolean ambientDashboardSelected;
    private boolean renderedAmbientDashboardSelected;
    private String ambientBackgroundFit = "cover";
    private boolean activityResumed;
    private ValueAnimator displayFade;
    private float displayFadeTarget;
    private float displayVisibility = -1f;
    private int displayAwakeLevel = DisplayFadePolicy.MAX_BACKLIGHT;
    private long lastBacklightUpdateUptime;

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        DashboardDiagnostics.record("activity-create", "", "");
        ActivityDiagnostics.created();
        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_FULLSCREEN
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (!ForegroundKeeper.hasInputDevices(getResources().getConfiguration())) {
            // Nobody can wake a Mirror's display or dismiss a lock screen, so
            // the dashboard's window does both when it opens. A phone's stay
            // the business of its owner.
            getWindow().addFlags(
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                            | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                            | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        }
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);

        configStore = new ConfigStore(this);
        automation = AutomationManager.getInstance(this);
        backgroundVideos = BackgroundVideoLibrary.getInstance(this);
        pairingManager = PairingManager.getInstance(this);
        media = MediaPlaybackManager.getInstance(this);
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            // Lets the emulator checks inspect the dashboard page; never in a release.
            WebView.setWebContentsDebuggingEnabled(true);
        }

        root = new FrameLayout(this);
        setContentView(root);

        ambientVideoView = new PlayerView(this);
        ambientVideoView.setBackgroundColor(Color.BLACK);
        ambientVideoView.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        ambientVideoView.setVisibility(View.GONE);
        root.addView(
                ambientVideoView,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
        media.attachAmbient(ambientVideoView);

        // A black curtain between the video and the dashboard lets videos fade
        // through black; SurfaceView content itself ignores view alpha on Android 6.
        ambientCurtain = new View(this);
        ambientCurtain.setBackgroundColor(Color.BLACK);
        ambientCurtain.setAlpha(0f);
        ambientCurtain.setVisibility(View.GONE);
        root.addView(
                ambientCurtain,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));

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

        // What the Mirror heard, in place of a sound: nobody wants a mirror to talk back.
        voiceCaption = new TextView(this);
        voiceCaption.setTextColor(TEXT_COLOR);
        voiceCaption.setTextSize(TypedValue.COMPLEX_UNIT_SP, 32);
        voiceCaption.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        voiceCaption.setGravity(Gravity.CENTER);
        voiceCaption.setPadding(dp(28), dp(10), dp(28), dp(12));
        // Black is plain mirror on the glass: it shows nothing itself, and
        // keeps a film or a widget behind the words from tangling with them.
        // Wholly black, because a clock's large figures show through anything less.
        GradientDrawable captionBacking = new GradientDrawable();
        captionBacking.setColor(Color.BLACK);
        captionBacking.setCornerRadius(dp(36));
        voiceCaption.setBackground(captionBacking);
        voiceCaption.setAlpha(0f);
        voiceCaption.setVisibility(View.GONE);
        FrameLayout.LayoutParams captionLayout = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        captionLayout.bottomMargin = dp(96);
        // An answer may run to three lines; a command's caption is a word or two.
        voiceCaption.setMaxWidth(getResources().getDisplayMetrics().widthPixels * 84 / 100);
        voiceCaption.setMaxLines(4);
        voiceCaption.setEllipsize(android.text.TextUtils.TruncateAt.END);
        root.addView(voiceCaption, captionLayout);

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
        ScreenCapture.attach(screenSource);
        GlassCaption.attach(captionGlass);

        renderDashboard();
        updateMediaVisibility();
        updateSleepVisibility();
        requestCameraPermissionIfNeeded();
    }

    @Override
    protected void onStart() {
        super.onStart();
        ActivityDiagnostics.started();
    }

    @Override
    protected void onStop() {
        ActivityDiagnostics.stopped();
        super.onStop();
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityResumed = true;
        ActivityDiagnostics.resumed();
        if (root != null) {
            renderDashboard();
        }
        statusHandler.removeCallbacks(statusRefresh);
        statusHandler.post(statusRefresh);
        restartScheduleCheck();
        if (dashboardOffline) {
            dashboardHandler.removeCallbacks(dashboardRetry);
            dashboardHandler.postDelayed(dashboardRetry, DASHBOARD_RETRY_INTERVAL_MS);
        }
        updateSleepVisibility();
        updateAmbientVideoState();
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
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        ActivityDiagnostics.focusChanged(hasFocus);
    }

    @Override
    protected void onPause() {
        activityResumed = false;
        ActivityDiagnostics.paused();
        updateAmbientVideoState();
        statusHandler.removeCallbacks(statusRefresh);
        scheduleHandler.removeCallbacks(scheduleCheck);
        dashboardHandler.removeCallbacks(dashboardRetry);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        ActivityDiagnostics.destroyed();
        statusHandler.removeCallbacks(statusRefresh);
        scheduleHandler.removeCallbacks(scheduleCheck);
        dashboardHandler.removeCallbacks(dashboardRetry);
        ScreenCapture.detach(screenSource);
        GlassCaption.detach(captionGlass);
        voiceHandler.removeCallbacks(hideVoiceCaption);
        stopCaptionPulse();
        voiceCaption.animate().cancel();
        ambientCurtain.animate().cancel();
        cancelDisplayFade();
        unregisterReceiver(stateReceiver);
        media.detachAmbient(ambientVideoView);
        media.detach(playerView);
        destroyWebDashboard();
        super.onDestroy();
    }

    private void renderDashboard() {
        String dashboardUrl = configStore.getDashboardUrl();
        if (!dashboardUrl.isEmpty()) {
            ambientDashboardSelected = false;
            renderWebDashboard(dashboardUrl);
        } else if (pairingManager.isPaired() && !currentIpAddress().isEmpty()) {
            DashboardLayoutConfig layout = configStore.getDashboardLayout();
            ambientDashboardSelected = "video".equals(layout.backgroundMode())
                    && backgroundVideos.effectiveFile() != null;
            ambientBackgroundFit = layout.backgroundFit();
            renderWebDashboard(BUILT_IN_DASHBOARD_URL);
        } else {
            ambientDashboardSelected = false;
            renderNativeDashboard();
        }
        updateAmbientVideoState();
    }

    private void renderNativeDashboard() {
        DashboardDiagnostics.record("native", "", "");
        if (dashboardView != null && webDashboard == null) {
            if (dashboardView instanceof LinearLayout) {
                refreshNativeDashboard((LinearLayout) dashboardView);
            }
            return;
        }
        removeDashboardView();

        LinearLayout dashboard = new LinearLayout(this);
        dashboard.setOrientation(LinearLayout.VERTICAL);
        dashboard.setBackgroundColor(Color.BLACK);
        refreshNativeDashboard(dashboard);
        setDashboardView(dashboard);
    }

    /* The setup screen is the first thing anyone sees in the glass: a hairline
       clock, one rounded QR card, and a pairing code large enough to read from
       across the room. Telemetry stays off the mirror. */
    private void refreshNativeDashboard(LinearLayout dashboard) {
        dashboard.removeAllViews();
        nativeCode = null;
        int edge = dp(52);
        dashboard.setPadding(edge, dp(64), edge, dp(44));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setGravity(Gravity.START);

        nativeClockZoneId = configStore.getEffectiveTimeZoneId();
        TextClock clock = new TextClock(this);
        clock.setTimeZone(nativeClockZoneId);
        String format = configStore.isClock24Hour() ? "HH:mm" : "h:mm";
        clock.setFormat12Hour(format);
        clock.setFormat24Hour(format);
        clock.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        clock.setTextColor(TEXT_COLOR);
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 128);
        clock.setLetterSpacing(-0.04f);
        clock.setIncludeFontPadding(false);
        top.addView(clock);

        TextClock date = new TextClock(this);
        date.setTimeZone(nativeClockZoneId);
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, MMMM d");
        date.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        date.setTextColor(withAlpha(TEXT_COLOR, 0.72f));
        date.setTextSize(TypedValue.COMPLEX_UNIT_SP, 26);
        LinearLayout.LayoutParams dateLayout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        dateLayout.topMargin = dp(6);
        top.addView(date, dateLayout);
        dashboard.addView(top);

        LinearLayout middle = new LinearLayout(this);
        middle.setOrientation(LinearLayout.VERTICAL);
        middle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams middleLayout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f);
        dashboard.addView(middle, middleLayout);

        String address = currentIpAddress();
        WifiDirectOnboarding.Snapshot setup = WifiDirectOnboarding.getInstance(this).snapshot();
        boolean setupNetwork = address.isEmpty()
                && setup.active
                && !setup.networkName.isEmpty()
                && !setup.passphrase.isEmpty();
        if (!address.isEmpty()) {
            String controlUrl = "http://" + address + ":" + ControlServerService.PORT + "/";
            middle.addView(createQrCard(controlUrl, 252, "Scan to open Mirror controls"));
            middle.addView(caption(
                    pairingManager.isPaired()
                            ? "Scan to open the controls"
                            : "Scan with your phone to set up",
                    dp(26)));
            addPairingCode(middle);
        } else if (setupNetwork) {
            LinearLayout codes = new LinearLayout(this);
            codes.setOrientation(LinearLayout.HORIZONTAL);
            codes.setGravity(Gravity.CENTER);
            codes.addView(setupStep(
                    "1",
                    "Join Mirror Setup Wi-Fi",
                    WifiDirectOnboarding.wifiQrPayload(setup.networkName, setup.passphrase)));
            View gap = new View(this);
            codes.addView(gap, new LinearLayout.LayoutParams(dp(28), 1));
            codes.addView(setupStep(
                    "2",
                    "Open setup",
                    "http://" + setup.address + ":" + ControlServerService.PORT + "/"));
            middle.addView(codes);
            addPairingCode(middle);
        } else {
            TextView title = new TextView(this);
            title.setText(pairingManager.isPaired() ? "Wi-Fi disconnected" : "Ready to set up");
            title.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
            title.setTextColor(TEXT_COLOR);
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 44);
            title.setGravity(Gravity.CENTER);
            middle.addView(title);
            middle.addView(caption(
                    pairingManager.isPaired()
                            ? "Waiting for the network to return"
                            : "Connect a computer over USB and open the forwarded controls to continue",
                    dp(14)));
            // The code makes USB setup and recovery possible without a network.
            addPairingCode(middle);
        }

        TextView status = new TextView(this);
        status.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        status.setTextColor(withAlpha(TEXT_COLOR, 0.42f));
        status.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        status.setLetterSpacing(0.02f);
        status.setGravity(Gravity.CENTER);
        status.setText(buildStatusText());
        dashboard.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        nativeStatus = status;
    }

    private void addPairingCode(LinearLayout parent) {
        TextView label = new TextView(this);
        label.setText("PAIRING CODE");
        label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        label.setTextColor(withAlpha(TEXT_COLOR, 0.5f));
        label.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        label.setLetterSpacing(0.22f);
        label.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams labelLayout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        labelLayout.topMargin = dp(44);
        parent.addView(label, labelLayout);

        TextView code = new TextView(this);
        code.setText(groupedPairingCode());
        code.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        code.setTextColor(TEXT_COLOR);
        code.setTextSize(TypedValue.COMPLEX_UNIT_SP, 64);
        code.setLetterSpacing(0.14f);
        code.setGravity(Gravity.CENTER);
        code.setIncludeFontPadding(false);
        LinearLayout.LayoutParams codeLayout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        codeLayout.topMargin = dp(8);
        parent.addView(code, codeLayout);
        nativeCode = code;
    }

    private String groupedPairingCode() {
        // Drawing the code on the glass is what opens pairing.
        String value = pairingManager.displayCode();
        return value.length() == 6 ? value.substring(0, 3) + " " + value.substring(3) : value;
    }

    private View setupStep(String number, String title, String payload) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(Gravity.CENTER_HORIZONTAL);
        column.addView(createQrCard(payload, 178, title));

        TextView step = new TextView(this);
        step.setText(number);
        step.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        step.setTextColor(TEXT_COLOR);
        step.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34);
        step.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams stepLayout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        stepLayout.topMargin = dp(20);
        column.addView(step, stepLayout);

        TextView hint = caption(title, dp(4));
        hint.setMaxWidth(dp(230));
        column.addView(hint);
        return column;
    }

    private TextView caption(String text, int topMargin) {
        TextView hint = new TextView(this);
        hint.setText(text);
        hint.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        hint.setTextColor(withAlpha(TEXT_COLOR, 0.78f));
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        hint.setGravity(Gravity.CENTER);
        hint.setLineSpacing(0f, 1.25f);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        layout.topMargin = topMargin;
        layout.gravity = Gravity.CENTER_HORIZONTAL;
        hint.setLayoutParams(layout);
        hint.setMaxWidth(dp(420));
        return hint;
    }

    private View createQrCard(String value, int sizeDp, String description) {
        int size = dp(sizeDp);
        int quietZone = dp(18);
        FrameLayout card = new FrameLayout(this);
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.WHITE);
        background.setCornerRadius(dp(22));
        card.setBackground(background);
        card.setPadding(quietZone, quietZone, quietZone, quietZone);

        ImageView code = new ImageView(this);
        code.setImageBitmap(QrCodeRenderer.render(value, size));
        code.setContentDescription(description);
        card.addView(code, new FrameLayout.LayoutParams(size, size));

        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        layout.gravity = Gravity.CENTER_HORIZONTAL;
        card.setLayoutParams(layout);
        return card;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static int withAlpha(int color, float alpha) {
        return Color.argb(
                Math.round(255 * alpha),
                Color.red(color),
                Color.green(color),
                Color.blue(color));
    }
    @SuppressLint("SetJavaScriptEnabled")
    private void renderWebDashboard(String dashboardUrl) {
        if (webDashboard != null && dashboardUrl.equals(loadedDashboardUrl)) {
            boolean backgroundModeChanged =
                    renderedAmbientDashboardSelected != ambientDashboardSelected;
            renderedAmbientDashboardSelected = ambientDashboardSelected;
            webDashboard.setBackgroundColor(
                    ambientDashboardSelected ? Color.TRANSPARENT : Color.BLACK);
            if (backgroundModeChanged) {
                webDashboard.reload();
            }
            return;
        }
        removeDashboardView();
        DashboardDiagnostics.record("loading", dashboardUrl, "");

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
                DashboardDiagnostics.record("page-finished", url, "");
                view.evaluateJavascript(
                        "(function(){return [document.readyState,location.href,"
                                + "typeof window.MirrorRenderer].join('|');}())",
                        DashboardDiagnostics::recordPageProbe);
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
                    DashboardDiagnostics.recordFailure(
                            "load-error",
                            String.valueOf(request.getUrl()),
                            String.valueOf(error.getDescription()));
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
                    DashboardDiagnostics.recordFailure(
                            "http-error",
                            String.valueOf(request.getUrl()),
                            String.valueOf(errorResponse.getStatusCode()));
                    showOfflineDashboard();
                }
            }
        });
        webDashboard.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage message) {
                DashboardDiagnostics.recordConsole(
                        message.messageLevel().name(),
                        message.message(),
                        message.sourceId(),
                        message.lineNumber());
                return false;
            }
        });
        webDashboard.setBackgroundColor(
                ambientDashboardSelected ? Color.TRANSPARENT : Color.BLACK);
        renderedAmbientDashboardSelected = ambientDashboardSelected;
        loadedDashboardUrl = dashboardUrl;
        setDashboardView(webDashboard);
        webDashboard.loadUrl(dashboardUrl);
    }

    private void setDashboardView(View view) {
        dashboardView = view;
        root.addView(
                view,
                root.indexOfChild(ambientCurtain) + 1,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void removeDashboardView() {
        nativeStatus = null;
        nativeCode = null;
        dashboardHandler.removeCallbacks(dashboardRetry);
        dashboardOffline = false;
        if (dashboardView != null) {
            root.removeView(dashboardView);
            dashboardView = null;
        }
        destroyWebDashboard();
        loadedDashboardUrl = "";
        renderedAmbientDashboardSelected = false;
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
        DashboardDiagnostics.record("offline", loadedDashboardUrl, "");
        webDashboard.loadUrl(OFFLINE_DASHBOARD_URL);
        dashboardHandler.removeCallbacks(dashboardRetry);
        dashboardHandler.postDelayed(dashboardRetry, DASHBOARD_RETRY_INTERVAL_MS);
    }

    private void updateMediaVisibility() {
        playerView.setVisibility(media.isPresentationActive() ? View.VISIBLE : View.GONE);
        updateAmbientVideoState();
    }

    /**
     * Shows what was heard: that the Mirror listens, what it did, that it
     * did not follow; and what its assistant is doing and answers.
     *
     * <p>A caption that breathes means work is going on: three dots while
     * the request travels, then the words as they were understood. An
     * answer stands still, in the full white of the glass.
     */
    private void showVoiceEvent(String kind, String caption, long millis) {
        if (voiceCaption == null || kind == null) {
            return;
        }
        String text = caption == null ? "" : caption;
        long showFor = millis > 0 ? millis : VOICE_CAPTION_MS;
        boolean working = false;
        int color = TEXT_COLOR;
        if (AssistantManager.KIND_CLEAR.equals(kind)) {
            voiceHandler.removeCallbacks(hideVoiceCaption);
            hideVoiceCaption.run();
            return;
        } else if (VoiceManager.KIND_LISTENING.equals(kind)) {
            text = "Listening";
            showFor = VoiceInterpreter.WINDOW_MS;
        } else if (VoiceManager.KIND_NOT_UNDERSTOOD.equals(kind)) {
            text = "Didn\u2019t catch that";
            showFor = VoiceInterpreter.WINDOW_MS;
        } else if (AssistantManager.KIND_THINKING.equals(kind)) {
            text = "\u2022  \u2022  \u2022";
            working = true;
        } else if (AssistantManager.KIND_HEARD.equals(kind)) {
            text = "\u201c" + text + "\u201d";
            color = CAPTION_HEARD_COLOR;
            working = true;
        }
        voiceHandler.removeCallbacks(hideVoiceCaption);
        stopCaptionPulse();
        voiceCaption.animate().cancel();
        voiceCaption.setText(text);
        voiceCaption.setTextColor(color);
        // Large for a word or two, smaller for a sentence that has to fit.
        voiceCaption.setTextSize(TypedValue.COMPLEX_UNIT_SP, text.length() <= 32 ? 32 : 26);
        voiceCaption.setVisibility(View.VISIBLE);
        voiceCaption.animate().alpha(1f).setDuration(180L).withEndAction(null);
        if (working) {
            startCaptionPulse(color);
        }
        voiceHandler.postDelayed(hideVoiceCaption, showFor);
    }

    /** Lets the words breathe. Their backing stays, so that what lies behind them does not come and go. */
    private void startCaptionPulse(int color) {
        captionPulse = ValueAnimator.ofFloat(1f, 0.35f);
        captionPulse.setDuration(1_600L);
        captionPulse.setRepeatCount(ValueAnimator.INFINITE);
        captionPulse.setRepeatMode(ValueAnimator.REVERSE);
        captionPulse.addUpdateListener(animation -> voiceCaption.setTextColor(Color.argb(
                Math.round(Color.alpha(color) * (float) animation.getAnimatedValue()),
                Color.red(color), Color.green(color), Color.blue(color))));
        captionPulse.start();
    }

    private void stopCaptionPulse() {
        if (captionPulse != null) {
            captionPulse.cancel();
            captionPulse = null;
        }
    }

    /**
     * Draws what the glass shows, for {@link ScreenCapture}: the widgets and
     * captions as they are, over the background film's poster, since the
     * film's own layer cannot be drawn.
     *
     * @return the picture, or null while the display is dark or the dashboard is not in front
     */
    private android.graphics.Bitmap drawGlass(int width) {
        if (root == null || !activityResumed || automation.isSleeping()
                || root.getWidth() == 0 || root.getHeight() == 0) {
            return null;
        }
        float scale = width / (float) root.getWidth();
        int height = Math.round(root.getHeight() * scale);
        android.graphics.Bitmap views = android.graphics.Bitmap.createBitmap(
                width, height, android.graphics.Bitmap.Config.ARGB_8888);
        android.graphics.Canvas canvas = new android.graphics.Canvas(views);
        canvas.scale(scale, scale);
        // The video's surface draws itself as a hole, which leaves the poster to show through.
        root.draw(canvas);
        android.graphics.Bitmap glass = android.graphics.Bitmap.createBitmap(
                width, height, android.graphics.Bitmap.Config.RGB_565);
        android.graphics.Canvas composed = new android.graphics.Canvas(glass);
        composed.drawColor(Color.BLACK);
        File poster = ambientVideoView.getVisibility() == View.VISIBLE && !appliedAmbientId.isEmpty()
                ? backgroundVideos.poster(appliedAmbientId)
                : null;
        android.graphics.Bitmap still = poster == null
                ? null
                : android.graphics.BitmapFactory.decodeFile(poster.getAbsolutePath());
        if (still != null) {
            boolean contain = "contain".equals(ambientBackgroundFit);
            float fit = contain
                    ? Math.min(width / (float) still.getWidth(), height / (float) still.getHeight())
                    : Math.max(width / (float) still.getWidth(), height / (float) still.getHeight());
            float drawnWidth = still.getWidth() * fit;
            float drawnHeight = still.getHeight() * fit;
            composed.drawBitmap(
                    still,
                    null,
                    new android.graphics.RectF(
                            (width - drawnWidth) / 2f,
                            (height - drawnHeight) / 2f,
                            (width + drawnWidth) / 2f,
                            (height + drawnHeight) / 2f),
                    new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG));
            still.recycle();
        }
        composed.drawBitmap(views, 0f, 0f, null);
        views.recycle();
        return glass;
    }

    /** Re-arms the boundary timer so a newly saved schedule is timed precisely. */
    private void restartScheduleCheck() {
        scheduleHandler.removeCallbacks(scheduleCheck);
        if (activityResumed) {
            scheduleHandler.post(scheduleCheck);
        }
    }

    private void updateSleepVisibility() {
        if (sleepOverlay == null) {
            return;
        }
        sleepOverlay.bringToFront();
        float target = automation.isSleeping() ? 0f : 1f;
        if (displayFade != null) {
            if (Float.compare(displayFadeTarget, target) == 0) {
                return;
            }
            cancelDisplayFade();
        }
        if (displayVisibility < 0f || !activityResumed) {
            // Nothing is on screen to fade (first frame or covered); settle at once.
            displayAwakeLevel = awakeBacklightLevel(target);
            applyDisplayVisibility(target);
            updateAmbientVideoState();
            return;
        }
        if (Float.compare(displayVisibility, target) == 0) {
            applyDisplayVisibility(target);
            updateAmbientVideoState();
            return;
        }
        if (displayVisibility == 1f) {
            // Awake without an override: fade from whatever the panel shows now.
            displayAwakeLevel = currentSystemBrightness();
        } else if (target == 1f) {
            displayAwakeLevel = awakeBacklightLevel(target);
        }
        startDisplayFade(target);
        updateAmbientVideoState();
    }

    private void startDisplayFade(float target) {
        float from = displayVisibility;
        ValueAnimator fade = ValueAnimator.ofFloat(from, target);
        fade.setDuration(Math.max(1L, DisplayFadePolicy.duration(from, target)));
        fade.setInterpolator(new AccelerateDecelerateInterpolator());
        fade.addUpdateListener(animation ->
                applyDisplayVisibility((Float) animation.getAnimatedValue()));
        fade.addListener(new AnimatorListenerAdapter() {
            private boolean cancelled;

            @Override
            public void onAnimationCancel(Animator animation) {
                cancelled = true;
            }

            @Override
            public void onAnimationEnd(Animator animation) {
                if (displayFade != animation) {
                    return;
                }
                displayFade = null;
                if (!cancelled) {
                    applyDisplayVisibility(displayFadeTarget);
                    updateAmbientVideoState();
                }
            }
        });
        displayFade = fade;
        displayFadeTarget = target;
        // Pin the starting level as an explicit override before the first frame,
        // so a stored-brightness change cannot show through.
        applyDisplayVisibility(from);
        fade.start();
    }

    private void cancelDisplayFade() {
        if (displayFade != null) {
            ValueAnimator fade = displayFade;
            displayFade = null;
            fade.cancel();
        }
    }

    private void applyDisplayVisibility(float visibility) {
        displayVisibility = DisplayFadePolicy.clampVisibility(visibility);
        // A translucent background avoids an offscreen layer that view alpha needs.
        sleepOverlay.setBackgroundColor(Color.argb(
                Math.round(255 * DisplayFadePolicy.overlayAlpha(displayVisibility)), 0, 0, 0));
        sleepOverlay.setVisibility(displayVisibility < 1f ? View.VISIBLE : View.GONE);
        boolean settled = displayVisibility == 0f || displayVisibility == 1f;
        long now = SystemClock.uptimeMillis();
        if (!settled && now - lastBacklightUpdateUptime
                < DisplayFadePolicy.BACKLIGHT_UPDATE_INTERVAL_MS) {
            // Each override is a window relayout; Android's own brightness ramp
            // interpolates between these steps.
            return;
        }
        boolean releaseOverride = displayVisibility >= 1f
                && displayFade == null
                && automation.isBrightnessApplied();
        float brightness = releaseOverride
                ? WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
                : DisplayFadePolicy.windowBrightness(DisplayFadePolicy.backlightLevel(
                        displayAwakeLevel, displayVisibility));
        WindowManager.LayoutParams attributes = getWindow().getAttributes();
        if (Float.compare(attributes.screenBrightness, brightness) != 0) {
            attributes.screenBrightness = brightness;
            getWindow().setAttributes(attributes);
            lastBacklightUpdateUptime = now;
        }
        if (displayVisibility == 0f && displayFade == null && automation.isSleeping()) {
            automation.onDisplayFadedOut();
        }
    }

    private int awakeBacklightLevel(float target) {
        return target == 1f
                ? automation.awakeBrightness()
                : currentSystemBrightness();
    }

    private int currentSystemBrightness() {
        try {
            return Settings.System.getInt(
                    getContentResolver(), Settings.System.SCREEN_BRIGHTNESS);
        } catch (Settings.SettingNotFoundException error) {
            return configStore.getWakeBrightness();
        }
    }

    /** Ambient video keeps playing until the panel has actually gone dark. */
    private boolean displayDark() {
        return automation.isSleeping() && displayVisibility == 0f;
    }

    private void updateAmbientVideoState() {
        if (ambientVideoView == null || automation == null || media == null) {
            return;
        }
        AmbientVideoPolicy.State state = AmbientVideoPolicy.desiredState(
                ambientDashboardSelected,
                media.isPresentationActive(),
                activityResumed,
                displayDark());
        ambientVideoView.setResizeMode(
                "contain".equals(ambientBackgroundFit)
                        ? AspectRatioFrameLayout.RESIZE_MODE_FIT
                        : AspectRatioFrameLayout.RESIZE_MODE_ZOOM);
        ambientVideoView.setVisibility(state.enabled ? View.VISIBLE : View.GONE);
        String targetId = backgroundVideos.effectiveId();
        File targetFile = backgroundVideos.effectiveFile();
        if (state.playing && !appliedAmbientId.isEmpty() && !targetId.equals(appliedAmbientId)) {
            // Fade the current video out through black, switch, then fade in.
            if (!ambientSwitchPending) {
                ambientSwitchPending = true;
                ambientCurtain.animate().cancel();
                ambientCurtain.setVisibility(View.VISIBLE);
                ambientCurtain.animate()
                        .setStartDelay(0L)
                        .alpha(1f)
                        .setDuration(AMBIENT_FADE_OUT_MS)
                        .withEndAction(() -> {
                            ambientSwitchPending = false;
                            appliedAmbientId = "";
                            updateAmbientVideoState();
                            revealAmbientVideo();
                        });
            }
            media.setAmbientState(appliedAmbientFile, appliedAmbientId, state.enabled, state.playing);
            return;
        }
        if (!state.playing && ambientSwitchPending) {
            ambientSwitchPending = false;
            ambientCurtain.animate().cancel();
            ambientCurtain.setAlpha(0f);
            ambientCurtain.setVisibility(View.GONE);
        }
        appliedAmbientId = targetId;
        appliedAmbientFile = targetFile;
        media.setAmbientState(targetFile, targetId, state.enabled, state.playing);
    }

    private void revealAmbientVideo() {
        ambientCurtain.animate().cancel();
        ambientCurtain.animate()
                .setStartDelay(AMBIENT_FADE_IN_DELAY_MS)
                .alpha(0f)
                .setDuration(AMBIENT_FADE_IN_MS)
                .withEndAction(() -> {
                    if (!ambientSwitchPending) {
                        ambientCurtain.setVisibility(View.GONE);
                    }
                });
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
        String address = currentIpAddress();
        if (!address.isEmpty()) {
            return "http://" + address + ":" + ControlServerService.PORT;
        }
        WifiDirectOnboarding.Snapshot setup = WifiDirectOnboarding.getInstance(this).snapshot();
        if (setup.active && !setup.networkName.isEmpty()) {
            return setup.networkName + "  \u00b7  http://" + setup.address + ":" + ControlServerService.PORT;
        }
        return "USB  \u00b7  http://127.0.0.1:" + ControlServerService.PORT;
    }

    private String currentIpAddress() {
        return LanAddress.current(this);
    }
}
