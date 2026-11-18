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
