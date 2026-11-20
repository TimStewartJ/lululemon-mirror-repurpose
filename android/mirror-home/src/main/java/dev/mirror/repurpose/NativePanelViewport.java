package dev.mirror.repurpose;

import android.os.Build;

/**
 * Native {@code 1080x1920} physical-pixel WebView viewport policy for the Mirror's
 * {@code APQ8016} / {@code msm8916} platform.
 *
 * <p>On both stock and clean-source AOSP {@code 6.0.1} ({@code msm8916_64}), Android reports
 * {@code Physical density: 240} ({@code DPR = 1.5}) on a {@code 1080x1920} portrait panel. Without
 * wide-viewport scaling, Android System WebView (Chromium 44) renders dashboards into a
 * {@code 720x1280} CSS stage and bilinearly upscales {@code 1.5x} onto {@code 1080x1920}, splitting
 * 1px strokes and letter crossbars across adjacent scanlines. Advertising {@link #WEBVIEW_UA_TOKEN}
 * with {@code useWideViewPort} + {@code initial-scale=0.6666666667} locks the WebView stage to
 * exact {@code 1:1} {@code 1080x1920} physical pixels while leaving {@code x86_64} emulators and
 * phones untouched.
 */
final class NativePanelViewport {
    static final String WEBVIEW_UA_TOKEN = "MirrorNative1080/1";

    private NativePanelViewport() {
    }

    static boolean isNative1080Panel() {
        return isNative1080Panel(Build.BOARD, Build.PRODUCT);
    }

    static boolean isNative1080Panel(String board, String product) {
        if ("msm8916".equals(board)) {
            return true;
        }
        return product != null && product.contains("msm8916");
    }
}
