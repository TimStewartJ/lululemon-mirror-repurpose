package dev.mirror.repurpose.updater;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;

import java.io.File;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Comparator;
import java.util.Locale;

final class ApkInspector {
    private final Context context;
    private final PackageManager packageManager;
    private final Signature[] trustedSignatures;

    ApkInspector(Context context) throws InspectionException {
        this.context = context.getApplicationContext();
        packageManager = this.context.getPackageManager();
        trustedSignatures = packageInfo(this.context.getPackageName()).signatures;
        if (trustedSignatures == null || trustedSignatures.length == 0) {
            throw new InspectionException("OTA supervisor signing certificate is unavailable");
        }
    }

    Metadata inspectArchive(File apk) throws InspectionException {
        if (apk == null || !apk.isFile()) {
            throw new InspectionException("APK file is missing");
        }
        PackageInfo info = packageManager.getPackageArchiveInfo(
                apk.getAbsolutePath(),
                PackageManager.GET_SIGNATURES);
        if (info == null) {
            throw new InspectionException("Unable to parse APK");
        }
        return metadata(info, apk);
    }

    Metadata installedHome() throws InspectionException {
        PackageInfo info = packageInfo(OtaConstants.HOME_PACKAGE);
        File source = new File(info.applicationInfo.sourceDir);
        if (!source.isFile()) {
            throw new InspectionException("Installed Mirror Home APK is unavailable");
        }
        return metadata(info, source);
    }

    void requireTrustedHome(Metadata metadata) throws InspectionException {
        if (!OtaConstants.HOME_PACKAGE.equals(metadata.packageName)) {
            throw new InspectionException("APK is not Mirror Home");
        }
        if (!sameSignatures(trustedSignatures, metadata.signatures)) {
            throw new InspectionException("APK signing certificate is not trusted");
        }
    }

    private PackageInfo packageInfo(String packageName) throws InspectionException {
        try {
            return packageManager.getPackageInfo(packageName, PackageManager.GET_SIGNATURES);
        } catch (PackageManager.NameNotFoundException error) {
            throw new InspectionException("Package is not installed: " + packageName);
        }
    }

    private static Metadata metadata(PackageInfo info, File source) throws InspectionException {
        if (info.signatures == null || info.signatures.length == 0) {
            throw new InspectionException("APK has no signing certificate");
        }
        return new Metadata(
                info.packageName,
                info.versionCode,
                info.versionName == null ? "" : info.versionName,
                info.signatures,
                source);
    }

    private static boolean sameSignatures(Signature[] first, Signature[] second) {
        if (first == null || second == null || first.length != second.length) {
            return false;
        }
        Signature[] firstCopy = first.clone();
        Signature[] secondCopy = second.clone();
        Comparator<Signature> comparator = new Comparator<Signature>() {
            @Override
            public int compare(Signature left, Signature right) {
                return left.toCharsString().compareTo(right.toCharsString());
            }
        };
        Arrays.sort(firstCopy, comparator);
        Arrays.sort(secondCopy, comparator);
        return Arrays.equals(firstCopy, secondCopy);
    }

    static String sha256(File file) throws InspectionException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (java.io.InputStream input = new java.io.FileInputStream(file)) {
                byte[] buffer = new byte[64 * 1024];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    digest.update(buffer, 0, count);
                }
            } catch (java.io.IOException error) {
                throw new InspectionException("Unable to read APK");
            }
            StringBuilder result = new StringBuilder();
            for (byte item : digest.digest()) {
                result.append(String.format(Locale.US, "%02x", item & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    static final class Metadata {
        final String packageName;
        final int versionCode;
        final String versionName;
        final Signature[] signatures;
        final File source;

        Metadata(
                String packageName,
                int versionCode,
                String versionName,
                Signature[] signatures,
                File source) {
            this.packageName = packageName;
            this.versionCode = versionCode;
            this.versionName = versionName;
            this.signatures = signatures;
            this.source = source;
        }
    }

    static final class InspectionException extends Exception {
        InspectionException(String message) {
            super(message);
        }
    }
}
