package dev.legacybridge.portal.internal;

import dev.legacybridge.portal.auth.AuthInterceptors;
import dev.legacybridge.portal.auth.AuthService;
import dev.legacybridge.portal.auth.AuthService.SessionUser;
import dev.legacybridge.portal.chaos.ChaosState;
import dev.legacybridge.portal.internal.InternalViews.Challenge;
import dev.legacybridge.portal.internal.InternalViews.Credentials;
import dev.legacybridge.portal.internal.InternalViews.Me;
import dev.legacybridge.portal.internal.InternalViews.UiConfig;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal")
public class SessionController {

    private final AuthService auth;
    private final ChaosState chaos;

    public SessionController(AuthService auth, ChaosState chaos) {
        this.auth = auth;
        this.chaos = chaos;
    }

    @GetMapping("/ui-config")
    public UiConfig uiConfig() {
        var config = chaos.config();
        return new UiConfig(config.domDrift(), config.poorA11y(), config.wall());
    }

    @GetMapping("/session")
    public ResponseEntity<?> current(HttpServletRequest request) {
        var session = request.getSession(false);
        var user = session == null ? null : (SessionUser) session.getAttribute(AuthInterceptors.SESSION_ATTRIBUTE);
        if (user == null || user.epoch() != chaos.sessionEpoch()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "not_authenticated"));
        }
        return ResponseEntity.ok(new Me(user.username(), user.displayName(), user.role()));
    }

    /**
     * Signs in. While a verification wall is active the credentials are accepted but the session is withheld
     * until a human clears the wall through {@code POST /admin/wall/clear}; there is no programmatic solve path.
     */
    @PostMapping("/session")
    public ResponseEntity<?> signIn(@RequestBody Credentials credentials, HttpServletRequest request) {
        var user = auth.authenticate(credentials.username(), credentials.password(), chaos.sessionEpoch());
        if (user.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "Invalid username or password"));
        }
        if (chaos.wallBlocks(user.get().username())) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(new Challenge(chaos.config().wall().name()));
        }
        var existing = request.getSession(false);
        if (existing != null) {
            existing.invalidate();
        }
        var session = request.getSession(true);
        session.setMaxInactiveInterval(chaos.config().sessionTtlSeconds());
        session.setAttribute(AuthInterceptors.SESSION_ATTRIBUTE, user.get());
        var u = user.get();
        return ResponseEntity.ok(new Me(u.username(), u.displayName(), u.role()));
    }

    @DeleteMapping("/session")
    public ResponseEntity<Void> signOut(HttpServletRequest request) {
        var session = request.getSession(false);
        if (session != null) {
            session.invalidate();
        }
        return ResponseEntity.noContent().build();
    }
}
