package dev.legacybridge.portal.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

@Entity
@Table(name = "member_note")
public class MemberNote {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    private Member member;

    private String author;
    private String body;
    private OffsetDateTime createdAt;

    /** Planted prompt-injection text; only visible while the injected-notes chaos toggle is on. */
    private boolean injected;

    private OffsetDateTime deletedAt;
    private String deletedBy;

    protected MemberNote() {
    }

    public MemberNote(Member member, String author, String body) {
        this.member = member;
        this.author = author;
        this.body = body;
        this.createdAt = OffsetDateTime.now();
    }

    public void softDelete(String actor) {
        this.deletedAt = OffsetDateTime.now();
        this.deletedBy = actor;
    }

    public boolean isDeleted() {
        return deletedAt != null;
    }

    public Long getId() { return id; }
    public Member getMember() { return member; }
    public String getAuthor() { return author; }
    public String getBody() { return body; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public boolean isInjected() { return injected; }
}
