package dev.legacybridge.portal;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.net.CookieManager;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

@Testcontainers
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"portal.admin-token=test-admin-token", "portal.test-user-password=test-password"})
class PortalIntegrationTest {

    private static final String ADMIN_TOKEN = "test-admin-token";
    private static final String CLERK = basic("clerk01", "test-password");

    @Container
    @ServiceConnection
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:18.6");

    @Autowired
    Environment environment;

    @Autowired
    JdbcTemplate jdbc;

    private HttpClient http;
    private String memberNumber;

    @BeforeEach
    void setUp() throws Exception {
        http = HttpClient.newBuilder().cookieHandler(new CookieManager()).build();
        assertThat(admin("PUT", "/admin/chaos", "{}").statusCode()).isEqualTo(200);
        assertThat(admin("POST", "/admin/reset", "").statusCode()).isEqualTo(204);
        memberNumber = jdbc.queryForObject("SELECT member_number FROM member WHERE id = 1", String.class);
    }

    @Test
    void publicApiServesWorkingOperationsAndRejectsUnfinishedOnes() throws Exception {
        var member = send("GET", "/api/v1/members/" + memberNumber, null, CLERK);
        assertThat(member.statusCode()).isEqualTo(200);
        assertThat(member.body()).contains("\"memberNumber\":\"" + memberNumber + "\"", "\"postalCode\"");

        assertThat(send("GET", "/api/v1/members/" + memberNumber, null, null).statusCode()).isEqualTo(401);
        assertThat(send("PATCH", "/api/v1/members/" + memberNumber + "/contact", "{\"phone\":\"(212) 555-0100\"}",
                CLERK).statusCode()).isEqualTo(501);
        assertThat(send("POST", "/api/v1/reimbursements", "{}", CLERK).statusCode()).isEqualTo(404);

        var retired = send("DELETE", "/api/v1/notes/1", null, CLERK);
        assertThat(retired.statusCode()).isEqualTo(410);
        assertThat(retired.headers().firstValue("Deprecation")).isPresent();
    }

    @Test
    void chaosRemovesEndpointsAndDriftsSchemas() throws Exception {
        admin("PUT", "/admin/chaos", "{\"api\":{\"getMember\":\"DRIFT\",\"updateAddress\":\"NOT_FOUND\"}}");

        var drifted = send("GET", "/api/v1/members/" + memberNumber, null, CLERK);
        assertThat(drifted.statusCode()).isEqualTo(200);
        assertThat(drifted.body()).contains("\"member_no\"", "\"mailing_address\"").doesNotContain("memberNumber");

        var address = "{\"street\":\"1 Main St\",\"city\":\"Albany\",\"state\":\"NY\",\"postalCode\":\"12207\"}";
        assertThat(send("PUT", "/api/v1/members/" + memberNumber + "/address", address, CLERK).statusCode())
                .isEqualTo(404);

        admin("PUT", "/admin/chaos", "{\"api\":{\"updateAddress\":\"DRIFT\"}}");
        var rejected = send("PUT", "/api/v1/members/" + memberNumber + "/address", address, CLERK);
        assertThat(rejected.statusCode()).isEqualTo(400);
        assertThat(rejected.body()).contains("Unrecognized field 'street'");

        var drift = "{\"line1\":\"1 Main St\",\"town\":\"Albany\",\"region\":\"NY\",\"zip\":\"12207\"}";
        assertThat(send("PUT", "/api/v1/members/" + memberNumber + "/address", drift, CLERK).statusCode())
                .isEqualTo(200);
        assertThat(jdbc.queryForObject("SELECT city FROM member WHERE id = 1", String.class)).isEqualTo("Albany");
    }

    @Test
    void unknownOperationInChaosConfigIsRejected() throws Exception {
        var response = admin("PUT", "/admin/chaos", "{\"api\":{\"getMembr\":\"GONE\"}}");
        assertThat(response.statusCode()).isEqualTo(400);
        assertThat(response.body()).contains("getMembr");
    }

