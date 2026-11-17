package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public final class DeviceControlsTest {
    @Test
    public void factoryServiceComesFirstAndTheWindowAlwaysRemains() {
        assertArrayEquals(
                new String[]{DeviceControls.VENDOR, DeviceControls.SYSTEM, DeviceControls.WINDOW},
                DeviceControls.brightnessMethods(true, true));
        assertArrayEquals(
                new String[]{DeviceControls.VENDOR, DeviceControls.WINDOW},
                DeviceControls.brightnessMethods(true, false));
        assertArrayEquals(
                new String[]{DeviceControls.SYSTEM, DeviceControls.WINDOW},
                DeviceControls.brightnessMethods(false, true));
        assertArrayEquals(
                new String[]{DeviceControls.WINDOW},
                DeviceControls.brightnessMethods(false, false));
    }
}
