package dev.mirror.repurpose;

/* A board request the Mirror turns down, worded for whoever sent it: people
   and programs alike read the message to put the request right. */
public final class BoardError extends RuntimeException {
    public static final int INVALID = 400;
    public static final int UNAUTHORIZED = 401;
    public static final int NOT_FOUND = 404;
    public static final int METHOD_NOT_ALLOWED = 405;
    public static final int CONFLICT = 409;

    public final int status;
    /** The request field at fault, or null when no single field is. */
    public final String field;

    public BoardError(int status, String field, String message) {
        super(message);
        this.status = status;
        this.field = field;
    }

    public static BoardError invalid(String field, String message) {
        return new BoardError(INVALID, field, message);
    }
}
