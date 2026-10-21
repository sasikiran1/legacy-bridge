package dev.legacybridge.portal.service;

import dev.legacybridge.portal.domain.Claim;
import dev.legacybridge.portal.domain.Member;
import dev.legacybridge.portal.domain.MemberNote;
import dev.legacybridge.portal.domain.Policy;
import dev.legacybridge.portal.domain.Reimbursement;
import dev.legacybridge.portal.domain.Repositories.ClaimRepository;
import dev.legacybridge.portal.domain.Repositories.MemberNoteRepository;
import dev.legacybridge.portal.domain.Repositories.MemberRepository;
import dev.legacybridge.portal.domain.Repositories.PolicyRepository;
import dev.legacybridge.portal.domain.Repositories.ReimbursementRepository;
import dev.legacybridge.portal.service.PortalErrors.Conflict;
import dev.legacybridge.portal.service.PortalErrors.Invalid;
import dev.legacybridge.portal.service.PortalErrors.NotFound;
import java.math.BigDecimal;
import java.security.SecureRandom;
import java.time.LocalDate;
import java.util.List;
import java.util.regex.Pattern;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Business operations shared by the public API and the SPA's internal endpoints. */
@Service
@Transactional
public class PortalService {

    private static final Pattern STATE = Pattern.compile("[A-Z]{2}");
    private static final Pattern POSTAL_CODE = Pattern.compile("\\d{5}(-\\d{4})?");
    private static final Pattern PHONE = Pattern.compile("\\(\\d{3}\\) \\d{3}-\\d{4}");
    private static final Pattern EMAIL = Pattern.compile("[^@\\s]+@[^@\\s]+\\.[a-z]{2,}");
    private static final Pattern ROUTING = Pattern.compile("\\d{9}");
    private static final Pattern ACCOUNT = Pattern.compile("\\d{4,17}");

    private final MemberRepository members;
    private final PolicyRepository policies;
    private final ClaimRepository claims;
    private final MemberNoteRepository notes;
    private final ReimbursementRepository reimbursements;
    private final SecureRandom random = new SecureRandom();

    public PortalService(MemberRepository members, PolicyRepository policies, ClaimRepository claims,
                         MemberNoteRepository notes, ReimbursementRepository reimbursements) {
        this.members = members;
        this.policies = policies;
        this.claims = claims;
        this.notes = notes;
        this.reimbursements = reimbursements;
    }

    @Transactional(readOnly = true)
    public Member member(String memberNumber) {
        return members.findByMemberNumber(memberNumber)
                .orElseThrow(() -> new NotFound("No member with number " + memberNumber));
    }

    @Transactional(readOnly = true)
    public List<Member> search(String query) {
        var q = query == null ? "" : query.strip();
        if (q.length() < 2) {
            throw new Invalid("Enter at least 2 characters to search");
        }
        return members.search(q, Limit.of(50));
    }

    @Transactional(readOnly = true)
    public List<Member> findByLastName(String lastName, LocalDate dateOfBirth) {
        if (lastName == null || lastName.isBlank()) {
            throw new Invalid("lastName is required");
        }
        return dateOfBirth == null
                ? members.findByLastNameIgnoreCaseOrderByFirstName(lastName.strip())
                : members.findByLastNameIgnoreCaseAndDateOfBirth(lastName.strip(), dateOfBirth);
    }

    public Member updateAddress(String memberNumber, String street, String city, String state, String postalCode,
                                String actor) {
        require(street != null && !street.isBlank(), "street is required");
        require(city != null && !city.isBlank(), "city is required");
        require(state != null && STATE.matcher(state).matches(), "state must be a two-letter code");
        require(postalCode != null && POSTAL_CODE.matcher(postalCode).matches(), "postal code must be 5 digits");
        var member = member(memberNumber);
        member.changeAddress(street.strip(), city.strip(), state, postalCode, actor);
        return member;
    }

    public Member updateContact(String memberNumber, String email, String phone, String actor) {
        require(email != null || phone != null, "email or phone is required");
        require(email == null || EMAIL.matcher(email).matches(), "email is not valid");
        require(phone == null || PHONE.matcher(phone).matches(), "phone must look like (555) 555-0100");
        var member = member(memberNumber);
        member.changeContact(email, phone, actor);
        return member;
    }

