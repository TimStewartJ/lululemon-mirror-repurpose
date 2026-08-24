package dev.mirror.repurpose.updater;

final class OtaException extends Exception {
    OtaException(String message) {
        super(message);
    }

    OtaException(String message, Throwable cause) {
        super(message, cause);
    }
}
