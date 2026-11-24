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
    public void onlyTheSystemFirmwareAndRamdiskMayBeFetched() {
        assertTrue(DeviceInventory.isFileRoot("/system"));
        assertTrue(DeviceInventory.isFileRoot("/firmware"));
        assertTrue(DeviceInventory.isFileRoot("/"));
        assertFalse(DeviceInventory.isFileRoot("/data"));
        assertFalse(DeviceInventory.isFileRoot("/system/../data"));
        assertFalse(DeviceInventory.isFileRoot("//"));
        assertFalse(DeviceInventory.isFileRoot(""));
    }

    @Test
    public void bootGuardPropertiesAreReported() throws Exception {
        JSONObject properties = DeviceInventory.properties(
                "[ro.secure]: [1]\n[ro.debuggable]: [0]\n[ro.adb.secure]: [1]\n"
                        + "[ro.crypto.state]: [unencrypted]\n[partition.system.verified]: [2]\n"
                        + "[ro.boot.veritymode]: [enforcing]\n[persist.sys.usb.config]: [mtp,adb]\n"
                        + "[ro.vold.forceencryption]: [0]\n[partition.data.guid]: [abc-123]\n"
                        + "[sys.usb.configfs]: [0]\n[ro.crypto.fs_crypto_blkdev]: [/dev/block/dm-1]\n"
                        + "[ro.boot.serialno]: [bebf077]\n");
        assertEquals("1", properties.getString("ro.secure"));
        assertEquals("0", properties.getString("ro.debuggable"));
        assertEquals("1", properties.getString("ro.adb.secure"));
        assertEquals("unencrypted", properties.getString("ro.crypto.state"));
        assertEquals("2", properties.getString("partition.system.verified"));
        assertEquals("enforcing", properties.getString("ro.boot.veritymode"));
        assertEquals("mtp,adb", properties.getString("persist.sys.usb.config"));
        assertEquals("0", properties.getString("ro.vold.forceencryption"));
        assertFalse(properties.has("partition.data.guid"));
        assertFalse(properties.has("sys.usb.configfs"));
        assertFalse(properties.has("ro.crypto.fs_crypto_blkdev"));
        assertFalse(properties.has("ro.boot.serialno"));
    }

    @Test
    public void serviceStatesAreReported() throws Exception {
        JSONObject properties = DeviceInventory.properties(
                "[init.svc.thermal-engine]: [running]\n[init.svc.qseecomd]: [stopped]\n"
                        + "[init.svc.wcnss-service]: [restarting]\n[init.svcs]: [x]\n");
        assertEquals("running", properties.getString("init.svc.thermal-engine"));
        assertEquals("stopped", properties.getString("init.svc.qseecomd"));
        assertEquals("restarting", properties.getString("init.svc.wcnss-service"));
        assertFalse(properties.has("init.svcs"));
    }

    @Test
    public void thermalReportsDriverNodeZonesAndWhatCouldNotBeRead() throws Exception {
        java.nio.file.Path root = java.nio.file.Files.createTempDirectory("thermal");
        java.io.File tree = root.resolve("device-tree").toFile();
        java.io.File node = new java.io.File(tree, "soc/qcom,msm-thermal");
        assertTrue(new java.io.File(node, "qcom,vdd-dig-rstr").mkdirs());
        write(new java.io.File(node, "qcom,limit-temp"), new byte[]{0, 0, 0, 60});
        write(new java.io.File(node, "qcom,freq-control-mask"), new byte[]{0, 0, 0, 0x0f});
        write(new java.io.File(node, "qcom,vdd-dig-rstr/qcom,levels"), new byte[]{0, 0, 0, 5, 0, 0, 0, 7});
        write(new java.io.File(node, "huge"), new byte[2000]);
        assertTrue(new java.io.File(tree, "soc/qcom,other").mkdirs());
        java.io.File parameters = root.resolve("parameters").toFile();
        assertTrue(parameters.mkdirs());
        write(new java.io.File(parameters, "enabled"), "Y\n".getBytes(UTF8));
        java.io.File zones = root.resolve("thermal").toFile();
        for (String zone : new String[]{"thermal_zone10", "thermal_zone2", "cooling_device0"}) {
            assertTrue(new java.io.File(zones, zone).mkdirs());
        }
        write(new java.io.File(zones, "thermal_zone2/type"), "tsens_tz_sensor2\n".getBytes(UTF8));
        write(new java.io.File(zones, "thermal_zone2/temp"), "41\n".getBytes(UTF8));
        write(new java.io.File(zones, "thermal_zone2/trip_point_0_temp"), "105\n".getBytes(UTF8));
        write(new java.io.File(zones, "thermal_zone2/trip_point_0_type"), "critical\n".getBytes(UTF8));

        write(new java.io.File(node, "local-mac-address"), new byte[]{1, 2, 3, 4, 5, 6});
        JSONObject thermal = DeviceInventory.thermal(
                new java.io.File[]{root.resolve("absent").toFile(), tree}, parameters, zones,
                write(root.resolve("possible").toFile(), "0-3\n".getBytes(UTF8)));

        assertEquals("mirror-thermal/1", thermal.getString("schema"));
        assertEquals("0-3", thermal.getString("cpusPossible"));
        assertEquals("Y", thermal.getJSONObject("msmThermalParameters").getString("enabled"));
        JSONObject found = thermal.getJSONObject("deviceTreeNodes");
        assertEquals(1, found.length());
        JSONObject driver = found.getJSONObject("soc/qcom,msm-thermal");
        assertEquals("0000003c", driver.getJSONObject("properties").getString("qcom,limit-temp"));
        assertEquals("0000000f", driver.getJSONObject("properties").getString("qcom,freq-control-mask"));
        assertFalse(driver.getJSONObject("properties").has("huge"));
        assertFalse(driver.getJSONObject("properties").has("local-mac-address"));
        assertTrue(thermal.getBoolean("deviceTreeListed"));
        assertTrue(driver.getBoolean("listed"));
        assertEquals("0000000500000007", driver.getJSONObject("children").getJSONObject("qcom,vdd-dig-rstr")
                .getJSONObject("properties").getString("qcom,levels"));
        JSONArray zoneList = thermal.getJSONArray("zones");
        assertEquals(2, zoneList.length());
        JSONObject first = zoneList.getJSONObject(0);
        assertEquals("thermal_zone2", first.getString("name"));
        assertEquals("41", first.getString("temp"));
        assertEquals("critical", first.getJSONArray("trips").getJSONObject(0).getString("type"));
        assertEquals("105", first.getJSONArray("trips").getJSONObject(0).getString("temp"));
        assertTrue(zoneList.getJSONObject(1).isNull("temp"));
        String unreadable = thermal.getJSONArray("unreadable").toString();
        assertTrue(unreadable, unreadable.contains("huge") && unreadable.contains("longer than 1024 bytes"));
        assertTrue(unreadable, unreadable.contains("thermal_zone10"));
    }

    @Test
    public void thermalWithoutTheDriverSaysSo() throws Exception {
        java.io.File missing = java.nio.file.Files.createTempDirectory("none").resolve("absent").toFile();
        JSONObject thermal = DeviceInventory.thermal(new java.io.File[]{missing}, missing, missing, missing);
        assertTrue(thermal.isNull("msmThermalParameters"));
        assertTrue(thermal.isNull("cpusPossible"));
        assertEquals(0, thermal.getJSONObject("deviceTreeNodes").length());
        assertEquals(0, thermal.getJSONArray("zones").length());
        assertEquals(4, thermal.getJSONArray("unreadable").length());
    }

    @Test
    public void aTreeThatCannotBeListedIsReadByTheUsualNodeName() throws Exception {
        java.nio.file.Path root = java.nio.file.Files.createTempDirectory("unlisted");
        java.io.File node = root.resolve("soc/qcom,msm-thermal").toFile();
        assertTrue(node.mkdirs());
        write(new java.io.File(node, "qcom,limit-temp"), new byte[]{0, 0, 0, 60});
        java.io.File refused = new java.io.File(root.toString()) {
            @Override
            public String[] list() {
                return null;
            }
        };
        java.io.File missing = root.resolve("absent").toFile();
        JSONObject thermal = DeviceInventory.thermal(new java.io.File[]{refused}, missing, missing, missing);
        assertFalse(thermal.getBoolean("deviceTreeListed"));
        assertEquals("0000003c", thermal.getJSONObject("deviceTreeNodes")
                .getJSONObject("soc/qcom,msm-thermal").getJSONObject("properties").getString("qcom,limit-temp"));
        assertTrue(thermal.getJSONArray("unreadable").toString().contains("folder cannot be listed"));
    }

    private static java.io.File write(java.io.File file, byte[] bytes) throws java.io.IOException {
        java.nio.file.Files.write(file.toPath(), bytes);
        return file;
    }
}
