package dev.mirror.repurpose;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What this unit is, read from inside it, for a unit whose ADB is not at hand:
 * the same facts {@code tools/identify.py} reads over ADB, and the files of the
 * system partition that a rebuilt Android for this board is assembled from.
 * Everything here only reads; nothing personal is included (no serial number,
 * no user data: the roots are the read-only system and firmware partitions and
 * the boot image's ramdisk).
 */
final class DeviceInventory {
    /**
     * Folders whose files may be fetched; anything else is refused. {@code /} is
     * the boot image's ramdisk, the same on every unit of a firmware: only what
     * lies on the root file system itself, never another file system mounted in it.
     */
    static final List<String> FILE_ROOTS = Arrays.asList("/system", "/firmware", "/");
    /** Keys of computers allowed to use ADB: left out even where readable. */
    private static final List<String> NEVER_SENT = Arrays.asList("/adb_keys");
    private static final String[] PACKAGE_PREFIXES = {"com.mirror.", "co.mirror.", "dev.mirror."};
    private static final Pattern PROPERTY = Pattern.compile("^\\[([^\\]]+)\\]: \\[(.*)\\]\\s*$");
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final AtomicBoolean SENDING = new AtomicBoolean();

    private final Context context;

    DeviceInventory(Context context) {
        this.context = context.getApplicationContext();
    }

    JSONObject snapshot() throws JSONException {
        JSONObject result = new JSONObject()
                .put("schema", "mirror-inventory/1")
                .put("properties", properties(run("/system/bin/getprop")))
                .put("kernel", new JSONObject()
                        .put("release", read("/proc/sys/kernel/osrelease").trim())
                        .put("version", read("/proc/version").trim()))
                .put("selinux", selinux(read("/sys/fs/selinux/enforce")))
                .put("partitions", partitions(read("/proc/partitions"), byName()))
                .put("mounts", mounts(read("/proc/mounts")))
                .put("thermal", thermal(deviceTrees(), new File(MSM_THERMAL_PARAMETERS),
                        new File(THERMAL_ZONES), new File(CPUS_POSSIBLE)))
                .put("packages", packages());
        JSONArray roots = new JSONArray();
        for (String root : FILE_ROOTS) {
            File folder = new File(root);
            if (folder.isDirectory() && folder.canRead()) {
                roots.put(root);
            }
        }
        return result.put("fileRoots", roots);
    }

    static boolean isFileRoot(String root) {
        return FILE_ROOTS.contains(root);
    }

    /** Claims the right to send files; false while another transfer is under way. */
    static boolean beginSending() {
        return SENDING.compareAndSet(false, true);
    }

    static void endSending() {
        SENDING.set(false);
    }

    /**
     * Writes every readable file, folder and link under {@code root} as a tar
     * stream, then a manifest {@code MIRROR-FILES.json} of what was left out.
     */
    void writeFiles(String root, OutputStream out) throws IOException, JSONException {
        TarWriter tar = new TarWriter(out);
        Manifest manifest = new Manifest(root);
        long device = -1;
        if ("/".equals(root)) {
            try {
                device = Os.lstat(root).st_dev;
            } catch (ErrnoException error) {
                throw new IOException(error.getMessage(), error);
            }
        }
        walk(new File(root), tar, manifest, new byte[64 * 1024], device);
        byte[] json = manifest.toJson().toString(1).getBytes(UTF8);
        tar.header("MIRROR-FILES.json", TarWriter.REGULAR, 0644, 0, 0, json.length,
                System.currentTimeMillis() / 1000L, null);
        tar.content(json, json.length);
        tar.endContent(json.length);
        tar.finish();
    }

    /** {@code device}: when not -1, entries on any other file system are left out. */
    private void walk(File file, TarWriter tar, Manifest manifest, byte[] buffer, long device)
            throws IOException {
        String path = file.getPath();
        String name = path.startsWith("/") ? path.substring(1) : path;
        if (NEVER_SENT.contains(path)) {
            manifest.skip(path, "never sent");
            return;
        }
        StructStat stat;
        try {
            stat = Os.lstat(path);
        } catch (ErrnoException error) {
            manifest.skip(path, error.getMessage());
            return;
        }
        if (device != -1 && stat.st_dev != device) {
            manifest.skip(path, "another file system");
            return;
        }
        if (OsConstants.S_ISLNK(stat.st_mode)) {
            String target;
            try {
                target = Os.readlink(path);
            } catch (ErrnoException error) {
                manifest.skip(path, error.getMessage());
                return;
            }
            tar.header(name, TarWriter.SYMLINK, stat.st_mode, stat.st_uid, stat.st_gid, 0,
                    stat.st_mtime, target);
            manifest.links++;
            return;
        }
        if (OsConstants.S_ISDIR(stat.st_mode)) {
            String[] children = file.list();
            if (children == null) {
                manifest.skip(path, "folder cannot be listed");
                return;
            }
            if (!name.isEmpty()) {
                tar.header(name + "/", TarWriter.DIRECTORY, stat.st_mode, stat.st_uid, stat.st_gid, 0,
                        stat.st_mtime, null);
                manifest.folders++;
            }
            Arrays.sort(children);
            for (String child : children) {
                walk(new File(file, child), tar, manifest, buffer, device);
            }
            return;
        }
        if (!OsConstants.S_ISREG(stat.st_mode)) {
            manifest.skip(path, "not a regular file");
            return;
        }
        if (stat.st_size >= TarWriter.MAX_SIZE) {
            manifest.skip(path, "larger than a tar entry can hold");
            return;
        }
        InputStream input;
        try {
            input = new FileInputStream(file);
        } catch (IOException error) {
            manifest.skip(path, error.getMessage());
            return;
        }
        try {
            tar.header(name, TarWriter.REGULAR, stat.st_mode, stat.st_uid, stat.st_gid,
                    stat.st_size, stat.st_mtime, null);
            long remaining = stat.st_size;
            boolean shortRead = false;
            while (remaining > 0) {
                int count;
                try {
                    count = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
                } catch (IOException error) {
                    count = -1;
                }
                if (count < 0) {
                    // The header promised this size: fill it so the stream stays readable.
                    Arrays.fill(buffer, (byte) 0);
                    count = (int) Math.min(buffer.length, remaining);
                    shortRead = true;
                }
                tar.content(buffer, count);
                remaining -= count;
            }
            tar.endContent(stat.st_size);
            if (shortRead) {
                manifest.skip(path, "read ended early; padded with zeros");
            } else {
                manifest.files++;
                manifest.bytes += stat.st_size;
            }
        } finally {
            input.close();
        }
    }

    /** Property families that describe the build and board; everything else is left out. */
    private static final String[] PROPERTY_PREFIXES = {
            "ro.build.", "ro.product.", "ro.board.", "ro.bootimage.build.", "ro.hardware",
            "ro.boot.hardware", "ro.boot.baseband", "ro.boot.bootdevice", "ro.boot.emmc",
            "ro.boot.selinux", "ro.boot.authorized_kernel", "ro.baseband", "ro.revision",
            // Which of init's services run: what a rebuilt system must start.
            "init.svc.",
    };
    /** Single properties that say how the boot image guards the system it starts. */
    private static final List<String> BOOT_GUARD_PROPERTIES = Arrays.asList(
            "ro.secure", "ro.debuggable", "ro.adb.secure", "ro.crypto.state", "ro.crypto.type",
            "ro.vold.forceencryption", "ro.boot.veritymode", "ro.boot.verifiedbootstate",
            "ro.boot.flash.locked", "ro.oem_unlock_supported", "ro.zygote",
            "persist.sys.usb.config", "sys.usb.config");
    /** Set by Android 6's fs_mgr for a partition that dm-verity checks. */
    private static final Pattern VERIFIED_PARTITION = Pattern.compile("^partition\\.[a-z0-9_]+\\.verified$");
    /** Never sent even within those families: what could tell one unit or owner from another. */
    private static final Pattern IDENTIFYING = Pattern.compile(
            "serial|mac|addr|imei|meid|ssid|bdaddr|uuid|cid|android_id", Pattern.CASE_INSENSITIVE);

    static JSONObject properties(String getprop) throws JSONException {
        JSONObject result = new JSONObject();
        for (String line : getprop.split("\n")) {
            Matcher match = PROPERTY.matcher(line);
            if (match.matches()) {
                String name = match.group(1);
                if (describesTheBuild(name)) {
                    result.put(name, match.group(2));
                }
            }
        }
        return result;
    }

    static boolean describesTheBuild(String name) {
        if (IDENTIFYING.matcher(name).find()) {
            return false;
        }
        if (BOOT_GUARD_PROPERTIES.contains(name) || VERIFIED_PARTITION.matcher(name).matches()) {
            return true;
        }
        for (String prefix : PROPERTY_PREFIXES) {
            if (name.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    static JSONArray partitions(String procPartitions, Map<String, String> byName) throws JSONException {
        Map<String, Long> sizes = new LinkedHashMap<>();
        for (String line : procPartitions.split("\n")) {
            String[] fields = line.trim().split("\\s+");
            if (fields.length == 4 && fields[2].matches("\\d+")) {
                sizes.put(fields[3], Long.parseLong(fields[2]) * 1024L);
            }
        }
        JSONArray result = new JSONArray();
        List<String> names = new ArrayList<>(byName.keySet());
        java.util.Collections.sort(names);
        for (String name : names) {
            Long size = sizes.get(byName.get(name));
            if (size != null) {
                result.put(new JSONObject().put("name", name).put("sizeBytes", size));
            }
        }
        return result;
    }

    static JSONArray mounts(String procMounts) throws JSONException {
        JSONArray result = new JSONArray();
        for (String line : procMounts.split("\n")) {
            String[] fields = line.trim().split("\\s+");
            if (fields.length >= 4 && fields[0].startsWith("/dev/block/")) {
                result.put(new JSONObject()
                        .put("device", fields[0])
                        .put("mountPoint", fields[1])
                        .put("type", fields[2])
                        .put("options", fields[3]));
            }
        }
        return result;
    }

    static Object selinux(String enforce) {
        String value = enforce.trim();
        return "1".equals(value) ? "enforcing" : "0".equals(value) ? "permissive" : JSONObject.NULL;
    }

    /** Where the kernel shows its device tree, in the order tried. */
    private static final String[] DEVICE_TREES = {"/proc/device-tree", "/sys/firmware/devicetree/base"};
    private static final String MSM_THERMAL_PARAMETERS = "/sys/module/msm_thermal/parameters";
    private static final String THERMAL_ZONES = "/sys/class/thermal";
    private static final String CPUS_POSSIBLE = "/sys/devices/system/cpu/possible";
    /** Device-tree nodes of the kernel's own thermal driver, looked for at the top and under soc. */
    private static final String MSM_THERMAL_NODE = "qcom,msm-thermal";
    /** Where the driver's node usually is, tried by name when the tree cannot be listed. */
    private static final String[] KNOWN_NODES = {"soc/qcom,msm-thermal", "qcom,msm-thermal"};
    /** Properties read by name when a node cannot be listed: what says which driver it is for and what it sets. */
    private static final String[] KNOWN_PROPERTIES = {
            "compatible", "status", "qcom,sensor-id", "qcom,poll-ms", "qcom,limit-temp",
            "qcom,temp-hysteresis", "qcom,freq-step", "qcom,freq-control-mask", "qcom,therm-reset-temp",
            "qcom,core-limit-temp", "qcom,core-temp-hysteresis", "qcom,core-control-mask",
            "qcom,hotplug-temp", "qcom,hotplug-temp-hysteresis",
    };
    private static final int MAX_PROPERTY_BYTES = 1024;
    private static final int MAX_NODE_DEPTH = 3;
    private static final int MAX_TRIP_POINTS = 32;

    private static File[] deviceTrees() {
        File[] trees = new File[DEVICE_TREES.length];
        for (int i = 0; i < trees.length; i++) {
            trees[i] = new File(DEVICE_TREES[i]);
        }
        return trees;
    }

    /**
     * How the kernel guards the chip against heat without a thermal daemon: the
     * {@code msm_thermal} driver's parameters, its device-tree node (each property
     * as hex bytes; numbers are big-endian 32-bit cells), every thermal zone with
     * its temperature and trip points, and which CPUs exist (the driver throttles only
     * those its mask names). {@code deviceTreeListed} says whether the tree could be
     * listed; when it could not, the usual node is read by name and other nodes cannot
     * be ruled out. Anything that cannot be read is listed in {@code unreadable} with
     * the reason, so a missing value is never mistaken for an absent one. Names that
     * could tell one unit from another are left out.
     */
    static JSONObject thermal(File[] deviceTrees, File msmThermalParameters, File thermalZones,
            File cpusPossible) throws JSONException {
        JSONArray unreadable = new JSONArray();
        JSONObject nodes = new JSONObject();
        File listedTree = null;
        for (File tree : deviceTrees) {
            String[] names = tree.list();
            if (names == null) {
                unreadable.put(unreadable(tree, "folder cannot be listed"));
                continue;
            }
            listedTree = tree;
            findNodes(tree, "", names, nodes, unreadable);
            File soc = new File(tree, "soc");
            String[] socNames = soc.list();
            if (socNames != null) {
                findNodes(soc, "soc/", socNames, nodes, unreadable);
            } else if (Arrays.asList(names).contains("soc")) {
                unreadable.put(unreadable(soc, "folder cannot be listed"));
            }
            break;
        }
        if (listedTree == null) {
            for (File tree : deviceTrees) {
                for (String known : KNOWN_NODES) {
                    File node = new File(tree, known);
                    if (node.isDirectory() && !nodes.has(known)) {
                        nodes.put(known, node.list() != null
                                ? deviceTreeNode(node, 1, unreadable) : knownProperties(node, unreadable));
                    }
                }
                if (nodes.length() > 0) {
                    break;
                }
            }
        }
        String[] names = msmThermalParameters.list();
        String cpus = readText(cpusPossible, unreadable);
        JSONObject result = new JSONObject().put("schema", "mirror-thermal/1")
                .put("cpusPossible", cpus == null ? JSONObject.NULL : cpus)
                .put("deviceTreeListed", listedTree != null);
        if (names == null) {
            result.put("msmThermalParameters", JSONObject.NULL);
            unreadable.put(unreadable(msmThermalParameters, "folder cannot be listed"));
        } else {
            JSONObject parameters = new JSONObject();
            Arrays.sort(names);
            for (String name : names) {
                if (IDENTIFYING.matcher(name).find()) {
                    continue;
                }
                String value = readText(new File(msmThermalParameters, name), unreadable);
                if (value != null) {
                    parameters.put(name, value);
                }
            }
            result.put("msmThermalParameters", parameters);
        }
        return result
                .put("deviceTreeNodes", nodes)
                .put("zones", thermalZones(thermalZones, unreadable))
                .put("unreadable", unreadable);
    }

    private static void findNodes(File folder, String prefix, String[] names, JSONObject nodes,
            JSONArray unreadable) throws JSONException {
        Arrays.sort(names);
        for (String name : names) {
            File node = new File(folder, name);
            if (name.startsWith(MSM_THERMAL_NODE) && node.isDirectory()) {
                nodes.put(prefix + name, deviceTreeNode(node, 1, unreadable));
            }
        }
    }

    /** A node that cannot be listed: its known properties, each read by name. */
    private static JSONObject knownProperties(File node, JSONArray unreadable) throws JSONException {
        unreadable.put(unreadable(node, "folder cannot be listed; known properties read by name"));
        JSONObject properties = new JSONObject();
        for (String name : KNOWN_PROPERTIES) {
            File entry = new File(node, name);
            if (!entry.exists()) {
                continue;
            }
            byte[] value = readBytes(entry, unreadable);
            if (value != null) {
                properties.put(name, hex(value));
            }
        }
        return new JSONObject().put("properties", properties).put("children", new JSONObject())
                .put("listed", false);
    }

    private static JSONObject deviceTreeNode(File node, int depth, JSONArray unreadable)
            throws JSONException {
        JSONObject properties = new JSONObject();
        JSONObject children = new JSONObject();
        String[] names = node.list();
        boolean listed = names != null;
        if (!listed) {
            unreadable.put(unreadable(node, "folder cannot be listed"));
            names = new String[0];
        }
        Arrays.sort(names);
        for (String name : names) {
            File entry = new File(node, name);
            if (IDENTIFYING.matcher(name).find() && !"#address-cells".equals(name)) {
                continue;
            }
            if (entry.isDirectory()) {
                if (depth < MAX_NODE_DEPTH) {
                    children.put(name, deviceTreeNode(entry, depth + 1, unreadable));
                }
                continue;
            }
            byte[] value = readBytes(entry, unreadable);
            if (value != null) {
                properties.put(name, hex(value));
            }
        }
        return new JSONObject().put("properties", properties).put("children", children)
                .put("listed", listed);
    }

    private static JSONArray thermalZones(File folder, JSONArray unreadable) throws JSONException {
        JSONArray zones = new JSONArray();
        String[] names = folder.list();
        if (names == null) {
            unreadable.put(unreadable(folder, "folder cannot be listed"));
            return zones;
        }
        Arrays.sort(names, new java.util.Comparator<String>() {
            @Override
            public int compare(String a, String b) {
                return zoneNumber(a) - zoneNumber(b);
            }
        });
        for (String name : names) {
            if (zoneNumber(name) < 0) {
                continue;
            }
            File zone = new File(folder, name);
            JSONObject entry = new JSONObject().put("name", name);
            for (String field : new String[]{"type", "temp", "mode"}) {
                String value = readText(new File(zone, field), unreadable);
                entry.put(field, value == null ? JSONObject.NULL : value);
            }
            JSONArray trips = new JSONArray();
            for (int index = 0; index < MAX_TRIP_POINTS; index++) {
                File temp = new File(zone, "trip_point_" + index + "_temp");
                if (!temp.exists()) {
                    break;
                }
                String type = readText(new File(zone, "trip_point_" + index + "_type"), unreadable);
                String value = readText(temp, unreadable);
                trips.put(new JSONObject().put("index", index)
                        .put("type", type == null ? JSONObject.NULL : type)
                        .put("temp", value == null ? JSONObject.NULL : value));
            }
            zones.put(entry.put("trips", trips));
        }
        return zones;
    }

    /** The number of {@code thermal_zoneN}, or -1 for any other name. */
    static int zoneNumber(String name) {
        if (!name.startsWith("thermal_zone") || name.length() == "thermal_zone".length()) {
            return -1;
        }
        String number = name.substring("thermal_zone".length());
        return number.matches("\\d{1,6}") ? Integer.parseInt(number) : -1;
    }

    private static JSONObject unreadable(File file, String reason) throws JSONException {
        return new JSONObject().put("path", file.getPath()).put("reason", reason);
    }

    private static String readText(File file, JSONArray unreadable) throws JSONException {
        byte[] bytes = readBytes(file, unreadable);
        return bytes == null ? null : new String(bytes, UTF8).trim();
    }

    /** At most {@link #MAX_PROPERTY_BYTES}; null (and listed as unreadable) when it cannot be read or is longer. */
    private static byte[] readBytes(File file, JSONArray unreadable) throws JSONException {
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[MAX_PROPERTY_BYTES + 1];
            int length = 0;
            int count;
            while (length < buffer.length
                    && (count = input.read(buffer, length, buffer.length - length)) > 0) {
                length += count;
            }
            if (length > MAX_PROPERTY_BYTES) {
                unreadable.put(unreadable(file, "longer than " + MAX_PROPERTY_BYTES + " bytes"));
                return null;
            }
            return Arrays.copyOf(buffer, length);
        } catch (IOException error) {
            String reason = error.getMessage();
            unreadable.put(unreadable(file, reason == null ? error.getClass().getSimpleName() : reason));
            return null;
        }
    }

    static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            text.append(Character.forDigit((value >> 4) & 0xf, 16)).append(Character.forDigit(value & 0xf, 16));
        }
        return text.toString();
    }

    /** Partition name to block device name, from the by-name links. */
    private static Map<String, String> byName() {
        Map<String, String> result = new LinkedHashMap<>();
        List<File> folders = new ArrayList<>();
        folders.add(new File("/dev/block/bootdevice/by-name"));
        File[] platforms = new File("/dev/block/platform").listFiles();
        if (platforms != null) {
            for (File platform : platforms) {
                folders.add(new File(platform, "by-name"));
            }
        }
        for (File folder : folders) {
            File[] links = folder.listFiles();
            if (links == null) {
                continue;
            }
            for (File link : links) {
                try {
                    result.put(link.getName(), new File(Os.readlink(link.getPath())).getName());
                } catch (ErrnoException ignored) {
                    // Not a link, or not readable: leave it out.
                }
            }
            if (!result.isEmpty()) {
                break;
            }
        }
        return result;
    }

    private JSONArray packages() throws JSONException {
        List<String> names = new ArrayList<>();
        Map<String, String> versions = new LinkedHashMap<>();
        for (PackageInfo info : context.getPackageManager().getInstalledPackages(0)) {
            for (String prefix : PACKAGE_PREFIXES) {
                if (info.packageName.startsWith(prefix)) {
                    names.add(info.packageName);
                    versions.put(info.packageName, info.versionName);
                }
            }
        }
        java.util.Collections.sort(names);
        JSONArray result = new JSONArray();
        for (String name : names) {
            String version = versions.get(name);
            result.put(new JSONObject().put("name", name)
                    .put("versionName", version == null ? JSONObject.NULL : version));
        }
        return result;
    }

    private static String read(String path) {
        try (InputStream input = new FileInputStream(path)) {
            return slurp(input);
        } catch (IOException error) {
            return "";
        }
    }

    private static String run(String command) {
        try {
            Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
            try {
                return slurp(process.getInputStream());
            } finally {
                process.destroy();
            }
        } catch (IOException error) {
            return "";
        }
    }

    private static String slurp(InputStream input) throws IOException {
        StringBuilder text = new StringBuilder();
        BufferedReader reader = new BufferedReader(new InputStreamReader(input, UTF8));
        String line;
        while ((line = reader.readLine()) != null) {
            text.append(line).append('\n');
        }
        return text.toString();
    }

    private static final class Manifest {
        final String root;
        final JSONArray skipped = new JSONArray();
        int files;
        int folders;
        int links;
        long bytes;

        Manifest(String root) {
            this.root = root;
        }

        void skip(String path, String reason) {
            try {
                skipped.put(new JSONObject().put("path", path).put("reason", reason));
            } catch (JSONException ignored) {
                // Cannot happen with string values.
            }
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("schema", "mirror-files/1")
                    .put("root", root)
                    .put("files", files)
                    .put("folders", folders)
                    .put("links", links)
                    .put("bytes", bytes)
                    .put("skipped", skipped);
        }
    }
}
