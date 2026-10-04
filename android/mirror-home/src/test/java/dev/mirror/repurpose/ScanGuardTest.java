package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONObject;
import org.junit.Test;

import java.lang.reflect.InvocationTargetException;
import java.util.ArrayList;
import java.util.List;

public class ScanGuardTest {
    private static final long NOON = 1_791_100_000_000L;

    /** Android's Wi-Fi service as far as the guard sees it. */
    private static final class FakeAndroid implements ScanGuard.Switch {
        boolean present = true;
        boolean scanning = true;
        /** Wi-Fi is on and has lost its network. */
        boolean searching;
        /** Android takes the request and goes on as before. */
        boolean deaf;
        Exception refusal;
        final List<Boolean> asked = new ArrayList<>();

        @Override
        public boolean present() {
            return present;
        }

        @Override
        public boolean searching() {
            return searching;
        }

        @Override
        public boolean scanning() throws Exception {
            if (refusal != null) {
                throw refusal;
            }
            return scanning;
        }

        @Override
        public void scan(boolean wanted) throws Exception {
            if (refusal != null) {
                throw refusal;
            }
            asked.add(wanted);
            if (!deaf) {
                scanning = wanted;
            }
        }
    }

    /** Mirror Home's settings, which outlive a process. */
    private static final class FakeSettings implements ScanGuard.Memory {
        boolean wanted;
        String kept = "";

        @Override
        public boolean wanted() {
            return wanted;
        }

        @Override
        public void want(boolean wanted) {
            this.wanted = wanted;
        }

        @Override
        public String kept() {
            return kept;
        }

        @Override
        public void keep(String value) {
            kept = value;
        }
    }

    private final FakeAndroid android = new FakeAndroid();
    private final FakeSettings settings = new FakeSettings();

    private ScanGuard guard(String boot) {
        return new ScanGuard(android, settings, boot);
    }

