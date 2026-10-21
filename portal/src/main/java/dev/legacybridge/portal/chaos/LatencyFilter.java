package dev.legacybridge.portal.chaos;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Adds the configured delay to API traffic. Requests run on virtual threads, so sleeping is cheap. */
@Component
public class LatencyFilter extends OncePerRequestFilter {

    private final ChaosState chaos;

    public LatencyFilter(ChaosState chaos) {
        this.chaos = chaos;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        var path = request.getRequestURI();
        return !(path.startsWith("/api/") || path.startsWith("/internal/"));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        var delay = chaos.config().latencyMs();
        if (delay > 0) {
            try {
                Thread.sleep(delay);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
        chain.doFilter(request, response);
    }
}
