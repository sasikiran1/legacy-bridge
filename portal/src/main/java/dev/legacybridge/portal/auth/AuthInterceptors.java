package dev.legacybridge.portal.auth;

import dev.legacybridge.portal.PortalProperties;
import dev.legacybridge.portal.auth.AuthService.SessionUser;
import dev.legacybridge.portal.chaos.ChaosState;
import dev.legacybridge.portal.web.JsonErrors;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

public final class AuthInterceptors {

    public static final String USER_ATTRIBUTE = "portal.user";
    public static final String SESSION_ATTRIBUTE = "portal.sessionUser";

    private AuthInterceptors() {
    }

    public static String actor(HttpServletRequest request) {
        return ((SessionUser) request.getAttribute(USER_ATTRIBUTE)).username();
    }

    /** HTTP Basic for the public API. */
    @Component
    public static class BasicAuth implements HandlerInterceptor {

        private final AuthService auth;
        private final ChaosState chaos;

        public BasicAuth(AuthService auth, ChaosState chaos) {
            this.auth = auth;
            this.chaos = chaos;
        }

        @Override
        public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler)
                throws IOException {
            var header = request.getHeader("Authorization");
            if (header != null && header.regionMatches(true, 0, "Basic ", 0, 6)) {
                var decoded = new String(Base64.getDecoder().decode(header.substring(6).strip()),
                        StandardCharsets.UTF_8);
                var colon = decoded.indexOf(':');
                if (colon > 0) {
                    var user = auth.authenticate(
                            decoded.substring(0, colon), decoded.substring(colon + 1), chaos.sessionEpoch());
                    if (user.isPresent()) {
                        request.setAttribute(USER_ATTRIBUTE, user.get());
                        return true;
                    }
                }
            }
            response.setHeader("WWW-Authenticate", "Basic realm=\"member-services\"");
            JsonErrors.write(response, 401, "Unauthorized");
            return false;
        }
    }

    /** Cookie sessions for the SPA's internal endpoints. Sessions die when chaos bumps the epoch. */
    @Component
    public static class SessionAuth implements HandlerInterceptor {

        private final ChaosState chaos;

        public SessionAuth(ChaosState chaos) {
            this.chaos = chaos;
        }

        @Override
        public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler)
                throws IOException {
            var session = request.getSession(false);
            var user = session == null ? null : (SessionUser) session.getAttribute(SESSION_ATTRIBUTE);
            if (user == null) {
                JsonErrors.write(response, 401, "not_authenticated");
                return false;
            }
            if (user.epoch() != chaos.sessionEpoch()) {
                session.invalidate();
                JsonErrors.write(response, 401, "session_expired");
                return false;
            }
            request.setAttribute(USER_ATTRIBUTE, user);
            return true;
        }
    }

    /** Shared-token guard for the admin (chaos, reset) endpoints. */
    @Component
    public static class AdminToken implements HandlerInterceptor {

        private final byte[] token;

        public AdminToken(PortalProperties properties) {
            this.token = properties.adminToken().getBytes(StandardCharsets.UTF_8);
        }

        @Override
        public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler)
                throws IOException {
            var presented = request.getHeader("X-Admin-Token");
            if (presented != null && MessageDigest.isEqual(token, presented.getBytes(StandardCharsets.UTF_8))) {
                return true;
            }
            JsonErrors.write(response, 403, "Forbidden");
            return false;
        }
    }
}
