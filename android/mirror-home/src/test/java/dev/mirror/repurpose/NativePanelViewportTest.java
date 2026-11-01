package dev.mirror.repurpose;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class NativePanelViewportTest {
    @Test
    public void detectsMsm8916PanelOnly() {
        assertTrue(NativePanelViewport.isNative1080Panel("msm8916", "aosp"));
        assertTrue(NativePanelViewport.isNative1080Panel("unknown", "msm8916_64"));
        assertFalse(NativePanelViewport.isNative1080Panel("goldfish_x86_64", "sdk_gphone_x86_64"));
        assertFalse(NativePanelViewport.isNative1080Panel("", null));
    }

    @Test
    public void dashboardAssetsSupportNative1080ViewportWithoutCspViolations() throws Exception {
        for (String page : new String[]{"custom.html", "offline.html", "custom.css", "offline.css"}) {
            String content = readDashboardAsset(page);
            assertFalse(page + " must not contain CSP-blocked inline style attributes", content.contains("style="));
        }
        for (String script : new String[]{"mirror.js", "offline.js"}) {
            String js = readDashboardAsset(script);
            assertTrue(script + " must detect MirrorNative1080/1", js.contains("MirrorNative1080/1"));
            assertTrue(script + " must lock 1:1 1080px viewport", js.contains("width=1080, initial-scale=0.6666666667"));
        }
    }

    private static String readDashboardAsset(String fileName) throws Exception {
        Path direct = Paths.get("src/main/assets/control/dashboard", fileName);
        Path path = Files.isRegularFile(direct)
                ? direct
                : Paths.get("android/mirror-home/src/main/assets/control/dashboard", fileName);
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
    }
}