    @Test
    void sessionsExpireOnDemandAndTheWallNeedsAHuman() throws Exception {
        var credentials = "{\"username\":\"clerk01\",\"password\":\"test-password\"}";
        assertThat(send("POST", "/internal/session", credentials, null).statusCode()).isEqualTo(200);
        assertThat(send("GET", "/internal/members/" + memberNumber, null, null).statusCode()).isEqualTo(200);

        admin("POST", "/admin/sessions/expire", "");
        var expired = send("GET", "/internal/members/" + memberNumber, null, null);
        assertThat(expired.statusCode()).isEqualTo(401);
        assertThat(expired.body()).contains("session_expired");

        admin("PUT", "/admin/chaos", "{\"wall\":\"CAPTCHA\"}");
        var walled = send("POST", "/internal/session", credentials, null);
        assertThat(walled.statusCode()).isEqualTo(403);
        assertThat(walled.body()).contains("CAPTCHA");

        admin("POST", "/admin/wall/clear", "{\"username\":\"clerk01\"}");
        assertThat(send("POST", "/internal/session", credentials, null).statusCode()).isEqualTo(200);
    }

    @Test
    void internalEndpointsKeepWorkingWhileThePublicApiIsDown() throws Exception {
        admin("PUT", "/admin/chaos", "{\"api\":{\"submitReimbursement\":\"NOT_FOUND\",\"getMember\":\"SERVER_ERROR\"}}");
        send("POST", "/internal/session", "{\"username\":\"clerk02\",\"password\":\"test-password\"}", null);

        var claim = jdbc.queryForMap("""
                SELECT c.claim_number, c.amount, m.member_number, m.first_name || ' ' || m.last_name AS name
                FROM claim c JOIN policy p ON p.id = c.policy_id JOIN member m ON m.id = p.member_id
                WHERE c.status = 'APPROVED' ORDER BY c.id LIMIT 1""");
        var body = """
                {"memberNumber":"%s","claimNumber":"%s","amount":"10.00","payeeName":"%s",
                 "bankRouting":"021000021","bankAccount":"123456789"}""".formatted(
                claim.get("member_number"), claim.get("claim_number"), claim.get("name"));

        var created = send("POST", "/internal/reimbursements", body, null);
        assertThat(created.statusCode()).isEqualTo(201);
        assertThat(created.body()).contains("\"reference\":\"RB-");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM reimbursement WHERE submitted_by = 'clerk02'",
                Integer.class)).isEqualTo(1);

        admin("POST", "/admin/reset", "");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM reimbursement", Integer.class)).isZero();
    }

    @Test
    void injectedNotesAppearOnlyWhenToggled() throws Exception {
        var target = jdbc.queryForObject("""
                SELECT m.member_number FROM member_note n JOIN member m ON m.id = n.member_id
                WHERE n.injected ORDER BY n.id LIMIT 1""", String.class);
        var path = "/api/v1/members/" + target + "/notes";
        var injectedBody = jdbc.queryForObject("SELECT body FROM member_note WHERE injected ORDER BY id LIMIT 1",
                String.class);
        var marker = injectedBody.substring(0, 30);

        assertThat(send("GET", path, null, CLERK).body()).doesNotContain(marker);
        admin("PUT", "/admin/chaos", "{\"injectedNotes\":true}");
        assertThat(send("GET", path, null, CLERK).body()).contains(marker);
    }

    @Test
    void adminEndpointsRequireTheToken() throws Exception {
        assertThat(send("GET", "/admin/chaos", null, null).statusCode()).isEqualTo(403);
    }

    @Test
    void openApiDocumentListsOnlyThePublicApi() throws Exception {
        var doc = send("GET", "/v3/api-docs", null, null);
        assertThat(doc.statusCode()).isEqualTo(200);
        assertThat(doc.body()).contains("\"operationId\":\"submitReimbursement\"", "\"operationId\":\"getClaim\"")
                .contains("\"MemberView\"", "\"AddressBody\"", "\"ReimbursementBody\"")
                .doesNotContain("/internal/", "/admin/");
    }

    private HttpResponse<String> admin(String method, String path, String body) throws Exception {
        var request = request(method, path, body).header("X-Admin-Token", ADMIN_TOKEN).build();
        return http.send(request, HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> send(String method, String path, String body, String authorization)
            throws IOException, InterruptedException {
        var builder = request(method, path, body);
        if (authorization != null) {
            builder.header("Authorization", authorization);
        }
        return http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpRequest.Builder request(String method, String path, String body) {
        var port = environment.getRequiredProperty("local.server.port");
        var publisher = body == null || body.isEmpty()
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body);
        return HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .method(method, publisher);
    }

    private static String basic(String user, String password) {
        return "Basic " + Base64.getEncoder()
                .encodeToString((user + ":" + password).getBytes(StandardCharsets.UTF_8));
    }
}
