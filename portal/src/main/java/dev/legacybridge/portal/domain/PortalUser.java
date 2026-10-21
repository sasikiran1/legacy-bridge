package dev.legacybridge.portal.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "portal_user")
public class PortalUser {

    @Id
    private Long id;

    private String username;
    private String displayName;
    private String role;
    private String passwordHash;

    protected PortalUser() {
    }

    public void setPasswordHash(String passwordHash) {
        this.passwordHash = passwordHash;
    }

    public String getUsername() { return username; }
    public String getDisplayName() { return displayName; }
    public String getRole() { return role; }
    public String getPasswordHash() { return passwordHash; }
}
