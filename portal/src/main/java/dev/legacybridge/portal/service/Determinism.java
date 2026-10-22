package dev.legacybridge.portal.service;

import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Random;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Clock and random source for writes. In deterministic mode ({@code portal.deterministic=true}) timestamps are fixed
 * and reference numbers come from a seeded generator that {@code /admin/reset} reseeds, so a replayed agent run sees
 * byte-identical tool results and its recorded LLM exchanges stay valid.
 */
@Component
public class Determinism {

    private static final OffsetDateTime FIXED_NOW = OffsetDateTime.parse("2026-10-01T09:00:00Z");
    private static final long SEED = 20261001L;

    private final boolean enabled;
    private Random random;

    public Determinism(@Value("${portal.deterministic:false}") boolean enabled) {
        this.enabled = enabled;
        reset();
    }

    public OffsetDateTime now() {
        return enabled ? FIXED_NOW : OffsetDateTime.now(ZoneOffset.UTC);
    }

    public synchronized int nextInt(int bound) {
        return random.nextInt(bound);
    }

    public final synchronized void reset() {
        random = enabled ? new Random(SEED) : new SecureRandom();
    }
}
