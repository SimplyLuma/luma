// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Messages — the SIP request/response text the engine puts on the wire.

Every VoLTE message the daemon sends is assembled here from an explicit
context and the per-request variables (tags, branches, CSeq) so the builders
are pure functions with no hidden state: the engine owns the dialog bookkeeping
and the transport owns the socket, this module only formats bytes. That makes
each message byte-pinnable — tests/Messages compares the output against strings
cross-generated from the Python reference stack, header for header.

Covers: the unprotected + protected REGISTER and the refresh re-REGISTER
(sec-agree, Security-Client/Verify, AKAv1-MD5 Authorization); the INVITE with
an AMR-WB SDP offer and IMS MMTEL feature tags; the in-dialog PRACK/BYE and
2xx/non-2xx ACK; CANCEL (reusing the INVITE branch); the 200 OK that echoes
an inbound request's dialog headers; and the UAS responses to an inbound
INVITE (100/180/200 and rejection finals). Pure std C++.
*/

export module Imsd:Messages;
import std;
import :Util;
import :Aka;
import :Sip;

export namespace imsd::msg {

    // Everything the builders need that is stable for the life of a
    // registration. Ports default to the fixed protected values; route/ppi/
    // securityServer are filled in from the REGISTER 200 OK.
    struct Context {
        imsd::aka::Identity id;
        std::string local;
        int portUc = imsd::util::PortUc;
        int portUs = imsd::util::PortUs;
        int initPort = 5060;
        std::string ealgOffer = "aes-cbc";
        // RFC 7254 IMEI URN for +sip.instance (e.g. urn:gsma:imei:35999999-
        // 000000-1); empty omits the parameter.
        std::string instanceId;
        // RFC 5626 flow affinity.  When enabled, REGISTER identifies this
        // specific transport flow with the instance/reg-id pair and advertises
        // the outbound option tag. Keep it configurable: not every carrier
        // core implements SIP outbound. The FP6 T-Mobile registration accepts
        // this shape, but terminating-call delivery remains a separate
        // physical gate and must not be inferred from REGISTER success.
        bool outboundFlow = false;
        // User-part of the registered-binding Contact URI. The stock modem
        // registers a fresh random UUID here, NOT the IMSI (oracle capture
        // 2026-07-21, journal/ims.md s53) — one of the residual diffs from our
        // earlier REGISTER while inbound attempts were still diverted to
        // voicemail without an INVITE reaching Luma. Empty falls back to the
        // IMSI: the pre-oracle shape, which old state files resumed from and
        // the byte-pinned tests encode.
        std::string contactUser;
        // User-Agent header value for REGISTER; the stock modem sends its
        // build string (the other residual oracle diff). Empty omits.
        std::string userAgent;
        // From the successful REGISTER:
        std::string route;            // Route header value (Service-Route or fallback)
        std::string ppi;              // P-Preferred-Identity value
        std::string aor;              // default public identity (P-Associated-URI[0],
                                      // sip: entry preferred) — the identity for
                                      // self-targeted requests like reg-event
                                      // SUBSCRIBE; the temporary (IMSI-derived)
                                      // IMPU is barred for anything but REGISTER
        std::string securityServer;   // Security-Verify value (the network's ss)
        // P-Access-Network-Info value (imsd::aka::ParsePani from live mmcli
        // location: real MCC/MNC/TAC/ECI, FDD). Empty omits the header —
        // never fabricate one: the network reads it to learn which access
        // the UE is reachable on (stock sends it in every REGISTER/INVITE).
        std::string panInfo;
    };

    // A Via line: "Via: SIP/2.0/<transport> [local]:<port>;branch=z9hG4bK<b>;rport".
    //
    // Port rule (TS 24.229 5.1.1.4 / 33.203): every PROTECTED request carries
    // the protected SERVER port (portUs) in Via and Contact — that is the
    // port the network delivers terminating requests to (MT INVITE, NOTIFY,
    // in-dialog requests toward us). The protected CLIENT port (portUc) is
    // only the source port of the TCP connection we open; it never appears
    // in headers. Getting this wrong is invisible on the originating path
    // (TCP responses ride our own connection regardless), while it leaves no
    // standards-correct server-flow target for terminating requests. The
    // stock modem puts port_us in both Via and Contact. Correcting this is a
    // prerequisite; it is not, by itself, evidence that an MT call passed.
    inline std::string ViaLine(const Context& c, std::string_view transport, int port, std::string_view branch) {
        return std::format("Via: SIP/2.0/{} {};branch=z9hG4bK{};rport", transport, imsd::util::HostPort(c.local, port), branch);
    }

