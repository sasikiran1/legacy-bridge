package dev.legacybridge.portal.domain;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public final class Repositories {

    private Repositories() {
    }

    public interface MemberRepository extends JpaRepository<Member, Long> {

        Optional<Member> findByMemberNumber(String memberNumber);

        List<Member> findByLastNameIgnoreCaseAndDateOfBirth(String lastName, LocalDate dateOfBirth);

        List<Member> findByLastNameIgnoreCaseOrderByFirstName(String lastName);

        @Query("""
                select m from Member m
                where m.memberNumber = upper(:q)
                   or lower(m.lastName) like lower(concat(:q, '%'))
                   or lower(concat(m.firstName, ' ', m.lastName)) like lower(concat('%', :q, '%'))
                order by m.lastName, m.firstName
                """)
        List<Member> search(@Param("q") String query, Limit limit);
    }

    public interface PolicyRepository extends JpaRepository<Policy, Long> {

        @EntityGraph(attributePaths = "member")
        Optional<Policy> findByPolicyNumber(String policyNumber);

        @EntityGraph(attributePaths = "member")
        List<Policy> findByMemberOrderByProduct(Member member);
    }

    public interface ClaimRepository extends JpaRepository<Claim, Long> {

        @EntityGraph(attributePaths = {"policy", "policy.member"})
        Optional<Claim> findByClaimNumber(String claimNumber);

        @EntityGraph(attributePaths = {"policy", "policy.member"})
        List<Claim> findByPolicyMemberOrderByServiceDateDesc(Member member);
    }

    public interface MemberNoteRepository extends JpaRepository<MemberNote, Long> {

        List<MemberNote> findByMemberAndDeletedAtIsNullOrderByCreatedAtDesc(Member member);
    }

    public interface ReimbursementRepository extends JpaRepository<Reimbursement, Long> {

        boolean existsByReference(String reference);
    }

    public interface PortalUserRepository extends JpaRepository<PortalUser, Long> {

        Optional<PortalUser> findByUsername(String username);
    }
}
