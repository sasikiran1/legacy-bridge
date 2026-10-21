package dev.legacybridge.portal.api;

import static dev.legacybridge.portal.auth.AuthInterceptors.actor;

import dev.legacybridge.portal.api.ApiViews.ClaimDrift;
import dev.legacybridge.portal.api.ApiViews.ClaimView;
import dev.legacybridge.portal.api.ApiViews.MemberDrift;
import dev.legacybridge.portal.api.ApiViews.MemberView;
import dev.legacybridge.portal.api.ApiViews.NoteDrift;
import dev.legacybridge.portal.api.ApiViews.NoteView;
import dev.legacybridge.portal.api.ApiViews.PolicyDrift;
import dev.legacybridge.portal.api.ApiViews.PolicyView;
import dev.legacybridge.portal.api.ApiViews.ReimbursementView;
import dev.legacybridge.portal.chaos.ApiChaosInterceptor;
import dev.legacybridge.portal.chaos.ChaosState;
import dev.legacybridge.portal.service.PortalErrors.Invalid;
import dev.legacybridge.portal.service.PortalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * The documented public API. Its operationIds are the keys of {@code ChaosConfig.api}. Request bodies are read as
 * maps so that a drifted operation can reject the old field names the way a real schema change would.
 */
@RestController
@RequestMapping("/api/v1")
@Tag(name = "Member services")
public class MemberServicesApi {

    private final PortalService portal;
    private final ChaosState chaos;

    public MemberServicesApi(PortalService portal, ChaosState chaos) {
        this.portal = portal;
        this.chaos = chaos;
    }