    // Security-Client value carrying our in-use client SPIs + protected ports.
    inline std::string SecurityClient(const Context& c, std::uint32_t spiUc, std::uint32_t spiUs) {
        return std::format("Security-Client: ipsec-3gpp; alg=hmac-sha-1-96; ealg={}; " "spi-c={}; spi-s={}; port-c={}; port-s={}", c.ealgOffer, spiUc, spiUs, c.portUc, c.portUs);
    }

    // Authorization line with an empty (unchallenged) digest — the first
    // REGISTER, before the 401 nonce is known.
    inline std::string AuthEmpty(const Context& c) {
        return std::format("Authorization: Digest username=\"{}\", realm=\"{}\", uri=\"{}\", " "nonce=\"\", response=\"\"", c.id.impi, c.id.domain, c.id.regUri);
    }

    // Authorization line with a computed AKAv1-MD5 response.
    inline std::string AuthAka(const Context& c, std::string_view nonce, std::string_view cnonce, std::string_view response) {
        return std::format(
            "Authorization: Digest username=\"{}\", realm=\"{}\", uri=\"{}\", "
            "nonce=\"{}\", qop=auth, nc=00000001, cnonce=\"{}\", response=\"{}\", "
            "algorithm=AKAv1-MD5",
            c.id.impi, c.id.domain, c.id.regUri, nonce, cnonce, response);
    }

    // REGISTER Contact media-feature tags. GSMA IR.92 requires BOTH the
    // RFC 3840 `audio` tag and the MMTEL ICSI for a VoLTE registration. The
    // former advertises the media capability used by RFC 3841 target
    // selection; the latter advertises the IMS communication service. Luma
    // previously sent only the ICSI. That incomplete Contact could originate
    // calls but gave a terminating proxy no `audio` contact predicate to
    // select, making it a standards-backed candidate for the observed
    // voicemail diversion. The URN colons are
    // percent-encoded (TS 24.229 7.2A.8.2 feature-tag coding; RFC 3841
    // matching compares the value as a string, so the encoding must match
    // what the network selects on — the stock modem sends exactly
    // urn%3Aurn-7%3A..., diag capture 2026-07-18). +sip.instance carries the
    // IMEI URN when known. Deliberately NOT +g.3gpp.smsip: it routes
    // terminating SMS over IMS to a stack with no SMSoIP handling yet,
    // silently losing texts. Exact tag parity including smsip + nw-init-ussi
    // was tested live (2026-07-20, stored binding echo-verified), but no MT
    // INVITE reached Luma in that gate; do not infer routing success from the
    // binding echo.
    // The binding user-part every Contact we emit carries (see
    // Context::contactUser).
    inline std::string_view ContactUser(const Context& c) {
        return c.contactUser.empty() ? std::string_view(c.id.imsi)
                                     : std::string_view(c.contactUser);
    }

    inline std::string ContactFeatures(const Context& c) {
        std::string s;
        if (!c.instanceId.empty()) {
            s += std::format(";+sip.instance=\"<{}>\"", c.instanceId);
            if (c.outboundFlow) s += ";reg-id=1";
        }
        s += ";audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"";
        return s;
    }

    inline std::string RegisterSupported(const Context& c) {
        return c.outboundFlow ? "sec-agree, path, outbound"
                              : "sec-agree, path";
    }

    // The service URN an emergency INVITE targets (RFC 5031); Request-URI and
    // To both carry it (TS 24.229 5.1.6). No sos subtype: a dialled string
    // does not say police/ambulance/fire.
    inline constexpr std::string_view SosUrn = "urn:service:sos";

