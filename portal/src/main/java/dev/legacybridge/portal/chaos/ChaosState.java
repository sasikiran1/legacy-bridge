package dev.legacybridge.portal.chaos;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.stereotype.Component;

/** In-memory chaos state. The portal runs as a single instance, so no shared store is needed. */
@Component
public class ChaosState {

    private final AtomicReference<ChaosConfig> config = new AtomicReference<>(ChaosConfig.healthy());
    private final AtomicInteger sessionEpoch = new AtomicInteger();
    private final Set<String> wallCleared = ConcurrentHashMap.newKeySet();

    public ChaosConfig config() {
        return config.get();
    }

    public void apply(ChaosConfig next) {
        config.set(next);
        wallCleared.clear();
    }

    public int sessionEpoch() {
        return sessionEpoch.get();
    }

    /** Invalidates every SPA session created before this call. */
    public void expireSessions() {
        sessionEpoch.incrementAndGet();
    }

    /** Records that a human completed the verification wall for this user (the human-handoff path). */
    public void clearWall(String username) {
        wallCleared.add(username);
    }

    public boolean wallBlocks(String username) {
        return config().wall() != ChaosConfig.Wall.NONE && !wallCleared.contains(username);
    }
}
