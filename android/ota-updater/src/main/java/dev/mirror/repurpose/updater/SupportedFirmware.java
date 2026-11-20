package dev.mirror.repurpose.updater;

import java.util.regex.Pattern;

/**
 * The firmware this supervisor installs updates on: the factory MIRROR image,
 * and Silvering (silvering/ in this repository) built for the same board.
 * Silvering keeps the factory boot image and is the same CAF Android 6.0.1
 * release (build ID MMB29M) rebuilt as product "silvering", so the package
 * installer and device-owner calls the supervisor relies on behave the same.
 * Its build number names the recipe commit and changes from build to build;
 * the build type and keys change once Silvering moves to release keys.
 */
final class SupportedFirmware {
    static final String FACTORY = BuildConfig.SUPPORTED_FINGERPRINT;
    static final String SILVERING_PATTERN =
            "mirror/silvering/msm8916_64:6\\.0\\.1/MMB29M/[0-9A-Za-z._-]+"
                    + ":(user|userdebug)/(release-keys|test-keys)";

    private static final Pattern SILVERING = Pattern.compile(SILVERING_PATTERN);

    private SupportedFirmware() {
    }

    static boolean supports(String fingerprint) {
        if (fingerprint == null) {
            return false;
        }
        return FACTORY.equals(fingerprint) || SILVERING.matcher(fingerprint).matches();
    }
}
