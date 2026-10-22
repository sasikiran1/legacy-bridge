package dev.legacybridge.portal.api;

import com.fasterxml.jackson.annotation.JsonProperty;
import dev.legacybridge.portal.domain.Claim;
import dev.legacybridge.portal.domain.Member;
import dev.legacybridge.portal.domain.MemberNote;
import dev.legacybridge.portal.domain.Policy;
import dev.legacybridge.portal.domain.Reimbursement;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

/**
 * Public API representations. Each resource has a current shape and a drifted shape: the drifted one is what
 * the API returns when chaos marks the operation as DRIFT (renamed fields, restructured objects, other formats).
 */
public final class ApiViews {

    private static final DateTimeFormatter US_DATE = DateTimeFormatter.ofPattern("MM/dd/yyyy");

    private ApiViews() {
    }

    public record Address(String street, String city, String state, String postalCode) {
    }

    /** Request bodies as documented in the OpenAPI schema. Handlers read raw maps so drift can reject old fields. */
    public record AddressBody(String street, String city, String state, String postalCode) {
    }

    public record ContactBody(String email, String phone) {
    }

    public record NoteBody(String body) {
    }

    public record ReimbursementBody(String memberNumber, String claimNumber, String amount, String payeeName,
                                    String bankRouting, String bankAccount) {
    }

    public record MemberView(String memberNumber, String firstName, String lastName, LocalDate dateOfBirth,
                             String email, String phone, Address address) {

        public static MemberView of(Member m) {
            return new MemberView(m.getMemberNumber(), m.getFirstName(), m.getLastName(), m.getDateOfBirth(),
                    m.getEmail(), m.getPhone(), new Address(m.getStreet(), m.getCity(), m.getState(), m.getPostalCode()));
        }
    }

    public record MemberDrift(
            @JsonProperty("member_no") String memberNo,
            @JsonProperty("name") Name name,
            @JsonProperty("dob") String dob,
            @JsonProperty("contact") Contact contact,
            @JsonProperty("mailing_address") String mailingAddress) {

        public record Name(@JsonProperty("given") String given, @JsonProperty("family") String family) {
        }

        public record Contact(@JsonProperty("email_address") String emailAddress,
                              @JsonProperty("phone_no") String phoneNo) {
        }

        public static MemberDrift of(Member m) {
            return new MemberDrift(m.getMemberNumber(), new Name(m.getFirstName(), m.getLastName()),
                    m.getDateOfBirth().format(US_DATE), new Contact(m.getEmail(), m.getPhone()),
                    "%s, %s, %s %s".formatted(m.getStreet(), m.getCity(), m.getState(), m.getPostalCode()));
        }
    }

    public record PolicyView(String policyNumber, String memberNumber, String product, Policy.Status status,
                             LocalDate effectiveDate, BigDecimal monthlyPremium) {

        public static PolicyView of(Policy p) {
            return new PolicyView(p.getPolicyNumber(), p.getMember().getMemberNumber(), p.getProduct(),
                    p.getStatus(), p.getEffectiveDate(), p.getMonthlyPremium());
        }
    }

    public record PolicyDrift(String policyNo, String plan, String state, String effective, Money premium) {

        public static PolicyDrift of(Policy p) {
            return new PolicyDrift(p.getPolicyNumber(), p.getProduct(), lower(p.getStatus()),
                    p.getEffectiveDate().format(US_DATE), new Money(p.getMonthlyPremium(), "USD"));
        }
    }

    public record ClaimView(String claimNumber, String policyNumber, String memberNumber, LocalDate serviceDate,
                            String providerName, BigDecimal amount, Claim.Status status,
                            boolean reconsiderationRequested) {

        public static ClaimView of(Claim c) {
            return new ClaimView(c.getClaimNumber(), c.getPolicy().getPolicyNumber(),
                    c.getPolicy().getMember().getMemberNumber(), c.getServiceDate(), c.getProviderName(),
                    c.getAmount(), c.getStatus(), c.isReconsiderationRequested());
        }
    }

    public record ClaimDrift(String claimNo, String state, Money billed, String provider, String dateOfService,
                             boolean underReview) {

        public static ClaimDrift of(Claim c) {
            return new ClaimDrift(c.getClaimNumber(), lower(c.getStatus()), new Money(c.getAmount(), "USD"),
                    c.getProviderName(), c.getServiceDate().format(US_DATE), c.isReconsiderationRequested());
        }
    }

    public record NoteView(long id, String author, String body, OffsetDateTime createdAt) {

        public static NoteView of(MemberNote n) {
            return new NoteView(n.getId(), n.getAuthor(), n.getBody(), n.getCreatedAt());
        }
    }

    public record NoteDrift(long noteId, String text, String by, String at) {

        public static NoteDrift of(MemberNote n) {
            return new NoteDrift(n.getId(), n.getBody(), n.getAuthor(), n.getCreatedAt().toString());
        }
    }

    public record ReimbursementView(String reference, String status, OffsetDateTime submittedAt) {

        public static ReimbursementView of(Reimbursement r) {
            return new ReimbursementView(r.getReference(), r.getStatus(), r.getSubmittedAt());
        }
    }

    public record Money(BigDecimal amount, String currency) {
    }

    private static String lower(Enum<?> value) {
        return value.name().toLowerCase(Locale.ROOT).replace('_', ' ');
    }
}
