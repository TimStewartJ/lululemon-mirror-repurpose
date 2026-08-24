package dev.mirror.repurpose;

final class MotionFrameAnalyzer {
    static final int MIN_SENSITIVITY = 1;
    static final int MAX_SENSITIVITY = 10;

    private static final int SAMPLE_COLUMNS = 32;
    private static final int SAMPLE_ROWS = 24;
    private static final int LUMA_DELTA_THRESHOLD = 18;

    private int frameWidth;
    private int frameHeight;
    private int sampleCount;
    private int[] previousSamples;
    private int[] currentSamples;
    private boolean calibrated;

    Result analyze(byte[] frame, int width, int height, int sensitivity) {
        if (frame == null || width <= 0 || height <= 0 || frame.length < width * height) {
            throw new IllegalArgumentException("Frame does not contain a complete luminance plane");
        }
        if (sensitivity < MIN_SENSITIVITY || sensitivity > MAX_SENSITIVITY) {
            throw new IllegalArgumentException("Sensitivity must be between 1 and 10");
        }
        ensureBuffers(width, height);
        sampleLuminance(frame, width, height);
        if (!calibrated) {
            System.arraycopy(currentSamples, 0, previousSamples, 0, sampleCount);
            calibrated = true;
            return new Result(false, 0d, requiredChangedPercent(sensitivity), false);
        }

        long totalDelta = 0;
        for (int index = 0; index < sampleCount; index++) {
            totalDelta += currentSamples[index] - previousSamples[index];
        }
        double globalDelta = totalDelta / (double) sampleCount;
        int changedSamples = 0;
        for (int index = 0; index < sampleCount; index++) {
            double localDelta = currentSamples[index] - previousSamples[index] - globalDelta;
            if (Math.abs(localDelta) >= LUMA_DELTA_THRESHOLD) {
                changedSamples++;
            }
        }
        System.arraycopy(currentSamples, 0, previousSamples, 0, sampleCount);

        double score = changedSamples * 100d / sampleCount;
        double threshold = requiredChangedPercent(sensitivity);
        return new Result(score >= threshold, score, threshold, true);
    }

    void reset() {
        calibrated = false;
    }

    private void ensureBuffers(int width, int height) {
        if (previousSamples != null && frameWidth == width && frameHeight == height) {
            return;
        }
        frameWidth = width;
        frameHeight = height;
        int columns = Math.min(SAMPLE_COLUMNS, width);
        int rows = Math.min(SAMPLE_ROWS, height);
        sampleCount = columns * rows;
        previousSamples = new int[sampleCount];
        currentSamples = new int[sampleCount];
        calibrated = false;
    }

    private void sampleLuminance(byte[] frame, int width, int height) {
        int columns = Math.min(SAMPLE_COLUMNS, width);
        int rows = Math.min(SAMPLE_ROWS, height);
        int sample = 0;
        for (int row = 0; row < rows; row++) {
            int y = Math.min(height - 1, ((2 * row + 1) * height) / (2 * rows));
            int rowOffset = y * width;
            for (int column = 0; column < columns; column++) {
                int x = Math.min(width - 1, ((2 * column + 1) * width) / (2 * columns));
                currentSamples[sample++] = frame[rowOffset + x] & 0xff;
            }
        }
    }

    private static double requiredChangedPercent(int sensitivity) {
        return 18d - sensitivity * 1.5d;
    }

    static final class Result {
        final boolean motion;
        final double score;
        final double threshold;
        final boolean calibrated;

        Result(boolean motion, double score, double threshold, boolean calibrated) {
            this.motion = motion;
            this.score = score;
            this.threshold = threshold;
            this.calibrated = calibrated;
        }
    }
}
