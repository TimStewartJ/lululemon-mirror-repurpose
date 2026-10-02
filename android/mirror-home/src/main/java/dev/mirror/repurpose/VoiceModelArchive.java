package dev.mirror.repurpose;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Unpacks a speech model that arrives as a zip, as the Vosk project
 * publishes them. The model is too large for an update (the OTA supervisor
 * takes at most 32 MB), so it is uploaded once and kept apart from the app.
 * Whatever is uploaded is only ever written below the folder it is given.
 */
final class VoiceModelArchive {
    static final long MAX_ARCHIVE_BYTES = 96L * 1024 * 1024;
    static final long MAX_UNPACKED_BYTES = 256L * 1024 * 1024;
    static final int MAX_FILES = 64;

    static final class Limits {
        final long unpackedBytes;
        final int files;

        Limits(long unpackedBytes, int files) {
            this.unpackedBytes = unpackedBytes;
            this.files = files;
        }
    }

    static final class Contents {
        /** The folder the archive kept the model in, or "model" if it had none. */
        final String name;
        final long bytes;
        final int files;

        Contents(String name, long bytes, int files) {
            this.name = name;
            this.bytes = bytes;
            this.files = files;
        }
    }

    private VoiceModelArchive() {
    }

    static Contents unpack(File archive, File target) throws IOException {
        return unpack(archive, target, new Limits(MAX_UNPACKED_BYTES, MAX_FILES));
    }

    /**
     * Unpacks into {@code target}, which must not exist yet.
     *
     * @throws IOException with what is wrong, if this is not a speech model
     */
    static Contents unpack(File archive, File target, Limits limits) throws IOException {
        if (target.exists()) {
            throw new IOException("The folder to unpack into already exists");
        }
        try (ZipFile zip = openZip(archive)) {
            List<ZipEntry> files = new ArrayList<>();
            Enumeration<? extends ZipEntry> entries = zip.entries();
            while (entries.hasMoreElements()) {
                ZipEntry entry = entries.nextElement();
                if (entry.isDirectory()) {
                    continue;
                }
                files.add(entry);
                if (files.size() > limits.files) {
                    throw new IOException("The archive holds more than " + limits.files + " files");
                }
            }
            if (files.isEmpty()) {
                throw new IOException("The archive is empty");
            }
            String folder = commonFolder(files);
            if (!target.mkdirs()) {
                throw new IOException("Unable to create " + target.getName());
            }
            String root = target.getCanonicalPath() + File.separator;
            long written = 0;
            byte[] buffer = new byte[64 * 1024];
            for (ZipEntry entry : files) {
                String relative = safeName(entry.getName().substring(folder.length()));
                File output = new File(target, relative);
                if (!output.getCanonicalPath().startsWith(root)) {
                    throw new IOException("The archive names a file outside itself");
                }
                File parent = output.getParentFile();
                if (!parent.isDirectory() && !parent.mkdirs()) {
                    throw new IOException("Unable to create a folder of the model");
                }
                try (InputStream input = zip.getInputStream(entry);
                     OutputStream stream = new FileOutputStream(output)) {
                    int count;
                    while ((count = input.read(buffer)) > 0) {
                        // Counted as it is written; an archive's own sizes can lie.
                        written += count;
                        if (written > limits.unpackedBytes) {
                            throw new IOException("The model unpacks to more than "
                                    + limits.unpackedBytes / (1024 * 1024) + " MB");
                        }
                        stream.write(buffer, 0, count);
                    }
                }
            }
            requireModel(target);
            String name = folder.isEmpty() ? "model" : folder.substring(0, folder.length() - 1);
            return new Contents(name, written, files.size());
        }
    }

    /** What a Vosk model must hold for the recogniser to load it. */
    static void requireModel(File directory) throws IOException {
        for (String required : new String[]{"am/final.mdl", "conf/mfcc.conf", "conf/model.conf"}) {
            if (new File(directory, required).length() == 0) {
                throw new IOException("This is not a speech model: " + required + " is missing");
            }
        }
        boolean whole = new File(directory, "graph/HCLG.fst").length() > 0;
        boolean inParts = new File(directory, "graph/HCLr.fst").length() > 0
                && new File(directory, "graph/Gr.fst").length() > 0;
        if (!whole && !inParts) {
            throw new IOException("This is not a speech model: its graph is missing");
        }
        if (!inParts) {
            // Only a model with a graph in two parts can be given a command list.
            throw new IOException("This speech model cannot take a command list; use a small model");
        }
    }

    private static ZipFile openZip(File archive) throws IOException {
        try {
            return new ZipFile(archive);
        } catch (IOException error) {
            throw new IOException("The upload is not a zip archive");
        }
    }

    /** The one folder that every file lies in, with its slash; empty if there is none. */
    private static String commonFolder(List<ZipEntry> files) {
        String first = files.get(0).getName();
        int slash = first.indexOf('/');
        if (slash <= 0) {
            return "";
        }
        String folder = first.substring(0, slash + 1);
        for (ZipEntry entry : files) {
            if (!entry.getName().startsWith(folder)) {
                return "";
            }
        }
        return folder;
    }

    private static String safeName(String name) throws IOException {
        if (name.isEmpty() || name.startsWith("/") || name.indexOf('\\') >= 0 || name.indexOf(':') >= 0) {
            throw new IOException("The archive names a file outside itself");
        }
        for (String part : name.split("/")) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                throw new IOException("The archive names a file outside itself");
            }
        }
        return name;
    }

    /** Deletes a folder with everything in it; true if nothing is left. */
    static boolean delete(File file) {
        File[] children = file.listFiles();
        if (children != null) {
            for (File child : children) {
                delete(child);
            }
        }
        return !file.exists() || file.delete();
    }

    static List<String> list(File directory) {
        List<String> names = new ArrayList<>();
        collect(directory, "", names);
        Collections.sort(names);
        return names;
    }

    private static void collect(File directory, String prefix, List<String> names) {
        File[] children = directory.listFiles();
        if (children == null) {
            return;
        }
        for (File child : children) {
            if (child.isDirectory()) {
                collect(child, prefix + child.getName() + "/", names);
            } else {
                names.add(prefix + child.getName());
            }
        }
    }
}
