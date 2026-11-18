package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.io.ByteArrayOutputStream;
import java.nio.charset.Charset;
import java.util.LinkedHashMap;
import java.util.Map;

public final class DeviceInventoryTest {
    private static final Charset UTF8 = Charset.forName("UTF-8");

    @Test
    public void headerChecksumMatchesItsBytes() {
        byte[] header = TarWriter.block("system/build.prop".getBytes(UTF8), TarWriter.REGULAR,
                0644, 0, 0, 1234, 1561498809, new byte[0], "ustar\0", "00");
        assertEquals(TarWriter.BLOCK, header.length);
        long sum = 0;
        for (int i = 0; i < header.length; i++) {
            sum += (i >= 148 && i < 156) ? ' ' : header[i] & 0xff;
        }
        assertEquals(sum, Long.parseLong(new String(header, 148, 6, UTF8), 8));
        assertEquals("00000002322", new String(header, 124, 11, UTF8));
    }

    @Test
    public void longNamesGetALongLinkEntryAndContentIsPadded() throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        TarWriter tar = new TarWriter(out);
        StringBuilder name = new StringBuilder("system/");
        while (name.length() < 150) {
            name.append("deep/");
        }
        name.append("file.so");
        tar.header(name.toString(), TarWriter.REGULAR, 0644, 0, 0, 3, 0, null);
        tar.content("abc".getBytes(UTF8), 3);
        tar.endContent(3);
        tar.finish();
        byte[] bytes = out.toByteArray();
        assertEquals(0, bytes.length % TarWriter.BLOCK);
        assertEquals('L', (char) bytes[156]);
        assertTrue(new String(bytes, 512, name.length(), UTF8).equals(name.toString()));
        assertEquals(bytes.length, tar.bytesWritten());
    }

    @Test
    public void propertiesLeaveOutSerialsAndKeepOnlyReadOnlyOnes() throws Exception {
        JSONObject properties = DeviceInventory.properties(
                "[ro.board.platform]: [msm8916]\n[ro.serialno]: [bebf077]\n"
                        + "[ro.boot.serialno]: [bebf077]\n[persist.sys.timezone]: [UTC]\n"
                        + "[ro.boot.mac]: [00:11:22:33:44:55]\n[ro.boot.wifimacaddr]: [00:11:22:33:44:56]\n"
                        + "[ro.build.fingerprint]: [mirror/x]\n[ro.vendor.custom.id]: [owner-thing]\n");
        assertEquals("msm8916", properties.getString("ro.board.platform"));
        assertEquals("mirror/x", properties.getString("ro.build.fingerprint"));
        assertFalse(properties.toString().contains("bebf077"));
        assertFalse(properties.toString().contains("00:11:22"));
        assertFalse(properties.has("ro.vendor.custom.id"));
        assertFalse(properties.has("persist.sys.timezone"));
    }

    @Test
    public void partitionsJoinNamesToSizes() throws Exception {
        Map<String, String> byName = new LinkedHashMap<>();
        byName.put("system", "mmcblk0p21");
        byName.put("userdata", "mmcblk0p29");
        byName.put("ghost", "mmcblk0p99");
        JSONArray partitions = DeviceInventory.partitions(
                "major minor  #blocks  name\n\n 179 21 1572864 mmcblk0p21\n 179 29 5357551 mmcblk0p29\n",
                byName);
        assertEquals(2, partitions.length());
        assertEquals("system", partitions.getJSONObject(0).getString("name"));
        assertEquals(1572864L * 1024, partitions.getJSONObject(0).getLong("sizeBytes"));
    }

    @Test
    public void onlyTheSystemAndFirmwareMayBeFetched() {
        assertTrue(DeviceInventory.isFileRoot("/system"));
        assertTrue(DeviceInventory.isFileRoot("/firmware"));
        assertFalse(DeviceInventory.isFileRoot("/data"));
        assertFalse(DeviceInventory.isFileRoot("/system/../data"));
        assertFalse(DeviceInventory.isFileRoot(""));
    }
}
