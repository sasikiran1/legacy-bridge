package dev.legacybridge.portal.internal;

import dev.legacybridge.portal.api.ApiViews.ClaimView;
import dev.legacybridge.portal.api.ApiViews.NoteView;
import dev.legacybridge.portal.api.ApiViews.PolicyView;
import dev.legacybridge.portal.chaos.ChaosConfig;
import dev.legacybridge.portal.domain.Member;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** Shapes used only by the SPA. Not part of the public API and not in the OpenAPI document. */
public final class InternalViews {

    private InternalViews() {
    }

    public record UiConfig(ChaosConfig.DomDrift domDrift, boolean poorA11y, ChaosConfig.Wall wall) {
    }

    public record Credentials(String username, String password) {
    }

    public record Me(String username, String displayName, String role) {
    }

    public record Challenge(String challenge) {
    }

    public record MemberSummary(String memberNumber, String firstName, String lastName, LocalDate dateOfBirth,
                                String city, String state) {

        public static MemberSummary of(Member m) {
            return new MemberSummary(m.getMemberNumber(), m.getFirstName(), m.getLastName(), m.getDateOfBirth(),
                    m.getCity(), m.getState());
        }
    }

    public record MemberDetail(String memberNumber, String firstName, String lastName, LocalDate dateOfBirth,
                               String ssnLast4, String email, String phone, String street, String city,
                               String state, String postalCode, List<PolicyView> policies, List<ClaimView> claims,
                               List<NoteView> notes) {
    }

    public record AddressUpdate(String street, String city, String state, String postalCode) {
    }

    public record ContactUpdate(String email, String phone) {
    }

    public record NewNote(String body) {
    }

    public record ClaimDetail(ClaimView claim, MemberSummary member) {
    }

    public record NewReimbursement(String memberNumber, String claimNumber, BigDecimal amount, String payeeName,
                                   String bankRouting, String bankAccount) {
    }
}
