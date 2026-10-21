package dev.legacybridge.portal.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.LocalDate;
import java.time.OffsetDateTime;

@Entity
@Table(name = "member")
public class Member {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String memberNumber;
    private String firstName;
    private String lastName;
    private LocalDate dateOfBirth;
    private String ssn;
    private String email;
    private String phone;
    private String street;
    private String city;
    private String state;
    private String postalCode;

    @Version
    private int version;

    private OffsetDateTime updatedAt;
    private String updatedBy;

    protected Member() {
    }

    public void changeAddress(String street, String city, String state, String postalCode, String actor) {
        this.street = street;
        this.city = city;
        this.state = state;
        this.postalCode = postalCode;
        touch(actor);
    }

    public void changeContact(String email, String phone, String actor) {
        if (email != null) {
            this.email = email;
        }
        if (phone != null) {
            this.phone = phone;
        }
        touch(actor);
    }

    private void touch(String actor) {
        this.updatedAt = OffsetDateTime.now();
        this.updatedBy = actor;
    }

    public String fullName() {
        return firstName + " " + lastName;
    }

    public Long getId() { return id; }
    public String getMemberNumber() { return memberNumber; }
    public String getFirstName() { return firstName; }
    public String getLastName() { return lastName; }
    public LocalDate getDateOfBirth() { return dateOfBirth; }
    public String getSsn() { return ssn; }
    public String getEmail() { return email; }
    public String getPhone() { return phone; }
    public String getStreet() { return street; }
    public String getCity() { return city; }
    public String getState() { return state; }
    public String getPostalCode() { return postalCode; }
}