    // Emergency-number classification. 112 and 911 are emergency numbers on
    // every network (TS 22.101 §10.1.1), so they are compiled in; `extra`
    // adds carrier- or lab-specific numbers (the EMERGENCY_NUMBERS list —
    // e.g. a private test core's advertised short code). Exact match after
    // stripping "1 1 2"-style separators; prefixed/suffixed strings ("0112",
    // "1120") are ordinary numbers. SIM EF_ECC is not read yet.
    inline bool IsEmergency(std::string_view target, std::span<const std::string> extra = {}) {
        std::string t;
        for (char ch : target)
            if (ch != ' ' && ch != '-' && ch != '.') t.push_back(ch);
        if (t == "112" || t == "911") return true;
        for (const std::string& e : extra)
            if (!e.empty() && t == e) return true;
        return false;
    }

    // Map a dialled string to a Request-URI (imscall.ruri_for): E.164/national
    // numbers become user=phone SIP URIs at the home domain; a bare short code
    // gets phone-context.
    inline std::string RuriFor(std::string_view target, std::string_view domain) {
        std::string t;
        for (char ch : target)
            if (ch != ' ') t.push_back(ch);
        if (t.starts_with("+")) return std::format("sip:{}@{};user=phone", t, domain);
        if (t.starts_with("00")) return std::format("sip:+{}@{};user=phone", t.substr(2), domain);
        if (t.starts_with("0") && t.size() >= 9) return std::format("sip:+31{}@{};user=phone", t.substr(1), domain);
        return std::format("sip:{};phone-context={}@{};user=phone", t, domain, domain);
    }

    namespace detail {
        inline std::string Join(std::span<const std::string> lines) {
            std::string s;
            for (std::size_t i = 0; i < lines.size(); i++) {
                s += lines[i];
                s += "\r\n";
            }
            return s;
        }
    }

    // ---- REGISTER -------------------------------------------------------

    // The first, unprotected REGISTER (UDP from the init port, CSeq 1, empty
    // digest, Supported: path). Triggers the 401 that carries the AKA nonce.
    std::string BuildRegisterInitial(const Context& c, std::string_view callid, std::string_view ftag, std::string_view branch, std::uint32_t spiUc, std::uint32_t spiUs) {
        std::vector<std::string> l = {
            std::format("REGISTER {} SIP/2.0", c.id.regUri),
            ViaLine(c, "UDP", c.initPort, branch),
            "Max-Forwards: 70",
            std::format("From: <{}>;tag={}", c.id.impu, ftag),
            std::format("To: <{}>", c.id.impu),
            std::format("Call-ID: {}", callid),
            "CSeq: 1 REGISTER",
            std::format("Contact: <sip:{}@{}>{};expires=600000", ContactUser(c), imsd::util::HostPort(c.local, c.initPort), ContactFeatures(c)),
            "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, NOTIFY, PRACK, UPDATE, MESSAGE, REFER",
            AuthEmpty(c),
            "Require: sec-agree",
            "Proxy-Require: sec-agree",
            SecurityClient(c, spiUc, spiUs),
            std::format("Supported: {}", RegisterSupported(c)),
            "Expires: 600000",
        };
        if (!c.panInfo.empty()) l.push_back(std::format("P-Access-Network-Info: {}", c.panInfo));
        if (!c.userAgent.empty()) l.push_back(std::format("User-Agent: {}", c.userAgent));
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }

