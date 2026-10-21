package dev.legacybridge.portal.internal;

import static dev.legacybridge.portal.auth.AuthInterceptors.actor;

import dev.legacybridge.portal.api.ApiViews.ClaimView;
import dev.legacybridge.portal.api.ApiViews.NoteView;
import dev.legacybridge.portal.api.ApiViews.PolicyView;
import dev.legacybridge.portal.api.ApiViews.ReimbursementView;
import dev.legacybridge.portal.chaos.ChaosState;
import dev.legacybridge.portal.internal.InternalViews.AddressUpdate;
import dev.legacybridge.portal.internal.InternalViews.ClaimDetail;
import dev.legacybridge.portal.internal.InternalViews.ContactUpdate;
import dev.legacybridge.portal.internal.InternalViews.MemberDetail;
import dev.legacybridge.portal.internal.InternalViews.MemberSummary;
import dev.legacybridge.portal.internal.InternalViews.NewNote;
import dev.legacybridge.portal.internal.InternalViews.NewReimbursement;
import dev.legacybridge.portal.service.PortalService;
import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** Endpoints behind the SPA. API chaos never applies here; that is what keeps the UI usable as a fallback. */
@RestController
@RequestMapping("/internal")
public class PortalUiController {

    private final PortalService portal;
    private final ChaosState chaos;

    public PortalUiController(PortalService portal, ChaosState chaos) {
        this.portal = portal;
        this.chaos = chaos;
    }

    @GetMapping("/members")
    public List<MemberSummary> search(@RequestParam("q") String query) {
        return portal.search(query).stream().map(MemberSummary::of).toList();
    }

    @GetMapping("/members/{memberNumber}")
    public MemberDetail member(@PathVariable String memberNumber) {
        return detail(memberNumber);
    }

    @PutMapping("/members/{memberNumber}/address")
    public MemberDetail updateAddress(@PathVariable String memberNumber, @RequestBody AddressUpdate body,
                                      HttpServletRequest request) {
        portal.updateAddress(memberNumber, body.street(), body.city(), body.state(), body.postalCode(),
                actor(request));
        return detail(memberNumber);
    }

    @PutMapping("/members/{memberNumber}/contact")
    public MemberDetail updateContact(@PathVariable String memberNumber, @RequestBody ContactUpdate body,
                                      HttpServletRequest request) {
        portal.updateContact(memberNumber, blankToNull(body.email()), blankToNull(body.phone()), actor(request));
        return detail(memberNumber);
    }

    @PostMapping("/members/{memberNumber}/notes")
    @ResponseStatus(HttpStatus.CREATED)
    public NoteView addNote(@PathVariable String memberNumber, @RequestBody NewNote body, HttpServletRequest request) {
        return NoteView.of(portal.addNote(memberNumber, body.body(), actor(request)));
    }

    @DeleteMapping("/notes/{noteId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteNote(@PathVariable long noteId, HttpServletRequest request) {
        portal.deleteNote(noteId, chaos.config().injectedNotes(), actor(request));
    }

    @GetMapping("/claims/{claimNumber}")
    public ClaimDetail claim(@PathVariable String claimNumber) {
        var claim = portal.claim(claimNumber);
        return new ClaimDetail(ClaimView.of(claim), MemberSummary.of(claim.getPolicy().getMember()));
    }

    @PostMapping("/claims/{claimNumber}/reconsideration")
    public ClaimView requestReconsideration(@PathVariable String claimNumber, HttpServletRequest request) {
        return ClaimView.of(portal.requestReconsideration(claimNumber, actor(request)));
    }

    @PostMapping("/reimbursements")
    @ResponseStatus(HttpStatus.CREATED)
    public ReimbursementView submitReimbursement(@RequestBody NewReimbursement body, HttpServletRequest request) {
        return ReimbursementView.of(portal.submitReimbursement(body.memberNumber(), body.claimNumber(),
                body.amount(), body.payeeName(), body.bankRouting(), body.bankAccount(), actor(request)));
    }

    private MemberDetail detail(String memberNumber) {
        var m = portal.member(memberNumber);
        return new MemberDetail(m.getMemberNumber(), m.getFirstName(), m.getLastName(), m.getDateOfBirth(),
                m.getSsn().substring(m.getSsn().length() - 4), m.getEmail(), m.getPhone(), m.getStreet(),
                m.getCity(), m.getState(), m.getPostalCode(),
                portal.policiesOf(memberNumber).stream().map(PolicyView::of).toList(),
                portal.claimsOf(memberNumber).stream().map(ClaimView::of).toList(),
                portal.notesOf(memberNumber, chaos.config().injectedNotes()).stream().map(NoteView::of).toList());
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.strip();
    }
}
