package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

final class HomeSelection {
    private HomeSelection() {
    }

    static boolean isMirrorHomeSelected(Context context) {
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        ResolveInfo resolved = context.getPackageManager()
                .resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY);
        return resolved != null
                && resolved.activityInfo != null
                && context.getPackageName().equals(resolved.activityInfo.packageName)
                && MainActivity.class.getName().equals(resolved.activityInfo.name);
    }

    /** The packages of the HOME apps beside Mirror Home: on a Mirror, the factory launcher. */
    static Set<String> otherHomePackages(Context context) {
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        List<ResolveInfo> homes = context.getPackageManager().queryIntentActivities(home, 0);
        Set<String> packages = new LinkedHashSet<>();
        for (ResolveInfo candidate : homes) {
            if (candidate.activityInfo != null
                    && !context.getPackageName().equals(candidate.activityInfo.packageName)) {
                packages.add(candidate.activityInfo.packageName);
            }
        }
        return packages;
    }
}
