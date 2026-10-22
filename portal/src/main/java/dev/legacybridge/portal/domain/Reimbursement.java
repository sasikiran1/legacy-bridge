package dev.legacybridge.portal.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.OffsetDateTime;

@Entity
@Table(name = "reimbursement")
public class Reimbursement {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String reference;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    private Member member;

    @ManyToOne(fetch = FetchType.LAZY)
    private Claim claim;

    private BigDecimal amount;
    private String payeeName;
    private String bankRouting;
    private String bankAccount;
    private String status;
    private OffsetDateTime submittedAt;
    private String submittedBy;

    protected Reimbursement() {
    }

    public Reimbursement(String reference, Member member, Claim claim, BigDecimal amount, String payeeName,
                         String bankRouting, String bankAccount, String submittedBy, OffsetDateTime submittedAt) {
        this.reference = reference;
        this.member = member;
        this.claim = claim;
        this.amount = amount;
        this.payeeName = payeeName;
        this.bankRouting = bankRouting;
        this.bankAccount = bankAccount;
        this.status = "SUBMITTED";
        this.submittedAt = submittedAt;
        this.submittedBy = submittedBy;
    }

    public String getReference() { return reference; }
    public String getStatus() { return status; }
    public OffsetDateTime getSubmittedAt() { return submittedAt; }
}
