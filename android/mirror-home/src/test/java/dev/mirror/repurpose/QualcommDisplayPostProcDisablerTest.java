package dev.mirror.repurpose;

import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public final class QualcommDisplayPostProcDisablerTest {

    @Test
    public void marksAbsentWhenQualcommServicesAreMissing() throws Exception {
        FakeTransport transport = new FakeTransport(false, false);
        QualcommDisplayPostProcDisabler disabler =
                new QualcommDisplayPostProcDisabler(transport, new DirectExecutor(), null);

        disabler.ensureLinearNow(false);
        JSONObject snap = disabler.snapshot();

        assertEquals(QualcommDisplayPostProcDisabler.STATE_ABSENT, snap.getString("state"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_ABSENT, snap.getString("cabl"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_ABSENT, snap.getString("pp"));
        assertEquals(1, snap.getInt("attempts"));
        assertEquals(0, transport.bindCount);

        // Subsequent non-forced call is a fast no-op once absent.
        disabler.ensureLinearNow(false);
        assertEquals(1, disabler.snapshot().getInt("attempts"));
    }

    @Test
    public void retriesAsyncDaemonSocketThenSyncsStartBeforeStopAndUnbinds() throws Exception {
        FakeTransport transport = new FakeTransport(true, true);
        // Simulate CABLService and PPService returning false twice while DaemonConnector connects.
        transport.cablStartFailuresRemaining = 2;
        transport.ppStartFailuresRemaining = 1;

        QualcommDisplayPostProcDisabler disabler =
                new QualcommDisplayPostProcDisabler(transport, new DirectExecutor(), null);

        disabler.ensureLinearNow(false);
        JSONObject snap = disabler.snapshot();

        assertEquals(QualcommDisplayPostProcDisabler.STATE_DISABLED, snap.getString("state"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_DISABLED, snap.getString("cabl"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_DISABLED, snap.getString("pp"));
        assertEquals(123456L, snap.getLong("appliedAt"));
        assertEquals("", snap.getString("detail"));
        assertEquals(2, transport.closedSessions);

        // Verify exact Binder sequence: PP runs BEFORE CABL so PPService.startPP() cannot
        // re-enable CABL after CABLService.stopCABL().
        // PP start x2 -> stop -> status, then CABL start(1) x3 -> stop(0).
        assertEquals(
                "com.android.display.IPPService:1:null",
                transport.calls.get(0));
        assertEquals(
                "com.android.display.IPPService:1:null",
                transport.calls.get(1));
        assertEquals(
                "com.android.display.IPPService:2:null",
                transport.calls.get(2));
        assertEquals(
                "com.android.display.IPPService:4:null",
                transport.calls.get(3));
        assertEquals(
                "com.android.cabl.ICABLService:1:1",
                transport.calls.get(4));
        assertEquals(
                "com.android.cabl.ICABLService:1:1",
                transport.calls.get(5));
        assertEquals(
                "com.android.cabl.ICABLService:1:1",
                transport.calls.get(6));
        assertEquals(
                "com.android.cabl.ICABLService:1:0",
                transport.calls.get(7));

        // Settled state skips redundant Binder cycles unless forced.
        disabler.ensureLinearNow(false);
        assertEquals(1, disabler.snapshot().getInt("attempts"));

        disabler.ensureLinearNow(true);
        assertEquals(2, disabler.snapshot().getInt("attempts"));
    }

    @Test
    public void reportsErrorAndAlwaysClosesSessionWhenStopFailsThenRecoversOnRetry()
            throws Exception {
        FakeTransport transport = new FakeTransport(true, true);
        transport.rejectCablStop = true;

        QualcommDisplayPostProcDisabler disabler =
                new QualcommDisplayPostProcDisabler(transport, new DirectExecutor(), null);

        disabler.ensureLinearNow(false);
        JSONObject first = disabler.snapshot();
        assertEquals(QualcommDisplayPostProcDisabler.STATE_ERROR, first.getString("state"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_ERROR, first.getString("cabl"));
        assertEquals(QualcommDisplayPostProcDisabler.STATE_DISABLED, first.getString("pp"));
        assertTrue(first.getString("detail").contains("cabl: stop rejected"));
        assertEquals(2, transport.closedSessions);

        // Because state is STATE_ERROR, a regular ensureLinearNow(false) retries and recovers.
        transport.rejectCablStop = false;
        disabler.ensureLinearNow(false);
        JSONObject second = disabler.snapshot();
        assertEquals(QualcommDisplayPostProcDisabler.STATE_DISABLED, second.getString("state"));
        assertEquals(4, transport.closedSessions);
    }

    private static final class FakeTransport implements QualcommDisplayPostProcDisabler.Transport {
        final boolean hasCabl;
        final boolean hasPp;
        final List<String> calls = new ArrayList<>();
        int cablStartFailuresRemaining;
        int ppStartFailuresRemaining;
        boolean rejectCablStop;
        int bindCount;
        int closedSessions;

        FakeTransport(boolean hasCabl, boolean hasPp) {
            this.hasCabl = hasCabl;
            this.hasPp = hasPp;
        }

        @Override
        public boolean hasService(String packageName, String className) {
            if (QualcommDisplayPostProcDisabler.CABL_PACKAGE.equals(packageName)) {
                return hasCabl;
            }
            if (QualcommDisplayPostProcDisabler.PP_PACKAGE.equals(packageName)) {
                return hasPp;
            }
            return false;
        }

        @Override
        public QualcommDisplayPostProcDisabler.Session bind(
                String action, String packageName, String className, long timeoutMs) {
            bindCount++;
            return new QualcommDisplayPostProcDisabler.Session() {
                @Override
                public boolean transactBoolean(String descriptor, int code, Integer arg) {
                    calls.add(descriptor + ":" + code + ":" + arg);
                    if (QualcommDisplayPostProcDisabler.CABL_DESCRIPTOR.equals(descriptor)) {
                        if (arg != null
                                && arg == QualcommDisplayPostProcDisabler.CABL_CONTROL_START) {
                            if (cablStartFailuresRemaining > 0) {
                                cablStartFailuresRemaining--;
                                return false;
                            }
                            return true;
                        }
                        return !rejectCablStop;
                    }
                    if (code == QualcommDisplayPostProcDisabler.TRANSACTION_PP_START) {
                        if (ppStartFailuresRemaining > 0) {
                            ppStartFailuresRemaining--;
                            return false;
                        }
                        return true;
                    }
                    if (code == QualcommDisplayPostProcDisabler.TRANSACTION_PP_STOP) {
                        return true;
                    }
                    if (code == QualcommDisplayPostProcDisabler.TRANSACTION_PP_STATUS) {
                        return false;
                    }
                    return false;
                }

                @Override
                public void close() {
                    closedSessions++;
                }
            };
        }

        @Override
        public void sleepMs(long millis) {
        }

        @Override
        public long elapsedRealtimeMs() {
            return 500_000L;
        }

        @Override
        public long currentTimeMs() {
            return 123456L;
        }
    }

    private static final class DirectExecutor extends AbstractExecutorService {
        private boolean shutdown;

        @Override
        public void shutdown() {
            shutdown = true;
        }

        @Override
        public List<Runnable> shutdownNow() {
            shutdown = true;
            return new ArrayList<>();
        }

        @Override
        public boolean isShutdown() {
            return shutdown;
        }

        @Override
        public boolean isTerminated() {
            return shutdown;
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return true;
        }

        @Override
        public void execute(Runnable command) {
            command.run();
        }
    }
}
