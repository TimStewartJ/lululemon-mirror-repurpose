package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;

import org.junit.Test;

public final class MotionFrameAnalyzerTest {
    private static final int WIDTH = 64;
    private static final int HEIGHT = 48;

    @Test
    public void firstFrameCalibratesWithoutReportingMotion() {
        MotionFrameAnalyzer.Result result =
                new MotionFrameAnalyzer().analyze(frame(80), WIDTH, HEIGHT, 6);

        assertFalse(result.calibrated);
        assertFalse(result.motion);
    }

    @Test
    public void unchangedFrameDoesNotReportMotion() {
        MotionFrameAnalyzer analyzer = new MotionFrameAnalyzer();
        analyzer.analyze(frame(80), WIDTH, HEIGHT, 6);

        MotionFrameAnalyzer.Result result = analyzer.analyze(frame(80), WIDTH, HEIGHT, 6);

        assertTrue(result.calibrated);
        assertFalse(result.motion);
    }

    @Test
    public void globalExposureChangeIsIgnored() {
        MotionFrameAnalyzer analyzer = new MotionFrameAnalyzer();
        analyzer.analyze(frame(70), WIDTH, HEIGHT, 6);

        MotionFrameAnalyzer.Result result = analyzer.analyze(frame(105), WIDTH, HEIGHT, 6);

        assertFalse(result.motion);
    }

    @Test
    public void localizedLuminanceChangeReportsMotion() {
        MotionFrameAnalyzer analyzer = new MotionFrameAnalyzer();
        analyzer.analyze(frame(70), WIDTH, HEIGHT, 6);
        byte[] moved = frame(70);
        for (int y = 12; y < 36; y++) {
            Arrays.fill(moved, y * WIDTH + 16, y * WIDTH + 48, (byte) 150);
        }

        MotionFrameAnalyzer.Result result = analyzer.analyze(moved, WIDTH, HEIGHT, 6);

        assertTrue(result.motion);
    }

    @Test(expected = IllegalArgumentException.class)
    public void incompleteLuminancePlaneIsRejected() {
        new MotionFrameAnalyzer().analyze(new byte[20], WIDTH, HEIGHT, 6);
    }

    private static byte[] frame(int luminance) {
        byte[] frame = new byte[WIDTH * HEIGHT * 3 / 2];
        Arrays.fill(frame, 0, WIDTH * HEIGHT, (byte) luminance);
        return frame;
    }
}