    @Transactional(readOnly = true)
    public List<Policy> policiesOf(String memberNumber) {
        return policies.findByMemberOrderByProduct(member(memberNumber));
    }

    @Transactional(readOnly = true)
    public Policy policy(String policyNumber) {
        return policies.findByPolicyNumber(policyNumber)
                .orElseThrow(() -> new NotFound("No policy with number " + policyNumber));
    }

    @Transactional(readOnly = true)
    public List<Claim> claimsOf(String memberNumber) {
        return claims.findByPolicyMemberOrderByServiceDateDesc(member(memberNumber));
    }

    @Transactional(readOnly = true)
    public Claim claim(String claimNumber) {
        return claims.findByClaimNumber(claimNumber)
                .orElseThrow(() -> new NotFound("No claim with number " + claimNumber));
    }

    public Claim requestReconsideration(String claimNumber, String actor) {
        var claim = claim(claimNumber);
        if (claim.getStatus() != Claim.Status.DENIED) {
            throw new Conflict("Only denied claims can be reconsidered");
        }
        if (claim.isReconsiderationRequested()) {
            throw new Conflict("Reconsideration has already been requested for this claim");
        }
        claim.requestReconsideration(actor);
        return claim;
    }

    @Transactional(readOnly = true)
    public List<MemberNote> notesOf(String memberNumber, boolean includeInjected) {
        return notes.findByMemberAndDeletedAtIsNullOrderByCreatedAtDesc(member(memberNumber)).stream()
                .filter(n -> includeInjected || !n.isInjected())
                .toList();
    }

    public MemberNote addNote(String memberNumber, String body, String actor) {
        require(body != null && !body.isBlank(), "note text is required");
        require(body.length() <= 2000, "note text is limited to 2000 characters");
        return notes.save(new MemberNote(member(memberNumber), actor, body.strip()));
    }

    public void deleteNote(long noteId, boolean includeInjected, String actor) {
        var note = notes.findById(noteId)
                .filter(n -> !n.isDeleted() && (includeInjected || !n.isInjected()))
                .orElseThrow(() -> new NotFound("No note with id " + noteId));
        note.softDelete(actor);
    }

    public Reimbursement submitReimbursement(String memberNumber, String claimNumber, BigDecimal amount,
                                             String payeeName, String routing, String account, String actor) {
        var member = member(memberNumber);
        Claim claim = null;
        if (claimNumber != null && !claimNumber.isBlank()) {
            claim = claim(claimNumber.strip());
            require(claim.getPolicy().getMember().getId().equals(member.getId()),
                    "claim " + claimNumber + " does not belong to member " + memberNumber);
            if (!claim.isPayable()) {
                throw new Conflict("Reimbursements can only be filed against approved or paid claims");
            }
        }
        require(amount != null && amount.signum() > 0, "amount must be positive");
        require(amount.scale() <= 2, "amount must have at most two decimal places");
        require(claim == null || amount.compareTo(claim.getAmount()) <= 0, "amount exceeds the claim amount");
        require(payeeName != null && !payeeName.isBlank(), "payee name is required");
        require(routing != null && ROUTING.matcher(routing).matches() && routingChecksumValid(routing),
                "routing number is not valid");
        require(account != null && ACCOUNT.matcher(account).matches(), "account number must be 4-17 digits");

        var reference = nextReference();
        return reimbursements.save(
                new Reimbursement(reference, member, claim, amount, payeeName.strip(), routing, account, actor));
    }

    private String nextReference() {
        String reference;
        do {
            reference = "RB-%06d".formatted(random.nextInt(1_000_000));
        } while (reimbursements.existsByReference(reference));
        return reference;
    }

    static boolean routingChecksumValid(String routing) {
        int[] weights = {3, 7, 1, 3, 7, 1, 3, 7, 1};
        int sum = 0;
        for (int i = 0; i < 9; i++) {
            sum += (routing.charAt(i) - '0') * weights[i];
        }
        return sum % 10 == 0;
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new Invalid(message);
        }
    }
}