    // A protected REGISTER over IPsec/TCP. The Contact explicitly carries
    // transport=tcp because sip: defaults to UDP (RFC 3261 19.1.2). This is
    // not cosmetic when the registrar declines RFC 5626 outbound: TS 24.229
    // 5.1.1.2.1 then binds terminating routing to the Contact address and its
    // security associations instead of the client-initiated TCP flow. The UE
    // listens on the advertised protected server port for a new TCP flow.
    // Always advertises
    // Supported: sec-agree, path — RFC 3327: 'path' here is the UA's
    // declaration that it is reachable via the Path the P-CSCF inserted,
    // i.e. the route every terminating request (NOTIFY, MT INVITE) takes.
    // The stock oracle sends it in BOTH REGISTERs (2026-07-21 capture,
    // reg2 build at 19:30:44); an earlier reading that reg2 omits it was
    // wrong. `authLine` is a full
    // Authorization line (AuthEmpty for the refresh's first try, AuthAka
    // once challenged).
    std::string BuildRegisterProtected(const Context& c, std::string_view callid, std::string_view ftag, std::string_view branch, int cseq, std::uint32_t spiUc, std::uint32_t spiUs, std::string_view authLine) {
        std::vector<std::string> l = {
            std::format("REGISTER {} SIP/2.0", c.id.regUri),
            ViaLine(c, "TCP", c.portUs, branch),
            "Max-Forwards: 70",
            std::format("From: <{}>;tag={}", c.id.impu, ftag),
            std::format("To: <{}>", c.id.impu),
            std::format("Call-ID: {}", callid),
            std::format("CSeq: {} REGISTER", cseq),
            std::format("Contact: <sip:{}@{};transport=tcp>{};expires=600000", ContactUser(c), imsd::util::HostPort(c.local, c.portUs), ContactFeatures(c)),
            "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, NOTIFY, PRACK, UPDATE, MESSAGE, REFER",
            std::string(authLine),
            "Require: sec-agree",
            "Proxy-Require: sec-agree",
            SecurityClient(c, spiUc, spiUs),
            std::format("Security-Verify: {}", c.securityServer),
        };
        l.push_back(std::format("Supported: {}", RegisterSupported(c)));
        l.push_back("Expires: 600000");
        if (!c.panInfo.empty()) l.push_back(std::format("P-Access-Network-Info: {}", c.panInfo));
        if (!c.userAgent.empty()) l.push_back(std::format("User-Agent: {}", c.userAgent));
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }

    // SUBSCRIBE to the registration event package (RFC 3680) for our own
    // AOR — TS 24.229 5.1.1.3 requires it after registration. The immediate
    // NOTIFY carries application/reginfo+xml with every binding the S-CSCF
    // holds for the implicit registration set, feature tags as stored — the
    // network's own view of what terminating routing selects on. Same
    // sec-agree/route shape as the other protected in-flow requests; a new
    // dialog (fresh Call-ID, CSeq 1) per subscription.
    std::string BuildSubscribeReg(const Context& c, std::string_view callid, std::string_view ftag, std::string_view branch) {
        std::string_view aor = c.aor.empty() ? std::string_view(c.id.impu)
                                             : std::string_view(c.aor);
        std::vector<std::string> l = {
            std::format("SUBSCRIBE {} SIP/2.0", aor),
            ViaLine(c, "TCP", c.portUs, branch),
            "Max-Forwards: 70",
            std::format("Route: {}", c.route),
            std::format("From: <{}>;tag={}", aor, ftag),
            std::format("To: <{}>", aor),
            std::format("Call-ID: {}", callid),
            "CSeq: 1 SUBSCRIBE",
            std::format("Contact: <sip:{}@{}>", ContactUser(c), imsd::util::HostPort(c.local, c.portUs)),
            "Event: reg",
            "Accept: application/reginfo+xml",
            "Expires: 600000",
        };
        // P-Preferred-Identity is a hint (RFC 3325) — omitted when no public
        // identity has been learned yet; the network asserts one regardless.
        if (!c.ppi.empty()) l.push_back(std::format("P-Preferred-Identity: <{}>", c.ppi));
        if (!c.panInfo.empty()) l.push_back(std::format("P-Access-Network-Info: {}", c.panInfo));
        l.push_back(std::format("Security-Verify: {}", c.securityServer));
        l.push_back("Proxy-Require: sec-agree");
        l.push_back("Require: sec-agree");
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }

    // ---- call dialog ----------------------------------------------------

