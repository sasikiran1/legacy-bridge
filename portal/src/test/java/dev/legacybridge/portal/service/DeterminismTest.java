package dev.legacybridge.portal.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

class DeterminismTest {

    @Test
    void deterministicModeFixesTheClockAndReplaysReferencesAfterReset() {
        var determinism = new Determinism(true);
        assertThat(determinism.now()).isEqualTo(determinism.now());
        var first = IntStream.range(0, 5).map(i -> determinism.nextInt(1_000_000)).toArray();
        determinism.reset();
        var second = IntStream.range(0, 5).map(i -> determinism.nextInt(1_000_000)).toArray();
        assertThat(second).containsExactly(first);
    }

    @Test
    void normalModeUsesTheWallClock() {
        var determinism = new Determinism(false);
        assertThat(determinism.now().getYear()).isGreaterThanOrEqualTo(2026);
    }
}
