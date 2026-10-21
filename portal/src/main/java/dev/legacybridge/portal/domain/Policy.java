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

@Entity
@Table(name = "policy")
public class Policy {

    public enum Status { ACTIVE, LAPSED, CANCELLED }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String policyNumber;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    private Member member;

    private String product;

    @Enumerated(EnumType.STRING)
    private Status status;

    private LocalDate effectiveDate;
    private BigDecimal monthlyPremium;

    protected Policy() {
    }

    public Long getId() { return id; }
    public String getPolicyNumber() { return policyNumber; }
    public Member getMember() { return member; }
    public String getProduct() { return product; }
    public Status getStatus() { return status; }
    public LocalDate getEffectiveDate() { return effectiveDate; }
    public BigDecimal getMonthlyPremium() { return monthlyPremium; }
}
