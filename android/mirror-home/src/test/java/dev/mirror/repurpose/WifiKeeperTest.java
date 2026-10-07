package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class WifiKeeperTest {
    private static final long NOON = 1_791_100_000_000L;
    private static final long MINUTE = 60_000L;

    /** Android's Wi-Fi as far as the keeper sees it. */
    private static final class FakeRadio implements WifiKeeper.Radio {
        boolean present = true;
        boolean on = true;
        boolean connected = true;
        boolean known = true;
        boolean busy;
        String doing = "COMPLETED";
        /** Asked to join again, Android does. */
        boolean obliging;
        /** Switched off and on, Wi-Fi finds its network. */
        boolean curedByRestart;
        final List<String> asked = new ArrayList<>();

        @Override
        public boolean present() {
            return present;
        }

        @Override
        public boolean on() {
            return on;
        }

        @Override
        public boolean connected() {
            return connected;
        }

        @Override
        public boolean known() {
            return known;
        }

        @Override
        public boolean busy() {
            return busy;
        }

        @Override
        public String supplicant() {
            return on ? doing : "OFF";
        }

        @Override
        public void rejoin() {
            asked.add("rejoin");
            if (obliging) {
                connected = true;
                doing = "COMPLETED";
            }
        }

        @Override
        public void power(boolean wanted) {
            asked.add(wanted ? "on" : "off");
            on = wanted;
            if (wanted && curedByRestart) {
                connected = true;
                doing = "COMPLETED";
            }
        }

        @Override
        public JSONObject look() {
            try {
                return new JSONObject().put("wifi", on ? "on" : "off").put("doing", supplicant());
            } catch (org.json.JSONException impossible) {
                throw new AssertionError(impossible);
            }
        }

        @Override
        public JSONObject signal() {
            try {
                return connected ? new JSONObject().put("rssi", -45).put("frequencyMhz", 5220) : null;
            } catch (org.json.JSONException impossible) {
                throw new AssertionError(impossible);
            }
        }

        void lose() {
            connected = false;
            doing = "SCANNING";
        }
    }

    /** What the keeper wrote down, and the copies of the log it asked for. */
    private static final class FakeBook implements WifiKeeper.Book {
        final List<String> written = new ArrayList<>();
        final List<JSONObject> details = new ArrayList<>();
        final List<String> copies = new ArrayList<>();

        @Override
        public void write(String what, JSONObject more) {
            written.add(what);
            details.add(more == null ? new JSONObject() : more);
        }

        @Override
        public void copyLog(String reason) {
            copies.add(reason);
        }

        JSONObject last(String what) {
            return details.get(written.lastIndexOf(what));
        }

        int count(String what) {
            int count = 0;
            for (String entry : written) {
                count += entry.equals(what) ? 1 : 0;
            }
            return count;
        }
    }

    private final FakeRadio radio = new FakeRadio();
    private final FakeBook book = new FakeBook();
    private final WifiKeeper keeper = new WifiKeeper(radio, book);
    private long now = 3_600_000L;

    /** Looks as the keeper's own clock would, for so long. */
    private void pass(long millis) {
        long until = now + millis;
        while (now < until) {
            long wait = keeper.check(now, NOON + now);
            now += Math.min(wait, WifiKeeper.LOOK_EVERY_MS);
        }
    }

    private JSONObject report() throws Exception {
        return keeper.snapshot(now);
    }

    @Test
    public void withANetworkItOnlyWritesThatThereIsOneAndLooksRarely() throws Exception {
        assertEquals(WifiKeeper.LOOK_RARELY_MS, keeper.check(now, NOON));
        pass(60 * MINUTE);
        assertTrue(radio.asked.isEmpty());
        assertEquals(java.util.Collections.singletonList("connected"), book.written);
        assertTrue(book.copies.isEmpty());
        JSONObject report = report();
        assertEquals("connected", report.getString("state"));
        assertTrue(report.getBoolean("supported"));
        assertTrue(report.isNull("withoutNetworkSeconds"));
        assertTrue(report.isNull("lastOutage"));
        assertEquals(0, report.getInt("outages"));
    }

    @Test
    public void aLostNetworkIsWrittenDownAtOnceWithHowItWasReceivedBefore() throws Exception {
        keeper.check(now, NOON);
        now += 4 * MINUTE;
        radio.lose();
        assertEquals(WifiKeeper.LOOK_EVERY_MS, keeper.check(now, NOON + now));

        assertEquals("lost", book.written.get(1));
        JSONObject lost = book.last("lost");
        assertEquals("SCANNING", lost.getString("doing"));
        assertEquals(-45, lost.getJSONObject("before").getInt("rssi"));
        assertEquals(240, lost.getJSONObject("before").getLong("secondsAgo"));
        // Android's log says why, and only for some minutes.
        assertEquals(java.util.Collections.singletonList("wifi-lost"), book.copies);
        // Android gets its minute to find the network by itself.
        assertTrue(radio.asked.isEmpty());
        JSONObject report = report();
        assertEquals("watching", report.getString("state"));
        assertEquals(1, report.getInt("outages"));
        assertEquals(NOON + now, report.getLong("since"));
    }

    @Test
    public void aNetworkThatAndroidFindsByItselfIsLeftToAndroid() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        pass(45_000);
        radio.connected = true;
        radio.doing = "COMPLETED";
        pass(MINUTE);

        assertTrue(radio.asked.isEmpty());
        JSONObject back = book.last("back");
        assertEquals(45, back.getLong("after"));
        assertEquals(0, back.getInt("rejoins"));
        // Too short to be worth another copy of the log.
        assertEquals(java.util.Collections.singletonList("wifi-lost"), book.copies);
        JSONObject report = report();
        assertEquals("connected", report.getString("state"));
        assertEquals(45, report.getJSONObject("lastOutage").getLong("seconds"));
        assertTrue(report.isNull("withoutNetworkSeconds"));
    }

    @Test
    public void afterAMinuteItAsksAndroidToJoinAgainAndThenEveryTwoMinutes() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        pass(59_000);
        assertTrue(radio.asked.isEmpty());
        pass(8 * MINUTE);

        // At one, three, five and seven minutes.
        assertEquals(java.util.Arrays.asList("rejoin", "rejoin", "rejoin", "rejoin"), radio.asked);
        assertEquals(4, book.count("rejoin"));
        JSONObject first = book.details.get(book.written.indexOf("rejoin"));
        assertEquals(1, first.getInt("attempt"));
        assertEquals(60, first.getLong("without"));
        assertEquals("SCANNING", first.getString("doing"));
        assertEquals(java.util.Arrays.asList("wifi-lost", "wifi-rejoin"), book.copies);
        assertEquals("rejoining", report().getString("state"));
        assertEquals(4, report().getInt("rejoins"));
        assertEquals("rejoin", report().getJSONObject("lastAction").getString("what"));
    }

    @Test
    public void anAndroidThatJoinsWhenAskedIsBackAfterTheFirstAsking() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        radio.obliging = true;
        pass(5 * MINUTE);

        assertEquals(java.util.Collections.singletonList("rejoin"), radio.asked);
        JSONObject back = book.last("back");
        assertEquals(1, back.getInt("rejoins"));
        assertEquals(0, back.getInt("wifiRestarts"));
        assertEquals(1, back.getJSONObject("states").getInt("SCANNING"));
        assertEquals(java.util.Arrays.asList("wifi-lost", "wifi-rejoin", "wifi-back"), book.copies);
        assertEquals("connected", report().getString("state"));
        assertEquals(0, report().getInt("rejoins"));
    }

    @Test
    public void afterTenMinutesItSwitchesWifiOffAndOnAndAsksAgainAtOnce() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        pass(10 * MINUTE + 45_000);

        // One, three, five, seven and nine minutes; then off, on, and asking straight away.
        assertEquals(
                java.util.Arrays.asList("rejoin", "rejoin", "rejoin", "rejoin", "rejoin", "off", "on", "rejoin"),
                radio.asked);
        assertEquals(1, book.count("restart-wifi"));
        assertEquals(1, book.count("wifi-on"));
        assertEquals(600, book.last("restart-wifi").getLong("without"));
        assertEquals(java.util.Arrays.asList("wifi-lost", "wifi-rejoin", "wifi-restart"), book.copies);
        assertEquals(1, report().getInt("wifiRestarts"));
    }

    @Test
    public void wifiStaysOffForSomeSecondsHoweverSoonItIsLookedAtAgain() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        pass(10 * MINUTE);
        keeper.check(now, NOON + now);
        assertEquals("off", radio.asked.get(radio.asked.size() - 1));
        // Android says at once that Wi-Fi is going off, and the keeper looks.
        now += 200;
        assertEquals(WifiKeeper.OFF_FOR_MS, keeper.check(now, NOON + now));
        assertEquals("off", radio.asked.get(radio.asked.size() - 1));
        assertEquals("restarting-wifi", report().getString("state"));
        now += WifiKeeper.OFF_FOR_MS;
        keeper.check(now, NOON + now);
        assertEquals("on", radio.asked.get(radio.asked.size() - 1));
    }

    @Test
    public void aRestartOfWifiThatHelpsEndsTheOutage() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        radio.curedByRestart = true;
        pass(15 * MINUTE);

        JSONObject back = book.last("back");
        assertEquals(1, back.getInt("wifiRestarts"));
        assertEquals(5, back.getInt("rejoins"));
        JSONObject outage = report().getJSONObject("lastOutage");
        assertEquals(1, outage.getInt("wifiRestarts"));
        assertTrue(outage.getLong("seconds") >= 600 && outage.getLong("seconds") < 660);
        assertEquals("connected", report().getString("state"));
    }

    @Test
    public void wifiIsSwitchedOffAndOnAgainAfterHalfAnHourAndThenHourly() {
        assertEquals(10 * MINUTE, WifiKeeper.restartDue(0));
        assertEquals(30 * MINUTE, WifiKeeper.restartDue(1));
        assertEquals(60 * MINUTE, WifiKeeper.restartDue(2));
        assertEquals(120 * MINUTE, WifiKeeper.restartDue(3));
        assertEquals(180 * MINUTE, WifiKeeper.restartDue(4));

        keeper.check(now, NOON);
        radio.lose();
        pass(3 * 60 * MINUTE + 30_000);
        int restarts = 0;
        for (String request : radio.asked) {
            restarts += request.equals("off") ? 1 : 0;
        }
        // At ten and thirty minutes, and at one, two and three hours.
        assertEquals(5, restarts);
    }

    @Test
    public void aNightWithoutANetworkDoesNotFillTheJournal() {
        keeper.check(now, NOON);
        radio.lose();
        pass(12 * 60 * MINUTE);

        int rejoins = 0;
        for (String request : radio.asked) {
            rejoins += request.equals("rejoin") ? 1 : 0;
        }
        // It went on asking all night: every two minutes at first, then every five.
        assertTrue("Asked " + rejoins + " times", rejoins > 130);
        // And wrote the first ten askings and every twelfth after.
        assertTrue("Wrote " + book.count("rejoin"), book.count("rejoin") <= WifiKeeper.REJOINS_WRITTEN + rejoins / 12);
        assertTrue("Wrote " + book.written.size() + " lines", book.written.size() < 80);
    }

    @Test
    public void whatWifiDoesWhileItSearchesIsWrittenOnceAndTheStepsToAConnectionEachTime() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        keeper.check(now, NOON);
        for (int round = 0; round < 100; round++) {
            for (String doing : new String[]{"DISCONNECTED", "SCANNING", "ASSOCIATING"}) {
                radio.doing = doing;
                now += 1_000;
                keeper.check(now, NOON + now);
            }
        }
        // Searching and resting once each; joining until the allowance is used up.
        assertEquals(WifiKeeper.STATES_WRITTEN, book.count("state"));
        radio.connected = true;
        radio.doing = "COMPLETED";
        keeper.check(now, NOON + now);
        JSONObject states = book.last("back").getJSONObject("states");
        assertEquals(100, states.getInt("ASSOCIATING"));
        assertEquals(100, states.getInt("DISCONNECTED"));
        assertEquals(101, states.getInt("SCANNING"));
    }

    @Test
    public void aRefusedPasswordIsWrittenDownAFewTimes() {
        keeper.check(now, NOON);
        radio.lose();
        keeper.check(now, NOON);
        for (int index = 0; index < 20; index++) {
            keeper.refused();
        }
        assertEquals(5, book.count("authentication-failed"));
    }

    @Test
    public void aMirrorThatWasNeverOnANetworkIsLeftToWhoeverSetsItUp() throws Exception {
        radio.connected = false;
        radio.known = false;
        radio.doing = "DISCONNECTED";
        pass(3 * 60 * MINUTE);

        assertTrue(radio.asked.isEmpty());
        assertEquals("without", book.written.get(0));
        assertTrue(book.copies.isEmpty());
        assertEquals("nothing-saved", report().getString("state"));
        assertEquals(0, report().getInt("outages"));
    }

    @Test
    public void startingWithoutANetworkIsNotALossButIsHelpedAllTheSame() throws Exception {
        radio.connected = false;
        radio.doing = "SCANNING";
        radio.obliging = true;
        pass(2 * MINUTE);

        assertEquals(java.util.Collections.singletonList("rejoin"), radio.asked);
        assertEquals("without", book.written.get(0));
        assertEquals(1, book.count("connected"));
        assertEquals(0, book.count("lost"));
        assertEquals(0, book.count("back"));
        assertFalse(book.copies.contains("wifi-lost"));
        assertEquals(0, report().getInt("outages"));
        assertTrue(report().isNull("lastOutage"));
    }

    @Test
    public void wifiThatIsOffWithANetworkToGoBackToIsSwitchedOn() throws Exception {
        keeper.check(now, NOON);
        radio.on = false;
        radio.connected = false;
        pass(50_000);
        assertTrue(radio.asked.isEmpty());
        pass(15_000);
        assertEquals(java.util.Collections.singletonList("on"), radio.asked);
        assertEquals(1, book.count("switched-on"));
        // And once it is on, Android is asked to join without waiting further.
        radio.doing = "SCANNING";
        pass(20_000);
        assertEquals(java.util.Arrays.asList("on", "rejoin"), radio.asked);
    }

    @Test
    public void wifiIsNotSwitchedOffUnderAPhoneThatIsSettingTheMirrorUp() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        radio.busy = true;
        pass(45 * MINUTE);
        assertFalse(radio.asked.contains("off"));
        assertTrue(radio.asked.contains("rejoin"));
        radio.busy = false;
        pass(MINUTE);
        assertTrue(radio.asked.contains("off"));
    }

    @Test
    public void anAndroidThatManagesWifiItselfIsLeftAlone() throws Exception {
        radio.present = false;
        radio.connected = false;
        pass(60 * MINUTE);
        assertTrue(radio.asked.isEmpty());
        assertTrue(book.written.isEmpty());
        assertEquals("unsupported", report().getString("state"));
        assertFalse(report().getBoolean("supported"));
    }

    @Test
    public void everyOutageStartsCountingAfresh() throws Exception {
        keeper.check(now, NOON);
        radio.lose();
        radio.curedByRestart = true;
        pass(12 * MINUTE);
        assertEquals("connected", report().getString("state"));
        radio.curedByRestart = false;
        radio.asked.clear();

        radio.lose();
        pass(5 * MINUTE + 30_000);
        // One, three and five minutes again, and no restart until ten.
        assertEquals(java.util.Arrays.asList("rejoin", "rejoin", "rejoin"), radio.asked);
        assertEquals(2, report().getInt("outages"));
        assertEquals(2, book.count("lost"));
    }
}
