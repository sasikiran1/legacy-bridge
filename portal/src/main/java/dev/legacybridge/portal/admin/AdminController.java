package dev.legacybridge.portal.admin;

import dev.legacybridge.portal.auth.AuthService;
import dev.legacybridge.portal.chaos.ChaosConfig;
import dev.legacybridge.portal.chaos.ChaosState;
import dev.legacybridge.portal.service.Determinism;
import org.flywaydb.core.Flyway;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** Testbed controls. Guarded by {@code X-Admin-Token}; excluded from the OpenAPI document. */
@RestController
@RequestMapping("/admin")
public class AdminController {

    private final ChaosState chaos;
    private final Flyway flyway;
    private final AuthService auth;
    private final Determinism determinism;

    public AdminController(ChaosState chaos, Flyway flyway, AuthService auth, Determinism determinism) {
        this.chaos = chaos;
        this.flyway = flyway;
        this.auth = auth;
        this.determinism = determinism;
    }

    @GetMapping("/chaos")
    public ChaosConfig chaos() {
        return chaos.config();
    }

    @PutMapping("/chaos")
    public ChaosConfig applyChaos(@RequestBody ChaosConfig config) {
        chaos.apply(config);
        return chaos.config();
    }

    @PostMapping("/sessions/expire")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void expireSessions() {
        chaos.expireSessions();
    }

    /** The human-handoff resolution: an operator has completed the CAPTCHA/MFA step for this user. */
    @PostMapping("/wall/clear")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void clearWall(@RequestBody WallClear body) {
        chaos.clearWall(body.username());
    }

    /** Restores the seed dataset. Run between tasks so every task starts from the same state. */
    @PostMapping("/reset")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public synchronized void reset() {
        flyway.clean();
        flyway.migrate();
        auth.assignTestUserPasswords();
        determinism.reset();
        chaos.expireSessions();
    }

    public record WallClear(String username) {
    }
}
