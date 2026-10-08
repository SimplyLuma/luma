// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Messages unit tests — every builder compared byte-for-byte against a
// reference message cross-generated from the Python VoLTE stack with pinned
// tags/branches/SPIs. A divergence here is a wire-format change, i.e. a
// registration or call that the commercial IMS core may reject; these are the
// hardest pins in the project. Two deliberate post-parity divergences:
// the REGISTER Contact carries +sip.instance, the mandatory RFC 3840 audio
// tag, and the MMTEL ICSI feature tag (see ContactFeatures in
// Imsd:Messages), and
// every ICSI feature-tag value is percent-encoded (urn%3Aurn-7%3A...) — the
// TS 24.229 coding and what the stock modem sends on the wire; the Python
// stack's plain-colon form is a different string under RFC 3841 matching.
// The corrected form is required for parity, but incoming-call delivery is a
// separate physical gate and must not be inferred from REGISTER acceptance.
import std;
import Imsd;

using namespace imsd::msg;

namespace {
    int Failures = 0;
    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }
    void Eq(std::string_view got, std::string_view want, std::string_view msg) {
        if (got != want) {
            std::println(std::cerr, "FAIL: {}\n--- got ---\n{}\n--- want ---\n{}", msg, got, want);
            ++Failures;
        }
    }

    Context MakeCtx() {
        Context c;
        c.id = imsd::aka::MakeIdentity("001010123456789", "001", "01");
        c.local = "2001:db8::db43";
        c.route = "<sip:[2001:db8::105]:6000;lr>";
        c.ppi = "tel:+31611111111";
        c.securityServer =
            "ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; "
            "spi-c=86438593; spi-s=86438592; port-c=33421; port-s=6000";
        c.instanceId = "urn:gsma:imei:35999999-000000-1";
        c.panInfo = "3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=0010112340ABCDE0";
        return c;
    }

    constexpr std::string_view Reg1 =
        "REGISTER sip:ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
        "Via: SIP/2.0/UDP [2001:db8::db43]:5060;branch=z9hG4bKREGBRANCH123456;rport\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=FTAG5678\r\n"
        "To: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        "Call-ID: REGCALLID@2001:db8::db43\r\n"
        "CSeq: 1 REGISTER\r\n"
        "Contact: <sip:001010123456789@[2001:db8::db43]:5060>;+sip.instance=\"<urn:gsma:imei:35999999-000000-1>\";audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\";expires=600000\r\n"
        "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, NOTIFY, PRACK, UPDATE, MESSAGE, REFER\r\n"
        "Authorization: Digest username=\"001010123456789@ims.mnc001.mcc001.3gppnetwork.org\", realm=\"ims.mnc001.mcc001.3gppnetwork.org\", uri=\"sip:ims.mnc001.mcc001.3gppnetwork.org\", nonce=\"\", response=\"\"\r\n"
        "Require: sec-agree\r\n"
        "Proxy-Require: sec-agree\r\n"
        "Security-Client: ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=7449812; spi-s=176466735; port-c=45061; port-s=45062\r\n"
        "Supported: sec-agree, path\r\n"
        "Expires: 600000\r\n"
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=0010112340ABCDE0\r\n"
        "Content-Length: 0\r\n"
        "\r\n";

    constexpr std::string_view Reg2 =
        "REGISTER sip:ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
        "Via: SIP/2.0/TCP [2001:db8::db43]:45062;branch=z9hG4bKREGBRANCH123456;rport\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=FTAG5678\r\n"
        "To: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        "Call-ID: REGCALLID@2001:db8::db43\r\n"
        "CSeq: 2 REGISTER\r\n"
        "Contact: <sip:001010123456789@[2001:db8::db43]:45062;transport=tcp>;+sip.instance=\"<urn:gsma:imei:35999999-000000-1>\";audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\";expires=600000\r\n"
        "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, NOTIFY, PRACK, UPDATE, MESSAGE, REFER\r\n"
        "Authorization: Digest username=\"001010123456789@ims.mnc001.mcc001.3gppnetwork.org\", realm=\"ims.mnc001.mcc001.3gppnetwork.org\", uri=\"sip:ims.mnc001.mcc001.3gppnetwork.org\", nonce=\"NONCEXYZ\", qop=auth, nc=00000001, cnonce=\"CNONCE0123456789\", response=\"RESP0000\", algorithm=AKAv1-MD5\r\n"
        "Require: sec-agree\r\n"
        "Proxy-Require: sec-agree\r\n"
        "Security-Client: ipsec-3gpp; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=7449812; spi-s=176466735; port-c=45061; port-s=45062\r\n"
        "Security-Verify: ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=86438593; spi-s=86438592; port-c=33421; port-s=6000\r\n"
        "Supported: sec-agree, path\r\n"
        "Expires: 600000\r\n"
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=0010112340ABCDE0\r\n"
        "Content-Length: 0\r\n"
        "\r\n";

    constexpr std::string_view Sdp =
        "v=0\r\n"
        "o=- 7777777 7777777 IN IP6 2001:db8::db43\r\n"
        "s=-\r\n"
        "c=IN IP6 2001:db8::db43\r\n"
        "t=0 0\r\n"
        "m=audio 50004 RTP/AVP 97 98\r\n"
        "b=AS:41\r\n"
        "b=RS:512\r\n"
        "b=RR:1536\r\n"
        "a=rtpmap:97 AMR-WB/16000/1\r\n"
        "a=fmtp:97 octet-align=1;mode-change-capability=2;max-red=0\r\n"
        "a=rtpmap:98 telephone-event/16000\r\n"
        "a=fmtp:98 0-15\r\n"
        "a=ptime:20\r\n"
        "a=maxptime:240\r\n"
        "a=sendrecv\r\n";

    constexpr std::string_view Invite =
        "INVITE sip:1233;phone-context=ims.mnc001.mcc001.3gppnetwork.org@ims.mnc001.mcc001.3gppnetwork.org;user=phone SIP/2.0\r\n"
        "Via: SIP/2.0/TCP [2001:db8::db43]:45062;branch=z9hG4bKINVBRANCH0000000000;rport\r\n"
        "Max-Forwards: 70\r\n"
        "Route: <sip:[2001:db8::105]:6000;lr>\r\n"
        "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=ITAG123456\r\n"
        "To: <sip:1233;phone-context=ims.mnc001.mcc001.3gppnetwork.org@ims.mnc001.mcc001.3gppnetwork.org;user=phone>\r\n"
        "Call-ID: CALLID789@2001:db8::db43\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:001010123456789@[2001:db8::db43]:45062>;audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"\r\n"
        "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, PRACK, UPDATE, MESSAGE, REFER\r\n"
        "Supported: 100rel, timer\r\n"
        "Accept-Contact: *;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"\r\n"
        "P-Preferred-Service: urn:urn-7:3gpp-service.ims.icsi.mmtel\r\n"
        "P-Preferred-Identity: <tel:+31611111111>\r\n"
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=0010112340ABCDE0\r\n"
        "Security-Verify: ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=86438593; spi-s=86438592; port-c=33421; port-s=6000\r\n"
        "Proxy-Require: sec-agree\r\n"
        "Require: sec-agree\r\n"
        "Session-Expires: 1800;refresher=uac\r\n"
        "Min-SE: 90\r\n"
        "Content-Type: application/sdp\r\n"
        "Content-Length: 323\r\n"
        "\r\n"
        "v=0\r\n"
        "o=- 7777777 7777777 IN IP6 2001:db8::db43\r\n"
        "s=-\r\n"
        "c=IN IP6 2001:db8::db43\r\n"
        "t=0 0\r\n"
        "m=audio 50004 RTP/AVP 97 98\r\n"
        "b=AS:41\r\n"
        "b=RS:512\r\n"
        "b=RR:1536\r\n"
        "a=rtpmap:97 AMR-WB/16000/1\r\n"
        "a=fmtp:97 octet-align=1;mode-change-capability=2;max-red=0\r\n"
        "a=rtpmap:98 telephone-event/16000\r\n"
        "a=fmtp:98 0-15\r\n"
        "a=ptime:20\r\n"
        "a=maxptime:240\r\n"
        "a=sendrecv\r\n";

    constexpr std::string_view Subscribe =
        "SUBSCRIBE sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
        "Via: SIP/2.0/TCP [2001:db8::db43]:45062;branch=z9hG4bKSUBBRANCH0000000000;rport\r\n"
        "Max-Forwards: 70\r\n"
        "Route: <sip:[2001:db8::105]:6000;lr>\r\n"
        "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=STAG1234\r\n"
        "To: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        "Call-ID: SUBCALLID@2001:db8::db43\r\n"
        "CSeq: 1 SUBSCRIBE\r\n"
        "Contact: <sip:001010123456789@[2001:db8::db43]:45062>\r\n"
        "Event: reg\r\n"
        "Accept: application/reginfo+xml\r\n"
        "Expires: 600000\r\n"
        "P-Preferred-Identity: <tel:+31611111111>\r\n"
        "P-Access-Network-Info: 3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=0010112340ABCDE0\r\n"
        "Security-Verify: ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=86438593; spi-s=86438592; port-c=33421; port-s=6000\r\n"
        "Proxy-Require: sec-agree\r\n"
        "Require: sec-agree\r\n"
        "Content-Length: 0\r\n"
        "\r\n";
}

