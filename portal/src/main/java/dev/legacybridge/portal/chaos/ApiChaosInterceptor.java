package dev.legacybridge.portal.chaos;

import dev.legacybridge.portal.web.JsonErrors;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

/** Applies the effective {@link ChaosConfig.ApiMode} of each public-API operation before its handler runs. */
@Component
public class ApiChaosInterceptor implements HandlerInterceptor {

    public static final String DRIFT_ATTRIBUTE = "portal.chaos.drift";

    private static final String RETIRED_ON = "@1767225600";
    private static final String RETIRED_SUNSET = "Thu, 01 Jan 2026 00:00:00 GMT";
    private static final String DEPRECATED_SINCE = "@1782864000";
    private static final String DEPRECATED_SUNSET = "Fri, 01 Jan 2027 00:00:00 GMT";

    private final ChaosState chaos;

    public ApiChaosInterceptor(ChaosState chaos) {
        this.chaos = chaos;
    }

    public static boolean drift(HttpServletRequest request) {
        return Boolean.TRUE.equals(request.getAttribute(DRIFT_ATTRIBUTE));
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler)
            throws IOException {
        if (!(handler instanceof HandlerMethod method)) {
            return true;
        }
        var operation = method.getMethodAnnotation(Operation.class);
        if (operation == null) {
            return true;
        }
        switch (chaos.config().modeFor(operation.operationId())) {
            case OK -> {
                return true;
            }
            case DEPRECATED -> {
                response.setHeader("Deprecation", DEPRECATED_SINCE);
                response.setHeader("Sunset", DEPRECATED_SUNSET);
                return true;
            }
            case DRIFT -> {
                request.setAttribute(DRIFT_ATTRIBUTE, true);
                return true;
            }
            case NOT_FOUND -> JsonErrors.write(response, 404, "Not Found");
            case GONE -> {
                response.setHeader("Deprecation", RETIRED_ON);
                response.setHeader("Sunset", RETIRED_SUNSET);
                JsonErrors.write(response, 410, "This endpoint has been retired. Use the member services portal.");
            }
            case NOT_IMPLEMENTED -> JsonErrors.write(response, 501, "Not Implemented");
            case SERVER_ERROR -> JsonErrors.write(response, 500, "Internal Server Error");
        }
        return false;
    }
}
