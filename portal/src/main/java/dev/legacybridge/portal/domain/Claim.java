package dev.legacybridge.portal.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

@Entity
@Table(name = "claim")
public class Claim {

    public enum Status { RECEIVED, IN_REVIEW, APPROVED, DENIED, PAID }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String claimNumber;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    private Policy policy;

    private LocalDate serviceDate;
    private String providerName;
    private BigDecimal amount;

    @Enumerated(EnumType.STRING)
    private Status status;

    private boolean reconsiderationRequested;
    private OffsetDateTime updatedAt;
    private String updatedBy;

    protected Claim() {
    }

    public void requestReconsideration(String actor) {
        this.reconsiderationRequested = true;
        this.updatedAt = OffsetDateTime.now();
        this.updatedBy = actor;
    }

    public boolean isPayable() {
        return status == Status.APPROVED || status == Status.PAID;
    }

    public Long getId() { return id; }
    public String getClaimNumber() { return claimNumber; }
    public Policy getPolicy() { return policy; }
    public LocalDate getServiceDate() { return serviceDate; }
    public String getProviderName() { return providerName; }
    public BigDecimal getAmount() { return amount; }
    public Status getStatus() { return status; }
    public boolean isReconsiderationRequested() { return reconsiderationRequested; }
}
