package dev.legacybridge.portal.web;

import dev.legacybridge.portal.auth.AuthInterceptors;
import dev.legacybridge.portal.chaos.ApiChaosInterceptor;
import java.io.IOException;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.Resource;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import org.springframework.web.servlet.resource.PathResourceResolver;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final ApiChaosInterceptor apiChaos;
    private final AuthInterceptors.BasicAuth basicAuth;
    private final AuthInterceptors.SessionAuth sessionAuth;
    private final AuthInterceptors.AdminToken adminToken;

    public WebConfig(ApiChaosInterceptor apiChaos, AuthInterceptors.BasicAuth basicAuth,
                     AuthInterceptors.SessionAuth sessionAuth, AuthInterceptors.AdminToken adminToken) {
        this.apiChaos = apiChaos;
        this.basicAuth = basicAuth;
        this.sessionAuth = sessionAuth;
        this.adminToken = adminToken;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(adminToken).addPathPatterns("/admin/**");
        // Chaos runs before auth: a removed endpoint is a 404 whether or not the caller is authenticated.
        registry.addInterceptor(apiChaos).addPathPatterns("/api/v1/**");
        registry.addInterceptor(basicAuth).addPathPatterns("/api/v1/**");
        registry.addInterceptor(sessionAuth)
                .addPathPatterns("/internal/**")
                .excludePathPatterns("/internal/session", "/internal/ui-config");
    }

    /** Serves the SPA bundle; unknown non-API paths fall back to index.html so client-side routes deep-link. */
    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        registry.addResourceHandler("/**")
                .addResourceLocations("classpath:/static/")
                .resourceChain(true)
                .addResolver(new PathResourceResolver() {
                    @Override
                    protected Resource getResource(String resourcePath, Resource location) throws IOException {
                        var resource = location.createRelative(resourcePath);
                        if (resource.exists() && resource.isReadable()) {
                            return resource;
                        }
                        if (isBackendPath(resourcePath) || resourcePath.contains(".")) {
                            return null;
                        }
                        var index = location.createRelative("index.html");
                        return index.exists() ? index : null;
                    }
                });
    }

    private static boolean isBackendPath(String path) {
        return path.startsWith("api/") || path.startsWith("internal/") || path.startsWith("admin/")
                || path.startsWith("v3/") || path.startsWith("swagger-ui");
    }
}
