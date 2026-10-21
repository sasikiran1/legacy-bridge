package dev.legacybridge.portal.chaos;

import dev.legacybridge.portal.service.PortalErrors.Invalid;
import java.util.Map;
import java.util.Set;

/**
 * Degradations applied to the running portal. Replaced wholesale via {@code PUT /admin/chaos}.
 *
 * @param api               per-operation override of the public API's behaviour, keyed by OpenAPI operationId
 * @param domDrift          SPA markup changes: renamed labels/ids, moved buttons, an interstitial modal
 * @param latencyMs         delay added to every public and internal API response
 * @param sessionTtlSeconds idle timeout for SPA sessions created after the change
 * @param wall              human-verification wall shown at SPA login
 * @param poorA11y          render the reimbursement form without an accessibility tree
 * @param injectedNotes     reveal the planted prompt-injection notes
 */
public record ChaosConfig(
        Map<String, ApiMode> api,
        DomDrift domDrift,
        Integer latencyMs,
        Integer sessionTtlSeconds,
        Wall wall,
        Boolean poorA11y,
        Boolean injectedNotes) {

    public enum ApiMode { OK, DEPRECATED, DRIFT, NOT_FOUND, GONE, NOT_IMPLEMENTED, SERVER_ERROR }

    public enum Wall { NONE, CAPTCHA, MFA }

    /** Components are boxed so that a partial JSON body leaves the omitted toggles off. */
    public record DomDrift(Boolean labels, Boolean layout, Boolean interstitial) {
        public static final DomDrift NONE = new DomDrift(false, false, false);

        public DomDrift {
            labels = Boolean.TRUE.equals(labels);
            layout = Boolean.TRUE.equals(layout);
            interstitial = Boolean.TRUE.equals(interstitial);
        }
    }

    public static final Set<String> OPERATIONS = Set.of(
            "searchMembers", "getMember", "updateAddress", "updateContact",
            "listPolicies", "getPolicy", "listClaims", "getClaim", "requestReconsideration",
            "submitReimbursement", "listNotes", "addNote", "deleteNote");

    /** How the legacy API behaves with no chaos applied: parts of it were never finished or were retired. */
    public static final Map<String, ApiMode> BASELINE = Map.of(
            "updateContact", ApiMode.NOT_IMPLEMENTED,
            "requestReconsideration", ApiMode.GONE,
            "submitReimbursement", ApiMode.NOT_FOUND,
            "deleteNote", ApiMode.GONE);

    public static final int DEFAULT_SESSION_TTL_SECONDS = 1800;

    public ChaosConfig {
        api = api == null ? Map.of() : Map.copyOf(api);
        domDrift = domDrift == null ? DomDrift.NONE : domDrift;
        wall = wall == null ? Wall.NONE : wall;
        latencyMs = latencyMs == null ? 0 : latencyMs;
        sessionTtlSeconds = sessionTtlSeconds == null || sessionTtlSeconds == 0
                ? DEFAULT_SESSION_TTL_SECONDS : sessionTtlSeconds;
        poorA11y = Boolean.TRUE.equals(poorA11y);
        injectedNotes = Boolean.TRUE.equals(injectedNotes);
        for (var operation : api.keySet()) {
            if (!OPERATIONS.contains(operation)) {
                throw new Invalid("unknown operationId in chaos config: " + operation);
            }
        }
        if (latencyMs < 0 || latencyMs > 30_000) {
            throw new Invalid("latencyMs must be between 0 and 30000");
        }
        if (sessionTtlSeconds < 5 || sessionTtlSeconds > 86_400) {
            throw new Invalid("sessionTtlSeconds must be between 5 and 86400");
        }
    }

    public static ChaosConfig healthy() {
        return new ChaosConfig(Map.of(), DomDrift.NONE, 0, DEFAULT_SESSION_TTL_SECONDS, Wall.NONE, false, false);
    }

    public ApiMode modeFor(String operationId) {
        return api.getOrDefault(operationId, BASELINE.getOrDefault(operationId, ApiMode.OK));
    }
}