int main() {
    Context c = MakeCtx();

    // ---- REGISTER (initial + protected), byte-exact vs Python
    Eq(BuildRegisterInitial(c, "REGCALLID@2001:db8::db43", "FTAG5678",
                            "REGBRANCH123456", 7449812u, 176466735u),
       Reg1, "unprotected REGISTER");
    {
        std::string auth = AuthAka(c, "NONCEXYZ", "CNONCE0123456789", "RESP0000");
        Eq(BuildRegisterProtected(c, "REGCALLID@2001:db8::db43", "FTAG5678",
                                  "REGBRANCH123456", 2, 7449812u, 176466735u, auth),
           Reg2, "protected REGISTER");
    }

    // ---- RuriFor variants
    Eq(RuriFor("1233", c.id.domain), "sip:1233;phone-context=ims.mnc001.mcc001.3gppnetwork.org@ims.mnc001.mcc001.3gppnetwork.org;user=phone", "ruri short code");
    Eq(RuriFor("+31612345678", c.id.domain), "sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org;user=phone", "ruri E.164");
    Eq(RuriFor("0612345678", c.id.domain), "sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org;user=phone", "ruri Dutch national");
    Eq(RuriFor("0031612345678", c.id.domain), "sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org;user=phone", "ruri 00 intl");

    // ---- SUBSCRIBE (reg event, RFC 3680) — no Python counterpart; pinned
    // against the shape of the other protected in-flow requests
    Eq(BuildSubscribeReg(c, "SUBCALLID@2001:db8::db43", "STAG1234", "SUBBRANCH0000000000"), Subscribe, "reg-event SUBSCRIBE (no aor: temporary IMPU fallback)");
    {
        Context c2 = MakeCtx();
        c2.aor = "sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org";
        std::string sub = BuildSubscribeReg(c2, "SUBCALLID@2001:db8::db43", "STAG1234", "SUBBRANCH0000000000");
        Check(sub.starts_with("SUBSCRIBE sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"), "reg-event SUBSCRIBE aor R-URI");
        Check(sub.contains("\r\nFrom: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org>;tag=STAG1234\r\n"), "reg-event SUBSCRIBE aor From");
        Check(sub.contains("\r\nTo: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org>\r\n"), "reg-event SUBSCRIBE aor To");
    }
    {
        // no identity learned yet: P-Preferred-Identity is omitted, not "<>"
        Context c3 = MakeCtx();
        c3.ppi.clear();
        Check(!BuildSubscribeReg(c3, "SUBCALLID@2001:db8::db43", "STAG1234", "SUBBRANCH0000000000") .contains("P-Preferred-Identity"), "empty ppi omits P-Preferred-Identity in SUBSCRIBE");
        Dialog d;
        d.ruri = RuriFor("1233", c3.id.domain);
        d.callid = "CID@x"; d.itag = "it"; d.invBranch = "ib";
        Check(!BuildInvite(c3, d, "v=0\r\n", false).contains("P-Preferred-Identity"), "empty ppi omits P-Preferred-Identity in INVITE");
    }

    // ---- INVITE, byte-exact vs Python
    {
        Dialog d;
        d.ruri = RuriFor("1233", c.id.domain);
        d.callid = "CALLID789@2001:db8::db43";
        d.itag = "ITAG123456";
        d.invBranch = "INVBRANCH0000000000";
        Eq(BuildInvite(c, d, Sdp, /*precond=*/false), Invite, "INVITE + AMR-WB SDP offer");
    }

    // ---- emergency INVITE: byte-identical to the plain INVITE except the
    // Request-URI and To carry the service URN (RFC 5031 / TS 24.229 5.1.6)
    {
        Dialog d;
        d.ruri = std::string(SosUrn);
        d.callid = "CALLID789@2001:db8::db43";
        d.itag = "ITAG123456";
        d.invBranch = "INVBRANCH0000000000";
        std::string want(Invite);
        auto swap = [&](std::string_view from, std::string_view to) {
            std::size_t at = want.find(from);
            Check(at != std::string::npos, "sos pin template line present");
            if (at != std::string::npos) want.replace(at, from.size(), to);
        };
        swap("INVITE sip:1233;phone-context=ims.mnc001.mcc001.3gppnetwork.org@ims.mnc001.mcc001.3gppnetwork.org;user=phone SIP/2.0\r\n",
             "INVITE urn:service:sos SIP/2.0\r\n");
        swap("To: <sip:1233;phone-context=ims.mnc001.mcc001.3gppnetwork.org@ims.mnc001.mcc001.3gppnetwork.org;user=phone>\r\n",
             "To: <urn:service:sos>\r\n");
        swap("P-Preferred-Service: urn:urn-7:3gpp-service.ims.icsi.mmtel\r\n", "");
        Eq(BuildInvite(c, d, Sdp, /*precond=*/false), want, "emergency INVITE (urn:service:sos)");
    }

    // ---- IsEmergency classification
    Check(IsEmergency("112"), "112 is emergency");
    Check(IsEmergency("911"), "911 is emergency");
    Check(IsEmergency("1 1 2"), "space separators stripped");
    Check(IsEmergency("1-1-2"), "dash separators stripped");
    Check(!IsEmergency("0112"), "prefixed 112 is not emergency");
    Check(!IsEmergency("1120"), "suffixed 112 is not emergency");
    Check(!IsEmergency("+31612345678"), "E.164 number is not emergency");
    Check(!IsEmergency(""), "empty string is not emergency");
    Check(!IsEmergency("199"), "extra number not classified by default");
    {
        std::vector<std::string> extra = {"199", "088"};
        Check(IsEmergency("199", extra), "EMERGENCY_NUMBERS extra matches");
        Check(IsEmergency("088", extra), "second extra matches");
        Check(!IsEmergency("19", extra), "extra requires exact match");
    }

    // ---- in-dialog PRACK/BYE + ACK + CANCEL + 200 (structural checks)
    {
        Dialog d;
        d.ruri = RuriFor("1233", c.id.domain);
        d.callid = "CID@x";
        d.itag = "it";
        d.invBranch = "ib";
        d.dialogTo = "<sip:1233@x>;tag=remote99";
        d.remoteTarget = "sip:termgw@2001:db8::9";
        d.recordRoute = {"<sip:rr1;lr>", "<sip:rr2;lr>"};

        // dialog route = reversed record-route
        Check(DialogRoute(c, d) == "<sip:rr2;lr>, <sip:rr1;lr>", "dialog route reversed");

        std::array<std::string, 1> rack = {"RAck: 42 1 INVITE"};
        std::string prack = BuildInDialog(c, d, "PRACK", 2, "prbranch", rack);
        Check(prack.starts_with("PRACK sip:termgw@2001:db8::9 SIP/2.0\r\n"), "PRACK request-URI = remote target");
        Check(prack.contains("Route: <sip:rr2;lr>, <sip:rr1;lr>\r\n"), "PRACK dialog route");
        Check(prack.contains("To: <sip:1233@x>;tag=remote99\r\n"), "PRACK To has remote tag");
        Check(prack.contains("CSeq: 2 PRACK\r\n"), "PRACK CSeq");
        Check(prack.contains("RAck: 42 1 INVITE\r\n"), "PRACK RAck");
        Check(prack.ends_with("Content-Length: 0\r\n\r\n"), "PRACK empty body");

        std::string bye = BuildInDialog(c, d, "BYE", 3, "byebranch");
        Check(bye.starts_with("BYE sip:termgw@2001:db8::9 SIP/2.0\r\n"), "BYE line");
        Check(bye.contains("CSeq: 3 BYE\r\n"), "BYE CSeq");

        std::string ack = BuildAck2xx(c, d, "ackbranch", "<ignored>");
        Check(ack.starts_with("ACK sip:termgw@2001:db8::9 SIP/2.0\r\n"), "ACK 2xx target");
        Check(ack.contains("To: <sip:1233@x>;tag=remote99\r\n"), "ACK 2xx dialog To");
        Check(ack.contains("CSeq: 1 ACK\r\n"), "ACK CSeq 1");

        std::string cancel = BuildCancel(c, d);
        Check(cancel.starts_with(std::format("CANCEL {} SIP/2.0\r\n", d.ruri)), "CANCEL request-URI = ruri");
        Check(cancel.contains("branch=z9hG4bKib;rport\r\n"), "CANCEL reuses INVITE branch");
        Check(cancel.contains("CSeq: 1 CANCEL\r\n"), "CANCEL CSeq copies INVITE");
    }
    {
        // non-2xx ACK uses the INVITE branch + registration route + resp To
        Dialog d;
        d.ruri = "sip:x@y"; d.callid = "CID"; d.itag = "it"; d.invBranch = "ib9";
        std::string ack = BuildAckNon2xx(c, d, "<sip:x@y>;tag=z");
        Check(ack.contains("branch=z9hG4bKib9;rport\r\n"), "non-2xx ACK INVITE branch");
        Check(ack.contains(std::format("Route: {}\r\n", c.route)), "non-2xx ACK registration route");
        Check(ack.contains("To: <sip:x@y>;tag=z\r\n"), "non-2xx ACK echoes response To");
    }

    // ---- 200 OK response echoes the request's dialog headers, in order
    {
        std::string req =
            "BYE sip:me@here SIP/2.0\r\n"
            "Via: SIP/2.0/TCP [a::1]:45061;branch=z9hG4bKother\r\n"
            "From: <sip:them@far>;tag=T\r\n"
            "To: <sip:me@here>;tag=M\r\n"
            "Call-ID: xyz@far\r\n"
            "CSeq: 7 BYE\r\n"
            "Content-Length: 0\r\n\r\n";
        std::string r = BuildResponse200(c, req);
        Check(r.starts_with("SIP/2.0 200 OK\r\n"), "200 status line");
        Check(r.contains("Via: SIP/2.0/TCP [a::1]:45061;branch=z9hG4bKother\r\n"), "200 echoes Via");
        Check(r.contains("Call-ID: xyz@far\r\n"), "200 echoes Call-ID");
        Check(r.contains("CSeq: 7 BYE\r\n"), "200 echoes CSeq");
        Check(r.ends_with("Content-Length: 0\r\n\r\n"), "200 empty body");
        // with an SDP answer: our Contact + application/sdp appear
        std::string r2 = BuildResponse200(c, req, Sdp);
        Check(r2.contains("Content-Type: application/sdp\r\n"), "200 w/ SDP content-type");
        Check(r2.contains("Contact: <sip:001010123456789@[2001:db8::db43]:45062>;audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"\r\n"), "200 w/ SDP includes IR.92 voice Contact");
        Check(r2.ends_with(Sdp), "200 w/ SDP body");
    }

    // ---- s53 oracle diff: UUID Contact user-part + User-Agent ------------
    {
        Context c = MakeCtx();
        c.contactUser = "029dd36c-857c-4270-ad7f-0ae70d31d50f";
        c.userAgent = "Fairphone_Fairphone 6_FP6.QREL.16.82.0";
        std::string r1 = BuildRegisterInitial(c, "cid@h", "ft", "br", 1, 2);
        Check(r1.contains("Contact: <sip:029dd36c-857c-4270-ad7f-0ae70d31d50f@" "[2001:db8::db43]:5060>"), "reg1 Contact carries contactUser, not the IMSI");
        Check(!r1.contains("sip:001010123456789@[2001:db8::db43]"), "reg1 has no IMSI-user Contact when contactUser set");
        Check(r1.contains("From: <sip:001010123456789@ims.mnc001.mcc001"), "reg1 From/AOR stays the IMSI IMPU");
        Check(r1.contains("User-Agent: Fairphone_Fairphone 6_FP6.QREL.16.82.0\r\n"), "reg1 User-Agent emitted");
        std::string r2 = BuildRegisterProtected(c, "cid@h", "ft", "br", 2, 1, 2, "Authorization: x");
        Check(r2.contains("Contact: <sip:029dd36c-857c-4270-ad7f-0ae70d31d50f@" "[2001:db8::db43]:45062;transport=tcp>"), "reg2 Contact carries contactUser and explicit TCP at the protected port");
        Check(r1.contains(";audio;+g.3gpp.icsi-ref="), "reg1 Contact advertises IR.92 audio before MMTEL");
        Check(r2.contains(";audio;+g.3gpp.icsi-ref="), "reg2 Contact advertises IR.92 audio before MMTEL");
        Check(r2.contains("User-Agent: Fairphone_Fairphone 6_FP6.QREL.16.82.0\r\n"), "reg2 User-Agent emitted");
        c.outboundFlow = true;
        r1 = BuildRegisterInitial(c, "cid@h", "ft", "br", 1, 2);
        r2 = BuildRegisterProtected(c, "cid@h", "ft", "br", 2, 1, 2, "Authorization: x");
        Check(r1.contains(";+sip.instance=\"<urn:gsma:imei:35999999-000000-1>\";reg-id=1"), "outbound reg1 Contact carries instance and reg-id");
        Check(r2.contains(";+sip.instance=\"<urn:gsma:imei:35999999-000000-1>\";reg-id=1"), "outbound reg2 Contact carries instance and reg-id");
        Check(r1.contains("Supported: sec-agree, path, outbound\r\n"), "outbound reg1 advertises option tag");
        Check(r2.contains("Supported: sec-agree, path, outbound\r\n"), "outbound reg2 advertises option tag");
        Check(r2.contains(";transport=tcp>;+sip.instance="), "protected Contact transport is explicit before feature parameters");
        c.userAgent.clear();
        Check(!BuildRegisterInitial(c, "cid@h", "ft", "br", 1, 2) .contains("User-Agent:"), "empty userAgent omits the header");
    }

    if (Failures == 0) std::println("Messages: all tests passed");
    return Failures;
}
