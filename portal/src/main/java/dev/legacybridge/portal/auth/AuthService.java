package dev.legacybridge.portal.auth;

import dev.legacybridge.portal.PortalProperties;
import dev.legacybridge.portal.domain.PortalUser;
import dev.legacybridge.portal.domain.Repositories.PortalUserRepository;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AuthService implements ApplicationRunner {

    private final PortalUserRepository users;
    private final PortalProperties properties;

    /**
     * Basic auth sends the password on every API call; re-deriving PBKDF2 each time would make the API itself
     * the slow path. Successful checks are cached by a SHA-256 of the credentials until the next reset.
     */
    private final Map<String, String> verified = new ConcurrentHashMap<>();

    public AuthService(PortalUserRepository users, PortalProperties properties) {
        this.users = users;
        this.properties = properties;
    }

    @Override
    public void run(ApplicationArguments args) {
        assignTestUserPasswords();
    }

    /** Seeded users carry no password hash; every one gets the configured test password. */
    @Transactional
    public void assignTestUserPasswords() {
        var hash = PasswordHasher.hash(properties.testUserPassword());
        var all = users.findAll();
        all.forEach(user -> user.setPasswordHash(hash));
        users.saveAll(all);
        verified.clear();
    }

    @Transactional(readOnly = true)
    public Optional<SessionUser> authenticate(String username, String password, int sessionEpoch) {
        if (username == null || password == null) {
            return Optional.empty();
        }
        var key = fingerprint(username, password);
        var cached = verified.get(key);
        if (cached != null) {
            return users.findByUsername(cached).map(u -> SessionUser.of(u, sessionEpoch));
        }
        return users.findByUsername(username)
                .filter(u -> PasswordHasher.matches(password, u.getPasswordHash()))
                .map(u -> {
                    verified.put(key, u.getUsername());
                    return SessionUser.of(u, sessionEpoch);
                });
    }

    private static String fingerprint(String username, String password) {
        try {
            var digest = MessageDigest.getInstance("SHA-256")
                    .digest((username + "\u0000" + password).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public record SessionUser(String username, String displayName, String role, int epoch)
            implements java.io.Serializable {

        static SessionUser of(PortalUser user, int epoch) {
            return new SessionUser(user.getUsername(), user.getDisplayName(), user.getRole(), epoch);
        }
    }
}