    @Test
    public void nothingIsTouchedUntilTheOwnerTurnsItOn() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.check(NOON);
        assertTrue(android.asked.isEmpty());
        assertEquals("", settings.kept);
        JSONObject report = guard.snapshot();
        assertFalse(report.getBoolean("enabled"));
        assertTrue(report.getBoolean("supported"));
        assertEquals("off", report.getString("state"));
        assertTrue(report.getBoolean("scanningWhileConnected"));
        assertTrue(report.isNull("appliedAt"));
        assertEquals(1, report.getInt("checks"));
        assertEquals(NOON, report.getLong("checkedAt"));
    }

    @Test
    public void turnedOnItStopsTheScansAtOnceAndSaysSo() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        assertFalse(android.scanning);
        assertTrue(settings.wanted);
        JSONObject report = guard.snapshot();
        assertTrue(report.getBoolean("enabled"));
        assertEquals("applied", report.getString("state"));
        assertEquals("", report.getString("detail"));
        assertFalse(report.getBoolean("scanningWhileConnected"));
        assertEquals(NOON, report.getLong("appliedAt"));
        assertEquals(1, report.getInt("applied"));
        // The status carries the short form.
        assertEquals("applied", guard.summary().getString("state"));
        assertFalse(guard.summary().has("checks"));
    }

    @Test
    public void aSwitchThatStillHoldsIsLeftAlone() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        guard.check(NOON + 300_000);
        guard.check(NOON + 600_000);
        assertEquals(1, android.asked.size());
        JSONObject report = guard.snapshot();
        assertEquals(1, report.getInt("applied"));
        assertEquals(NOON, report.getLong("appliedAt"));
        assertEquals(NOON + 600_000, report.getLong("checkedAt"));
    }

    @Test
    public void whatAndroidForgetsIsSetAgain() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        // Android restarted its Wi-Fi service and scans again.
        android.scanning = true;
        guard.check(NOON + 300_000);
        assertFalse(android.scanning);
        JSONObject report = guard.snapshot();
        assertEquals("applied", report.getString("state"));
        assertEquals(2, report.getInt("applied"));
        assertEquals(NOON + 300_000, report.getLong("appliedAt"));
    }

    @Test
    public void afterARestartOfTheMirrorItIsSetAgainWithoutBeingAsked() throws Exception {
        guard("boot-1").setEnabled(true, NOON);
        assertEquals("boot-1=true", settings.kept);
        // Android starts afresh, scanning as it came; so does Mirror Home.
        android.scanning = true;
        ScanGuard later = guard("boot-2");
        later.check(NOON + 3_600_000);
        assertFalse(android.scanning);
        assertEquals("boot-2=true", settings.kept);
        assertEquals("applied", later.snapshot().getString("state"));
    }

    @Test
    public void mirrorHomeStartingAgainFindsItsOwnWorkAndKeepsWhatWasThereBefore() throws Exception {
        guard("boot-1").setEnabled(true, NOON);
        // Mirror Home was updated; Android was not restarted and still does not scan.
        ScanGuard later = guard("boot-1");
        later.check(NOON + 60_000);
        assertEquals(1, android.asked.size());
        assertEquals("boot-1=true", settings.kept);
        JSONObject report = later.snapshot();
        assertEquals("applied", report.getString("state"));
        assertEquals(0, report.getInt("applied"));
        assertEquals(NOON + 60_000, report.getLong("appliedAt"));
        // Turned off now, Android scans again as it did before the guard came.
        later.setEnabled(false, NOON + 120_000);
        assertTrue(android.scanning);
    }

    @Test
    public void turnedOffItPutsBackWhatAndroidDidBefore() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        guard.setEnabled(false, NOON + 1_000);
        assertTrue(android.scanning);
        assertFalse(settings.wanted);
        assertEquals("", settings.kept);
        JSONObject report = guard.snapshot();
        assertEquals("off", report.getString("state"));
        assertTrue(report.getBoolean("scanningWhileConnected"));
        assertTrue(report.isNull("appliedAt"));
        // And nothing more after that.
        guard.check(NOON + 2_000);
        assertEquals(2, android.asked.size());
    }

    @Test
    public void anAndroidThatDidNotScanBeforeIsNotMadeToScan() throws Exception {
        android.scanning = false;
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        assertEquals("boot-1=false", settings.kept);
        assertEquals("applied", guard.snapshot().getString("state"));
        guard.setEnabled(false, NOON + 1_000);
        assertFalse(android.scanning);
        assertTrue(android.asked.isEmpty());
        assertEquals("off", guard.snapshot().getString("state"));
    }

    @Test
    public void whatWasKeptOfAnEarlierStartOfAndroidIsNotPutBack() throws Exception {
        // Left behind by a start of Android that is over; this one was never touched.
        settings.kept = "boot-1=false";
        ScanGuard guard = guard("boot-2");
        guard.check(NOON);
        assertTrue(android.asked.isEmpty());
        assertTrue(android.scanning);
        assertEquals("off", guard.snapshot().getString("state"));
    }

    @Test
    public void anAndroidThatGoesOnScanningIsReported() throws Exception {
        android.deaf = true;
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        JSONObject report = guard.snapshot();
        assertEquals("error", report.getString("state"));
        assertEquals("Android went on scanning", report.getString("detail"));
        assertTrue(report.getBoolean("scanningWhileConnected"));
        assertTrue(report.isNull("appliedAt"));
        // It keeps trying, and says so when Android comes round.
        android.deaf = false;
        guard.check(NOON + 300_000);
        assertEquals("applied", guard.snapshot().getString("state"));
    }

    @Test
    public void anAndroidThatRefusesIsReportedWithItsReason() throws Exception {
        android.refusal = new InvocationTargetException(
                new SecurityException("WifiService: Neither user 10061 nor current process has CHANGE_WIFI_STATE"));
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        JSONObject report = guard.snapshot();
        assertEquals("error", report.getString("state"));
        assertEquals(
                "SecurityException: WifiService: Neither user 10061 nor current process has CHANGE_WIFI_STATE",
                report.getString("detail"));
        assertTrue(report.isNull("scanningWhileConnected"));
        // The owner's wish stands, for when Android allows it.
        assertTrue(report.getBoolean("enabled"));
    }

    @Test
    public void whereAndroidHasNoSuchSwitchItCannotBeTurnedOn() throws Exception {
        android.present = false;
        ScanGuard guard = guard("boot-1");
        try {
            guard.setEnabled(true, NOON);
            fail("Turned on without a switch");
        } catch (IllegalStateException expected) {
            assertEquals("This Android has no switch for scanning while connected", expected.getMessage());
        }
        assertFalse(settings.wanted);
        guard.check(NOON);
        JSONObject report = guard.snapshot();
        assertFalse(report.getBoolean("supported"));
        assertEquals("off", report.getString("state"));
        assertTrue(report.isNull("scanningWhileConnected"));
        assertTrue(android.asked.isEmpty());
        // Turning it off is always allowed.
        guard.setEnabled(false, NOON);
    }

    @Test
    public void withoutAWayToTellOneStartOfAndroidFromAnotherItStillWorks() throws Exception {
        ScanGuard guard = guard(null);
        guard.setEnabled(true, NOON);
        assertFalse(android.scanning);
        guard.setEnabled(false, NOON + 1_000);
        assertTrue(android.scanning);
    }

    @Test
    public void whileWifiHasNoNetworkAndroidLooksForOneAsItCame() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        // The access point goes away.
        android.searching = true;
        guard.check(NOON + 60_000);
        assertTrue(android.scanning);
        JSONObject report = guard.snapshot();
        assertEquals("waiting", report.getString("state"));
        assertTrue(report.getBoolean("enabled"));
        assertTrue(report.getBoolean("scanningWhileConnected"));
        assertTrue(report.isNull("appliedAt"));
        // For as long as it stays away, nothing more is touched.
        guard.check(NOON + 360_000);
        assertEquals(2, android.asked.size());
        // It comes back, and so does the guard.
        android.searching = false;
        guard.check(NOON + 400_000);
        assertFalse(android.scanning);
        report = guard.snapshot();
        assertEquals("applied", report.getString("state"));
        assertEquals(2, report.getInt("applied"));
        assertEquals(NOON + 400_000, report.getLong("appliedAt"));
        // What Android did before the guard came is still known.
        guard.setEnabled(false, NOON + 500_000);
        assertTrue(android.scanning);
    }

    @Test
    public void aMirrorThatStartsWithoutItsNetworkIsNotTouchedUntilItHasOne() throws Exception {
        settings.wanted = true;
        android.searching = true;
        ScanGuard guard = guard("boot-1");
        guard.check(NOON);
        assertTrue(android.asked.isEmpty());
        assertEquals("", settings.kept);
        assertEquals("waiting", guard.snapshot().getString("state"));
        android.searching = false;
        guard.check(NOON + 20_000);
        assertFalse(android.scanning);
        assertEquals("boot-1=true", settings.kept);
        assertEquals("applied", guard.snapshot().getString("state"));
    }

    @Test
    public void turnedOffWhileWifiHasNoNetworkItLeavesAndroidAsItCame() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.setEnabled(true, NOON);
        android.searching = true;
        guard.check(NOON + 60_000);
        guard.setEnabled(false, NOON + 120_000);
        assertTrue(android.scanning);
        assertEquals("", settings.kept);
        assertEquals("off", guard.snapshot().getString("state"));
    }

    @Test
    public void scansWhileConnectedAreCountedAndThoseWhileLookingForANetworkAreNot() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.check(NOON);
        guard.scanned(true, NOON + 1_000);
        guard.scanned(false, NOON + 2_000);
        guard.scanned(true, NOON + 360_000);
        JSONObject scans = guard.snapshot().getJSONObject("scans");
        assertEquals(2, scans.getInt("whileConnected"));
        assertEquals(NOON + 360_000, scans.getLong("lastAt"));
        // Nothing was applied, so nothing came since.
        assertEquals(0, scans.getInt("sinceApplied"));
        assertTrue(guard("boot-1").snapshot().getJSONObject("scans").isNull("lastAt"));
    }

    @Test
    public void aScanThatArrivesWithTheGuardOnShowsThatItDoesNotHold() throws Exception {
        ScanGuard guard = guard("boot-1");
        guard.scanned(true, NOON - 5_000);
        guard.setEnabled(true, NOON);
        // One that Android had begun before the switch was set still ends.
        guard.scanned(true, NOON + 3_000);
        assertEquals(0, guard.snapshot().getJSONObject("scans").getInt("sinceApplied"));
        // The Mirror lost its network for a moment and looked for it: that is as it should be.
        guard.scanned(false, NOON + 600_000);
        assertEquals(0, guard.snapshot().getJSONObject("scans").getInt("sinceApplied"));
        guard.scanned(true, NOON + 900_000);
        JSONObject scans = guard.snapshot().getJSONObject("scans");
        assertEquals(1, scans.getInt("sinceApplied"));
        assertEquals(3, scans.getInt("whileConnected"));
        // Set again after Android forgot it, the count starts over.
        android.scanning = true;
        guard.check(NOON + 1_200_000);
        assertEquals(0, guard.snapshot().getJSONObject("scans").getInt("sinceApplied"));
    }
}
