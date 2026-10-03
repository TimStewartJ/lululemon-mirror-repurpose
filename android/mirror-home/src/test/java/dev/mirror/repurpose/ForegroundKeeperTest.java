package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import dev.mirror.repurpose.ForegroundKeeper.Action;

import org.junit.Test;

public final class ForegroundKeeperTest {
    private static final long NEVER = Long.MAX_VALUE;
    private static final long LONG_AGO = 3_600_000L;

    /** A Mirror: selected as HOME, activity created, nobody at the device. */
    private static Action onAMirror(
            boolean resumed, boolean interactive, long coveredForMs, long sinceWakeMs, long sinceRelaunchMs) {
        return ForegroundKeeper.decide(
                true, true, resumed, interactive, false, coveredForMs, sinceWakeMs, sinceRelaunchMs);
    }

    @Test
    public void anotherHomeAppsIdleProcessIsEndedOnlyFromUnderTheDashboardOfAnUnattendedMirror() {
        assertEquals(true, ForegroundKeeper.mayEndOtherHome(true, true, false));
        // Not while that app's screen may be the one in front.
        assertEquals(false, ForegroundKeeper.mayEndOtherHome(true, false, false));
        // Not on a phone, where someone may want to go to their launcher.
        assertEquals(false, ForegroundKeeper.mayEndOtherHome(true, true, true));
        // Not where Mirror Home is one app among others.
        assertEquals(false, ForegroundKeeper.mayEndOtherHome(false, true, false));
        // Soon enough to come before the kernel, which has taken seven seconds.
        assertEquals(true, ForegroundKeeper.END_OTHER_HOME_AFTER_MS[0] <= 2_000L);
    }

    @Test
    public void aDashboardInFrontIsLeftAlone() {
        assertEquals(Action.NONE, onAMirror(true, true, 0, NEVER, NEVER));
    }

    @Test
    public void aSleepingDisplayIsWokenAtOnce() {
        // Created, resumed and paused within a second: launched while Android slept.
        assertEquals(Action.WAKE, onAMirror(false, false, 0, NEVER, NEVER));
        assertEquals(Action.WAKE, onAMirror(false, false, LONG_AGO, LONG_AGO, NEVER));
    }

    @Test
    public void aDisplaySomeoneTurnedOffStaysOff() {
        // A phone's power key, or a developer over ADB: they can turn it on again.
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(true, true, false, false, true, LONG_AGO, NEVER, NEVER));
    }

    @Test
    public void wakingIsNotRepeatedBeforeItCanTakeEffect() {
        assertEquals(
                Action.NONE,
                onAMirror(false, false, 9_000, ForegroundKeeper.WAKE_RETRY_MS - 1, NEVER));
        assertEquals(
                Action.WAKE,
                onAMirror(false, false, 10_000, ForegroundKeeper.WAKE_RETRY_MS, NEVER));
    }

    @Test
    public void anotherScreenMayStayInFrontForAMoment() {
        assertEquals(
                Action.NONE,
                onAMirror(false, true, ForegroundKeeper.COVERED_GRACE_MS - 1, NEVER, NEVER));
        assertEquals(
                Action.RELAUNCH,
                onAMirror(false, true, ForegroundKeeper.COVERED_GRACE_MS, NEVER, NEVER));
        assertEquals(Action.RELAUNCH, onAMirror(false, true, LONG_AGO, NEVER, NEVER));
    }

    @Test
    public void aPersonAtTheDeviceIsNotInterrupted() {
        // An input device or a connected computer: someone may be using that screen.
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(true, true, false, true, true, LONG_AGO, NEVER, NEVER));
    }

    @Test
    public void theDashboardIsNotRelaunchedOverAndOver() {
        assertEquals(
                Action.NONE,
                onAMirror(false, true, LONG_AGO, NEVER, ForegroundKeeper.RELAUNCH_RETRY_MS - 1));
        assertEquals(
                Action.RELAUNCH,
                onAMirror(false, true, LONG_AGO, NEVER, ForegroundKeeper.RELAUNCH_RETRY_MS));
    }

    @Test
    public void nothingHappensUnlessMirrorHomeIsTheSelectedHome() {
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(false, true, false, false, false, LONG_AGO, NEVER, NEVER));
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(false, true, false, true, false, LONG_AGO, NEVER, NEVER));
    }

    @Test
    public void anActivityThatWasNeverCreatedIsLeftToTheStartUpLogic() {
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(true, false, false, false, false, LONG_AGO, NEVER, NEVER));
        assertEquals(
                Action.NONE,
                ForegroundKeeper.decide(true, false, false, true, false, LONG_AGO, NEVER, NEVER));
    }

    @Test
    public void onlyADeviceWithNothingToOperateItCountsAsHavingNoInput() {
        // Configuration's values: 1 is "none" for each; 0 is "not known".
        assertEquals(false, ForegroundKeeper.hasInputDevices(1, 1, 1));
        assertEquals(true, ForegroundKeeper.hasInputDevices(3, 1, 1));
        assertEquals(true, ForegroundKeeper.hasInputDevices(1, 2, 1));
        assertEquals(true, ForegroundKeeper.hasInputDevices(1, 1, 2));
        assertEquals(true, ForegroundKeeper.hasInputDevices(0, 0, 0));
    }

    @Test
    public void theGraceIsLongerThanTheCheckInterval() {
        // Otherwise one late check could act on a screen that only just appeared.
        assertEquals(true, ForegroundKeeper.COVERED_GRACE_MS >= 2 * ForegroundKeeper.CHECK_INTERVAL_MS);
        assertEquals(true, ForegroundKeeper.WAKE_RETRY_MS >= ForegroundKeeper.CHECK_INTERVAL_MS);
    }
}