    // The dialog identity + routing the engine accumulates for one call.
    // For an outgoing call itag is our From tag and dialogTo is learned from
    // the responses; for an incoming call itag is the To tag we mint,
    // dialogTo is the caller's From, and both feed BuildInDialog identically
    // (our requests always put OUR tag in From and the remote's in To).
    struct Dialog {
        std::string ruri;
        std::string callid;
        std::string itag;           // our (local) dialog tag
        std::string invBranch;      // INVITE Via branch (CANCEL reuses it)
        std::optional<std::string> dialogTo;     // remote To/From incl. tag, once known
        std::vector<std::string> recordRoute;    // route set, UAC order (see DialogRoute)
        std::optional<std::string> remoteTarget; // remote Contact URI
    };

    // Dialog route set: the reversed Record-Route, or the registration Route
    // when none was recorded (imsd._dialog_route).
    inline std::string DialogRoute(const Context& c, const Dialog& d) {
        if (d.recordRoute.empty()) return c.route;
        std::string s;
        for (std::size_t i = d.recordRoute.size(); i-- > 0;) {
            s += d.recordRoute[i];
            if (i != 0) s += ", ";
        }
        return s;
    }

    // INVITE with an AMR-WB SDP offer (passed in, built by Imsd:Sdp). IR.92
    // requires the RFC 3840 audio tag alongside the MMTEL ICSI in the Contact
    // of an INVITE and its 1xx/2xx responses.
    // `sessionExpires` is the RFC 4028 Session-Expires value. A network whose
    // Min-SE exceeds it answers 422 Session Interval Too Small and names its
    // minimum; the caller retries with that value (RFC 4028 7.4).
    std::string BuildInvite(const Context& c, const Dialog& d, std::string_view sdp, bool precond, int sessionExpires = 1800) {
        std::string supported = precond ? "100rel, timer, precondition"
                                        : "100rel, timer";
        std::vector<std::string> l = {
            std::format("INVITE {} SIP/2.0", d.ruri),
            std::format("Via: SIP/2.0/TCP {};branch=z9hG4bK{};rport", imsd::util::HostPort(c.local, c.portUs), d.invBranch),
            "Max-Forwards: 70",
            std::format("Route: {}", c.route),
            std::format("From: <{}>;tag={}", c.id.impu, d.itag),
            std::format("To: <{}>", d.ruri),
            std::format("Call-ID: {}", d.callid),
            "CSeq: 1 INVITE",
            std::format("Contact: <sip:{}@{}>;audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"", ContactUser(c), imsd::util::HostPort(c.local, c.portUs)),
            "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, PRACK, UPDATE, MESSAGE, REFER",
            std::format("Supported: {}", supported),
            "Accept-Contact: *;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"",
        };
        // TS 24.229 5.1.2A.1 requires service identification on a new MMTEL
        // dialog. Accept-Contact is only capability preference; it does not
        // replace P-Preferred-Service at the S-CSCF/service trigger boundary.
        if (d.ruri != SosUrn)
            l.push_back("P-Preferred-Service: urn:urn-7:3gpp-service.ims.icsi.mmtel");
        // see BuildSubscribeReg: P-Preferred-Identity only when known
        if (!c.ppi.empty()) l.push_back(std::format("P-Preferred-Identity: <{}>", c.ppi));
        if (!c.panInfo.empty()) l.push_back(std::format("P-Access-Network-Info: {}", c.panInfo));
        l.push_back(std::format("Security-Verify: {}", c.securityServer));
        l.push_back("Proxy-Require: sec-agree");
        l.push_back("Require: sec-agree");
        l.push_back(std::format("Session-Expires: {};refresher=uac", sessionExpires));
        l.push_back("Min-SE: 90");
        l.push_back("Content-Type: application/sdp");
        l.push_back(std::format("Content-Length: {}", sdp.size()));
        l.push_back("");
        return detail::Join(l) + std::string(sdp);
    }

    // An in-dialog request (PRACK, BYE, UPDATE...) over the dialog route set.
    // `extra` lines go after CSeq; an optional body sets Content-Type/Length.
    std::string BuildInDialog(const Context& c, const Dialog& d, std::string_view method, int cseq, std::string_view viaBranch, std::span<const std::string> extra = {}, std::string_view body = {}, std::string_view ctype = {}) {
        std::string target = d.remoteTarget.value_or(d.ruri);
        std::string to = d.dialogTo.value_or(std::format("<{}>", d.ruri));
        std::vector<std::string> l = {
            std::format("{} {} SIP/2.0", method, target),
            ViaLine(c, "TCP", c.portUs, viaBranch),
            "Max-Forwards: 70",
            std::format("Route: {}", DialogRoute(c, d)),
            std::format("From: <{}>;tag={}", c.id.impu, d.itag),
            std::format("To: {}", to),
            std::format("Call-ID: {}", d.callid),
            std::format("CSeq: {} {}", cseq, method),
        };
        for (const std::string& e : extra)
            l.push_back(e);
        if (!body.empty()) {
            l.push_back(std::format("Content-Type: {}", ctype));
            l.push_back(std::format("Content-Length: {}", body.size()));
            l.push_back("");
            return detail::Join(l) + std::string(body);
        }
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }

    // ACK for a 2xx final: a new transaction over the dialog route to the
    // remote target, echoing the dialog To (with the remote tag).
    std::string BuildAck2xx(const Context& c, const Dialog& d, std::string_view viaBranch, std::string_view toHeader) {
        std::string target = d.remoteTarget.value_or(d.ruri);
        std::string to = d.dialogTo.value_or(std::string(toHeader));
        std::vector<std::string> l = {
            std::format("ACK {} SIP/2.0", target),
            ViaLine(c, "TCP", c.portUs, viaBranch),
            "Max-Forwards: 70",
            std::format("Route: {}", DialogRoute(c, d)),
            std::format("From: <{}>;tag={}", c.id.impu, d.itag),
            std::format("To: {}", to),
            std::format("Call-ID: {}", d.callid),
            "CSeq: 1 ACK",
            "Content-Length: 0",
        };
        return std::format("{}\r\n", detail::Join(l));
    }

    // ACK for a non-2xx final: hop-by-hop over the INVITE's own branch and the
    // registration Route, echoing the response's To.
    std::string BuildAckNon2xx(const Context& c, const Dialog& d, std::string_view toHeader) {
        std::vector<std::string> l = {
            std::format("ACK {} SIP/2.0", d.ruri),
            std::format("Via: SIP/2.0/TCP {};branch=z9hG4bK{};rport", imsd::util::HostPort(c.local, c.portUs), d.invBranch),
            "Max-Forwards: 70",
            std::format("Route: {}", c.route),
            std::format("From: <{}>;tag={}", c.id.impu, d.itag),
            std::format("To: {}", toHeader),
            std::format("Call-ID: {}", d.callid),
            "CSeq: 1 ACK",
            "Content-Length: 0",
        };
        return std::format("{}\r\n", detail::Join(l));
    }

    // CANCEL: RFC 3261 requires it copy the INVITE's branch + CSeq number.
    std::string BuildCancel(const Context& c, const Dialog& d) {
        std::vector<std::string> l = {
            std::format("CANCEL {} SIP/2.0", d.ruri),
            std::format("Via: SIP/2.0/TCP {};branch=z9hG4bK{};rport", imsd::util::HostPort(c.local, c.portUs), d.invBranch),
            "Max-Forwards: 70",
            std::format("Route: {}", c.route),
            std::format("From: <{}>;tag={}", c.id.impu, d.itag),
            std::format("To: <{}>", d.ruri),
            std::format("Call-ID: {}", d.callid),
            "CSeq: 1 CANCEL",
            "Content-Length: 0",
        };
        return std::format("{}\r\n", detail::Join(l));
    }

    // A response to an inbound INVITE — the UAS side of call setup. Unlike
    // BuildResponse200 (first Via only, fine for hop-by-hop CANCEL/BYE
    // answers), an INVITE response travels the whole proxy chain back to the
    // caller: it must echo EVERY Via in order, return the Record-Route set on
    // dialog-establishing (1xx/2xx) responses, and mint the dialog's local
    // half by tagging To (all but 100 Trying, which is hop-by-hop and
    // tagless). 1xx/2xx carry our Contact (the dialog's remote target for the
    // caller) and Allow; `extra` appends headers (RSeq, Session-Expires...);
    // `sdp` turns a 200 into the answer.
    std::string BuildInviteResponse(const Context& c, std::string_view invite, int code, std::string_view text, std::string_view toTag, std::optional<std::string_view> sdp = std::nullopt, std::span<const std::string> extra = {}) {
        std::vector<std::string> l = {std::format("SIP/2.0 {} {}", code, text)};
        for (auto v : imsd::sip::Headers(invite, "Via"))
            l.push_back(std::format("Via: {}", v));
        if (code < 300)
            for (auto v : imsd::sip::Headers(invite, "Record-Route"))
                l.push_back(std::format("Record-Route: {}", v));
        if (auto f = imsd::sip::Header(invite, "From")) l.push_back(std::format("From: {}", *f));
        if (auto t = imsd::sip::Header(invite, "To")) {
            if (code == 100 || t->contains("tag=")) l.push_back(std::format("To: {}", *t));
            else
                l.push_back(std::format("To: {};tag={}", *t, toTag));
        }
        if (auto ci = imsd::sip::Header(invite, "Call-ID")) l.push_back(std::format("Call-ID: {}", *ci));
        if (auto cs = imsd::sip::Header(invite, "CSeq")) l.push_back(std::format("CSeq: {}", *cs));
        if (code != 100 && code < 300) {
            l.push_back(std::format("Contact: <sip:{}@{}>;audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"", ContactUser(c), imsd::util::HostPort(c.local, c.portUs)));
            l.push_back("Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, PRACK, UPDATE, MESSAGE, REFER");
        }
        // TS 24.229 5.1.2A: the UE inserts P-Access-Network-Info into any
        // request for a dialog, any subsequent request (except ACK/CANCEL) and
        // any RESPONSE within a dialog except CANCEL responses. Luma published
        // it on REGISTER/SUBSCRIBE/originating INVITE but on NO terminating
        // response, so on an incoming call the network learned the UE's serving
        // access at registration time and never at call time. A terminating
        // network doing access/location validation on the mobile branch has no
        // current access information to validate — the observed AT&T (Nokia/ALU
        // HPSS TAS) behaviour is a CANCEL at a fixed ~301 ms carrying
        // Reason: SIP;cause=480;text="Invalid Location". 100 Trying is excluded:
        // it is hop-by-hop and precedes the early dialog, and the stock modem
        // does not carry access information on it. CANCEL responses keep the
        // spec exception (they are built by BuildResponse200, not here).
        if (code != 100 && !c.panInfo.empty())
            l.push_back(std::format("P-Access-Network-Info: {}", c.panInfo));
        for (const std::string& e : extra)
            l.push_back(e);
        if (sdp) {
            l.push_back("Content-Type: application/sdp");
            l.push_back(std::format("Content-Length: {}", sdp->size()));
            l.push_back("");
            return detail::Join(l) + std::string(*sdp);
        }
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }

    // A 200 OK for an inbound request: echo Via/From/To/Call-ID/CSeq in order,
    // then either an SDP answer (with our Contact) or an empty body.
    // `extra` appends headers (Session-Expires echo for refreshes...).
    std::string BuildResponse200(const Context& c, std::string_view request, std::optional<std::string_view> sdp = std::nullopt, std::span<const std::string> extra = {}) {
        std::vector<std::string> l = {"SIP/2.0 200 OK"};
        for (std::string_view h : {"Via", "From", "To", "Call-ID", "CSeq"})
            if (auto v = imsd::sip::Header(request, h)) l.push_back(std::format("{}: {}", h, *v));
        for (const auto& e : extra)
            l.push_back(e);
        if (sdp) {
            l.push_back(std::format("Contact: <sip:{}@{}>;audio;+g.3gpp.icsi-ref=\"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel\"", ContactUser(c), imsd::util::HostPort(c.local, c.portUs)));
            l.push_back("Content-Type: application/sdp");
            l.push_back(std::format("Content-Length: {}", sdp->size()));
            l.push_back("");
            return detail::Join(l) + std::string(*sdp);
        }
        l.push_back("Content-Length: 0");
        return std::format("{}\r\n", detail::Join(l));
    }
}
