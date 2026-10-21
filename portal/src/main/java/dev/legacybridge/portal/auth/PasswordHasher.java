package dev.legacybridge.portal.auth;

import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** PBKDF2-HMAC-SHA256 password hashes in the form {@code pbkdf2$<iterations>$<salt>$<hash>}. */
public final class PasswordHasher {

    private static final int ITERATIONS = 210_000;
    private static final int KEY_BITS = 256;
    private static final SecureRandom RANDOM = new SecureRandom();

    private PasswordHasher() {
    }

    public static String hash(String password) {
        var salt = new byte[16];
        RANDOM.nextBytes(salt);
        var encoder = Base64.getEncoder();
        return "pbkdf2$%d$%s$%s".formatted(
                ITERATIONS, encoder.encodeToString(salt), encoder.encodeToString(derive(password, salt, ITERATIONS)));
    }

    public static boolean matches(String password, String stored) {
        if (stored == null) {
            return false;
        }
        var parts = stored.split("\\$");
        if (parts.length != 4 || !parts[0].equals("pbkdf2")) {
            return false;
        }
        var decoder = Base64.getDecoder();
        var expected = decoder.decode(parts[3]);
        var actual = derive(password, decoder.decode(parts[2]), Integer.parseInt(parts[1]));
        return MessageDigest.isEqual(expected, actual);
    }

    private static byte[] derive(String password, byte[] salt, int iterations) {
        try {
            var spec = new PBEKeySpec(password.toCharArray(), salt, iterations, KEY_BITS);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("PBKDF2 unavailable", e);
        }
    }
}
