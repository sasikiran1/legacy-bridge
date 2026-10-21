package dev.legacybridge.portal;

import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/**
 * @param adminToken       shared secret for the chaos/reset admin endpoints (header {@code X-Admin-Token})
 * @param testUserPassword password assigned to every seeded portal user at startup and after reset
 */
@Validated
@ConfigurationProperties("portal")
public record PortalProperties(@NotBlank String adminToken, @NotBlank String testUserPassword) {
}
