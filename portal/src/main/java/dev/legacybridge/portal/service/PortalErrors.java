package dev.legacybridge.portal.service;

public final class PortalErrors {

    private PortalErrors() {
    }

    public static final class NotFound extends RuntimeException {
        public NotFound(String message) {
            super(message);
        }
    }

    public static final class Conflict extends RuntimeException {
        public Conflict(String message) {
            super(message);
        }
    }

    public static final class Invalid extends RuntimeException {
        public Invalid(String message) {
            super(message);
        }
    }
}
