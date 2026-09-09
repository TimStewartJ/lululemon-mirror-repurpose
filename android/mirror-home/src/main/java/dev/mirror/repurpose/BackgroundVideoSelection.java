package dev.mirror.repurpose;

import java.util.Objects;

final class BackgroundVideoSelection {
    final String activeId;
    final String previousId;

    BackgroundVideoSelection(String activeId, String previousId) {
        this.activeId = clean(activeId);
        this.previousId = clean(previousId);
    }

    BackgroundVideoSelection activate(String nextId) {
        String normalized = clean(nextId);
        if (normalized.equals(activeId)) {
            return this;
        }
        return new BackgroundVideoSelection(normalized, activeId);
    }

    BackgroundVideoSelection rollback() {
        if (previousId.isEmpty()) {
            return this;
        }
        return new BackgroundVideoSelection(previousId, activeId);
    }

    BackgroundVideoSelection remove(String id) {
        String normalized = clean(id);
        return new BackgroundVideoSelection(
                normalized.equals(activeId) ? "" : activeId,
                normalized.equals(previousId) ? "" : previousId);
    }

    boolean canRollback() {
        return !previousId.isEmpty() && !Objects.equals(activeId, previousId);
    }

    static boolean validId(String id) {
        return id != null && id.matches("[0-9a-f]{64}");
    }

    private static String clean(String id) {
        return validId(id) ? id : "";
    }
}
