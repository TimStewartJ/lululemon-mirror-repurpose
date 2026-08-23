package co.mirror.datacap;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Binder;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;

final class TrustedCaller {
    private static final String HOME_PACKAGE = "dev.mirror.repurpose";

    private final Context context;

    TrustedCaller(Context context) {
        this.context = context.getApplicationContext();
    }

    boolean isTrusted() {
        String trustedCertificate = BuildConfig.TRUSTED_HOME_CERTIFICATE_SHA256;
        if (trustedCertificate.isEmpty()) {
            return false;
        }

        int callingUid = Binder.getCallingUid();
        String[] packages = context.getPackageManager().getPackagesForUid(callingUid);
        if (packages == null) {
            return false;
        }
        for (String packageName : packages) {
            if (HOME_PACKAGE.equals(packageName) && certificateMatches(packageName, trustedCertificate)) {
                return true;
            }
        }
        return false;
    }

    @SuppressWarnings("deprecation")
    private boolean certificateMatches(String packageName, String trustedCertificate) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(
                    packageName,
                    PackageManager.GET_SIGNATURES);
            if (info.signatures == null) {
                return false;
            }
            for (Signature signature : info.signatures) {
                if (trustedCertificate.equals(sha256(signature.toByteArray()))) {
                    return true;
                }
            }
        } catch (PackageManager.NameNotFoundException | NoSuchAlgorithmException ignored) {
            return false;
        }
        return false;
    }

    private static String sha256(byte[] value) throws NoSuchAlgorithmException {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(value);
        StringBuilder result = new StringBuilder(digest.length * 2);
        for (byte item : digest) {
            result.append(String.format(Locale.US, "%02x", item & 0xff));
        }
        return result.toString();
    }
}