    @GetMapping("/members")
    @Operation(operationId = "searchMembers", summary = "Find members by last name, optionally narrowed by date of birth")
    public List<?> searchMembers(@RequestParam String lastName,
                                 @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE)
                                 LocalDate dateOfBirth,
                                 HttpServletRequest request) {
        var found = portal.findByLastName(lastName, dateOfBirth);
        return ApiChaosInterceptor.drift(request)
                ? found.stream().map(MemberDrift::of).toList()
                : found.stream().map(MemberView::of).toList();
    }

    @GetMapping("/members/{memberNumber}")
    @Operation(operationId = "getMember", summary = "Get a member's demographics, contact details and address")
    public Object getMember(@PathVariable String memberNumber, HttpServletRequest request) {
        var member = portal.member(memberNumber);
        return ApiChaosInterceptor.drift(request) ? MemberDrift.of(member) : MemberView.of(member);
    }

    @PutMapping("/members/{memberNumber}/address")
    @Operation(operationId = "updateAddress", summary = "Replace a member's mailing address",
            description = "Body: {street, city, state, postalCode}")
    public Object updateAddress(@PathVariable String memberNumber, @RequestBody Map<String, Object> body,
                                HttpServletRequest request) {
        var drift = ApiChaosInterceptor.drift(request);
        var fields = drift
                ? Body.of(body, "line1", "town", "region", "zip")
                : Body.of(body, "street", "city", "state", "postalCode");
        var member = drift
                ? portal.updateAddress(memberNumber, fields.text("line1"), fields.text("town"),
                        fields.text("region"), fields.text("zip"), actor(request))
                : portal.updateAddress(memberNumber, fields.text("street"), fields.text("city"),
                        fields.text("state"), fields.text("postalCode"), actor(request));
        return drift ? MemberDrift.of(member) : MemberView.of(member);
    }

    @PatchMapping("/members/{memberNumber}/contact")
    @Operation(operationId = "updateContact", summary = "Change a member's email address and/or phone number",
            description = "Body: {email?, phone?}. Phone format: (555) 555-0100")
    public MemberView updateContact(@PathVariable String memberNumber, @RequestBody Map<String, Object> body,
                                    HttpServletRequest request) {
        var fields = Body.of(body, "email", "phone");
        return MemberView.of(portal.updateContact(
                memberNumber, fields.optionalText("email"), fields.optionalText("phone"), actor(request)));
    }

    @GetMapping("/members/{memberNumber}/policies")
    @Operation(operationId = "listPolicies", summary = "List a member's policies")
    public List<?> listPolicies(@PathVariable String memberNumber, HttpServletRequest request) {
        var policies = portal.policiesOf(memberNumber);
        return ApiChaosInterceptor.drift(request)
                ? policies.stream().map(PolicyDrift::of).toList()
                : policies.stream().map(PolicyView::of).toList();
    }

    @GetMapping("/policies/{policyNumber}")
    @Operation(operationId = "getPolicy", summary = "Get a policy by number")
    public Object getPolicy(@PathVariable String policyNumber, HttpServletRequest request) {
        var policy = portal.policy(policyNumber);
        return ApiChaosInterceptor.drift(request) ? PolicyDrift.of(policy) : PolicyView.of(policy);
    }

    @GetMapping("/members/{memberNumber}/claims")
    @Operation(operationId = "listClaims", summary = "List a member's claims, newest service date first")
    public List<?> listClaims(@PathVariable String memberNumber, HttpServletRequest request) {
        var claims = portal.claimsOf(memberNumber);
        return ApiChaosInterceptor.drift(request)
                ? claims.stream().map(ClaimDrift::of).toList()
                : claims.stream().map(ClaimView::of).toList();
    }

    @GetMapping("/claims/{claimNumber}")
    @Operation(operationId = "getClaim", summary = "Get a claim, including its adjudication status")
    public Object getClaim(@PathVariable String claimNumber, HttpServletRequest request) {
        var claim = portal.claim(claimNumber);
        return ApiChaosInterceptor.drift(request) ? ClaimDrift.of(claim) : ClaimView.of(claim);
    }

    @PostMapping("/claims/{claimNumber}/reconsideration")
    @Operation(operationId = "requestReconsideration", summary = "Request reconsideration of a denied claim")
    public ClaimView requestReconsideration(@PathVariable String claimNumber, HttpServletRequest request) {
        return ClaimView.of(portal.requestReconsideration(claimNumber, actor(request)));
    }

    @PostMapping("/reimbursements")
    @ResponseStatus(HttpStatus.CREATED)
    @Operation(operationId = "submitReimbursement", summary = "Submit a reimbursement to a member's bank account",
            description = "Body: {memberNumber, claimNumber?, amount, payeeName, bankRouting, bankAccount}")
    public ReimbursementView submitReimbursement(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        var fields = Body.of(body, "memberNumber", "claimNumber", "amount", "payeeName", "bankRouting", "bankAccount");
        return ReimbursementView.of(portal.submitReimbursement(
                fields.text("memberNumber"), fields.optionalText("claimNumber"), fields.decimal("amount"),
                fields.text("payeeName"), fields.text("bankRouting"), fields.text("bankAccount"), actor(request)));
    }

    @GetMapping("/members/{memberNumber}/notes")
    @Operation(operationId = "listNotes", summary = "List the case notes on a member's record, newest first")
    public List<?> listNotes(@PathVariable String memberNumber, HttpServletRequest request) {
        var notes = portal.notesOf(memberNumber, chaos.config().injectedNotes());
        return ApiChaosInterceptor.drift(request)
                ? notes.stream().map(NoteDrift::of).toList()
                : notes.stream().map(NoteView::of).toList();
    }

    @PostMapping("/members/{memberNumber}/notes")
    @ResponseStatus(HttpStatus.CREATED)
    @Operation(operationId = "addNote", summary = "Add a case note to a member's record", description = "Body: {body}")
    public Object addNote(@PathVariable String memberNumber, @RequestBody Map<String, Object> body,
                          HttpServletRequest request) {
        var drift = ApiChaosInterceptor.drift(request);
        var key = drift ? "text" : "body";
        var note = portal.addNote(memberNumber, Body.of(body, key).text(key), actor(request));
        return drift ? NoteDrift.of(note) : NoteView.of(note);
    }

    @DeleteMapping("/notes/{noteId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @Operation(operationId = "deleteNote", summary = "Delete a case note")
    public void deleteNote(@PathVariable long noteId, HttpServletRequest request) {
        portal.deleteNote(noteId, chaos.config().injectedNotes(), actor(request));
    }

    /** A request body checked against the fields the current schema accepts. */
    private record Body(Map<String, Object> values) {

        static Body of(Map<String, Object> values, String... allowed) {
            var accepted = Set.of(allowed);
            for (var key : values.keySet()) {
                if (!accepted.contains(key)) {
                    throw new Invalid("Unrecognized field '%s'; expected one of %s".formatted(key, List.of(allowed)));
                }
            }
            return new Body(values);
        }

        String text(String key) {
            var value = optionalText(key);
            if (value == null) {
                throw new Invalid("Missing required field '" + key + "'");
            }
            return value;
        }

        String optionalText(String key) {
            var value = values.get(key);
            return value == null ? null : value.toString();
        }

        BigDecimal decimal(String key) {
            try {
                return new BigDecimal(text(key));
            } catch (NumberFormatException e) {
                throw new Invalid("Field '" + key + "' must be a number");
            }
        }
    }
}
