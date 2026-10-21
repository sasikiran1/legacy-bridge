package dev.legacybridge.portal.web;

import dev.legacybridge.portal.service.PortalErrors;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class ErrorHandling {

    @ExceptionHandler(PortalErrors.NotFound.class)
    ResponseEntity<Map<String, String>> notFound(PortalErrors.NotFound e) {
        return error(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler(PortalErrors.Conflict.class)
    ResponseEntity<Map<String, String>> conflict(PortalErrors.Conflict e) {
        return error(HttpStatus.CONFLICT, e.getMessage());
    }

    @ExceptionHandler(PortalErrors.Invalid.class)
    ResponseEntity<Map<String, String>> invalid(PortalErrors.Invalid e) {
        return error(HttpStatus.BAD_REQUEST, e.getMessage());
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class,
            MissingServletRequestParameterException.class})
    ResponseEntity<Map<String, String>> unreadable(Exception e) {
        // Validation inside a record constructor surfaces wrapped in the deserialization error.
        for (Throwable cause = e; cause != null; cause = cause.getCause()) {
            if (cause instanceof PortalErrors.Invalid invalid) {
                return error(HttpStatus.BAD_REQUEST, invalid.getMessage());
            }
        }
        return error(HttpStatus.BAD_REQUEST, "Malformed request");
    }

    private static ResponseEntity<Map<String, String>> error(HttpStatus status, String message) {
        return ResponseEntity.status(status).body(Map.of("error", message));
    }
}
