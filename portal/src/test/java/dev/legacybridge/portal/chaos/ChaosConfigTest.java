package dev.legacybridge.portal.chaos;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.legacybridge.portal.chaos.ChaosConfig.ApiMode;
import dev.legacybridge.portal.service.PortalErrors.Invalid;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ChaosConfigTest {

    @Test
    void baselineAppliesUnlessOverridden() {
        var healthy = ChaosConfig.healthy();
        assertThat(healthy.modeFor("getMember")).isEqualTo(ApiMode.OK);
        assertThat(healthy.modeFor("submitReimbursement")).isEqualTo(ApiMode.NOT_FOUND);

        var restored = new ChaosConfig(Map.of("submitReimbursement", ApiMode.OK), null, 0, 0, null, false, false);
        assertThat(restored.modeFor("submitReimbursement")).isEqualTo(ApiMode.OK);
    }

    @Test
    void missingFieldsGetDefaults() {
        var config = new ChaosConfig(null, null, 0, 0, null, false, false);
        assertThat(config.domDrift()).isEqualTo(ChaosConfig.DomDrift.NONE);
        assertThat(config.wall()).isEqualTo(ChaosConfig.Wall.NONE);
        assertThat(config.sessionTtlSeconds()).isEqualTo(ChaosConfig.DEFAULT_SESSION_TTL_SECONDS);
    }

    @Test
    void rejectsUnknownOperationsAndOutOfRangeValues() {
        assertThatThrownBy(() -> new ChaosConfig(Map.of("nope", ApiMode.GONE), null, 0, 0, null, false, false))
                .isInstanceOf(Invalid.class);
        assertThatThrownBy(() -> new ChaosConfig(null, null, -1, 0, null, false, false))
                .isInstanceOf(Invalid.class);
        assertThatThrownBy(() -> new ChaosConfig(null, null, 0, 1, null, false, false))
                .isInstanceOf(Invalid.class);
    }
}
