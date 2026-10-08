// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Engine unit tests — the call state machine driven through recorded
// network dialogs. Outgoing: reliable-18x PRACK, 2xx ACK + media start,
// media-plane far-end hangup, local hang-up (CANCEL before answer, BYE
// after), the CANCEL/answer race, and busy/error finals. Incoming: 100+180
// on the INVITE (reliable when the caller requires 100rel), Accept -> 200
// with the SDP answer at the offer's payload types + media, ACK silence,
// reject -> 486, remote CANCEL -> 200+487, unusable offer -> 488, ring
// timeout -> 480, BYE both ways. Pure reducer, no I/O.
import std;
import Imsd;

using namespace imsd::engine;
using imsd::msg::Context;

namespace {
    int Failures = 0;
    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }

    Context MakeCtx() {
        Context c;
        c.id = imsd::aka::MakeIdentity("001010123456789", "001", "01");
        c.local = "2001:db8::db43";
        c.route = "<sip:[2001:db8::105]:6000;lr>";
        c.ppi = "tel:+31611111111";
        c.securityServer = "ipsec-3gpp; spi-c=1; spi-s=2; port-c=3; port-s=4";
        return c;
    }

    bool Has(const std::vector<Action>& a, Action::Type t) {
        for (const auto& x : a) if (x.type == t) return true;
        return false;
    }
    const Action* Find(const std::vector<Action>& a, Action::Type t) {
        for (const auto& x : a) if (x.type == t) return &x;
        return nullptr;
    }
    // First SendClient whose text starts with `method `.
    const Action* Sent(const std::vector<Action>& a, std::string_view method) {
        for (const auto& x : a)
            if (x.type == Action::Type::SendClient && x.text.starts_with(method)) return &x;
        return nullptr;
    }

    // Build a response with the machine's own Call-ID (so dialog matching in a
    // real shell would hold; the reducer itself doesn't re-check).
    std::string R183(std::string_view callid) {
        return std::format(
            "SIP/2.0 183 Session Progress\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45061;branch=z9hG4bKx\r\n"
            "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=it\r\n"
            "To: <sip:1233@ims>;tag=remote7\r\n"
            "Call-ID: {}\r\n"
            "CSeq: 1 INVITE\r\n"
            "Record-Route: <sip:[2001:db8::105]:6000;lr>\r\n"
            "Require: 100rel\r\n"
            "RSeq: 1\r\n"
            "P-Early-Media: sendonly\r\n"
            "Contact: <sip:termgw@[2001:db8::9]:5060>\r\n"
            "Content-Type: application/sdp\r\n"
            "Content-Length: 120\r\n\r\n"
            "v=0\r\no=- 1 1 IN IP6 2001:db8::9\r\ns=-\r\nc=IN IP6 2001:db8::9\r\n"
            "t=0 0\r\nm=audio 6004 RTP/AVP 97\r\na=rtpmap:97 AMR-WB/16000/1\r\n", callid);
    }
    std::string R200(std::string_view callid) {
        return std::format(
            "SIP/2.0 200 OK\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45061;branch=z9hG4bKx\r\n"
            "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=it\r\n"
            "To: <sip:1233@ims>;tag=remote7\r\n"
            "Call-ID: {}\r\n"
            "CSeq: 1 INVITE\r\n"
            "Contact: <sip:termgw@[2001:db8::9]:5060>\r\n"
            "Content-Type: application/sdp\r\n"
            "Content-Length: 120\r\n\r\n"
            "v=0\r\no=- 1 1 IN IP6 2001:db8::9\r\ns=-\r\nc=IN IP6 2001:db8::9\r\n"
            "t=0 0\r\nm=audio 6004 RTP/AVP 97\r\na=rtpmap:97 AMR-WB/16000/1\r\n", callid);
    }
    std::string Final(std::string_view callid, int code, std::string_view text) {
        return std::format(
            "SIP/2.0 {} {}\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45061;branch=z9hG4bKx\r\n"
            "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=it\r\n"
            "To: <sip:1233@ims>;tag=remote7\r\n"
            "Call-ID: {}\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n",
            code, text, callid);
    }

    // First SendResponse whose text starts with `prefix`.
    const Action* Responded(const std::vector<Action>& a, std::string_view prefix) {
        for (const auto& x : a)
            if (x.type == Action::Type::SendResponse && x.text.starts_with(prefix)) return &x;
        return nullptr;
    }

    // A 380 Alternative Service final; `body` empty = no emergency
    // indication (a bare redirect).
    std::string Alt380(std::string_view callid, std::string_view body) {
        return std::format(
            "SIP/2.0 380 Alternative Service\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45061;branch=z9hG4bKx\r\n"
            "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=it\r\n"
            "To: <sip:1233@ims>;tag=remote7\r\n"
            "Call-ID: {}\r\nCSeq: 1 INVITE\r\n"
            "Content-Type: application/3gpp-ims+xml\r\n"
            "Content-Length: {}\r\n\r\n{}",
            callid, body.size(), body);
    }
    constexpr std::string_view EmergencyXml =
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<ims-3gpp version=\"1\"><alternative-service><type><emergency/></type>"
        "<reason>emergency call detected</reason></alternative-service></ims-3gpp>";

    // An inbound (terminating) INVITE the way KPN's P-CSCF delivers one:
    // two Vias, a Record-Route pair, P-Asserted-Identity, an AMR-WB offer
    // at non-default payload types (104/105) with telephone-event.
    std::string MtInvite(std::string_view extraHeaders = "") {
        std::string sdp =
            "v=0\r\no=- 55 55 IN IP6 2001:db8::30c\r\ns=-\r\n"
            "c=IN IP6 2001:db8::30c\r\nt=0 0\r\n"
            "m=audio 27864 RTP/AVP 104 105\r\n"
            "a=rtpmap:104 AMR-WB/16000/1\r\n"
            "a=fmtp:104 octet-align=1;mode-change-capability=2\r\n"
            "a=rtpmap:105 telephone-event/16000\r\n"
            "a=fmtp:105 0-15\r\n"
            "a=ptime:20\r\na=sendrecv\r\n";
        return std::format(
            "INVITE sip:001010123456789@[2001:db8::db43]:45061 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105]:5100;branch=z9hG4bKpcscf1\r\n"
            "Via: SIP/2.0/UDP [2001:db8::9]:5060;branch=z9hG4bKscscf1\r\n"
            "Record-Route: <sip:[2001:db8::105]:5100;lr>\r\n"
            "Record-Route: <sip:[2001:db8::9]:5060;lr>\r\n"
            "Max-Forwards: 68\r\n"
            "From: <sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org;user=phone>\r\n"
            "Call-ID: mt-call-abc123\r\n"
            "CSeq: 1 INVITE\r\n"
            "Contact: <sip:origgw@[2001:db8::9]:5060>\r\n"
            "P-Asserted-Identity: <tel:+31612345678>\r\n"
            "Session-Expires: 1800\r\n"
            "{}"
            "Content-Type: application/sdp\r\n"
            "Content-Length: {}\r\n\r\n{}",
            extraHeaders, sdp.size(), sdp);
    }
}

int main() {
    Context c = MakeCtx();

    // ---- full answered call: INVITE -> 100 -> 183(rel)+PRACK -> 200 -> ACK
    //      + media -> far-end hangup
    {
        imsd::util::Rng rng(42);
        CallMachine m(c, rng, "ims-call-1", "1233", 50004, true);
        auto start = m.Start();
        Check(Sent(start, "INVITE ") != nullptr, "Start sends INVITE");
        Check(Has(start, Action::Type::SetDeadline), "Start arms ring timeout");
        Check(m.State() == CallState::Dialing, "starts dialing");

        // 100 Trying: nothing changes
        auto a100 = m.OnInviteResponse(Final(m.CallId(), 100, "Trying"));
        Check(a100.empty(), "100 Trying is a no-op");
        Check(m.State() == CallState::Dialing, "still dialing after 100");

        // 183 with reliable 100rel + SDP: -> ringing, PRACK
        auto a183 = m.OnInviteResponse(R183(m.CallId()));
        Check(m.State() == CallState::Ringing, "183 -> ringing");
        const Action* stAct = Find(a183, Action::Type::State);
        Check(stAct && stAct->state == "ringing" && stAct->reason == "outgoing", "183 emits ringing/outgoing");
        const Action* prack = Sent(a183, "PRACK ");
        Check(prack != nullptr, "reliable 183 triggers PRACK");
        Check(prack && prack->text.contains("RAck: 1 1 INVITE\r\n"), "PRACK RAck");
        Check(prack && prack->text.contains("CSeq: 2 PRACK\r\n"), "PRACK CSeq 2");
        // PRACK targets the remote Contact and routes via the Record-Route
        Check(prack && prack->text.starts_with("PRACK sip:termgw@[2001:db8::9]:5060 SIP/2.0"), "PRACK targets remote Contact");
        Check(prack && prack->text.contains("Route: <sip:[2001:db8::105]:6000;lr>\r\n"), "PRACK routes via reversed Record-Route");
        const Action* early = Find(a183, Action::Type::StartMedia);
        Check(early != nullptr, "authorized 183 starts early media");
        Check(early && !early->media.transmit, "early media keeps microphone closed");

        // The accepted PRACK completes the reliable-provisional exchange;
        // report the established SDP session ready with an UPDATE. This
        // report local media readiness while leaving the remote direction
        // pending until the UPDATE answer.
        auto aprack = m.OnPrackResponse(Final(m.CallId(), 200, "OK"));
        const Action* update = Sent(aprack, "UPDATE ");
        Check(update != nullptr, "200(PRACK) triggers media-resource UPDATE");
        Check(update && update->text.contains("m=audio 50004 RTP/AVP 97 98\r\n"), "UPDATE carries media offer");
        Check(update && update->text.contains("a=curr:qos local sendrecv\r\n"), "UPDATE reports working local path");
        Check(update && update->text.contains("a=curr:qos remote none\r\n"), "UPDATE leaves remote resources pending");
        Check(update && update->text.contains("a=des:qos mandatory local sendrecv\r\n"), "UPDATE preserves mandatory QoS request");
        Check(Sent(m.OnPrackResponse(Final(m.CallId(), 200, "OK")), "UPDATE ") == nullptr,
              "duplicate 200(PRACK) does not repeat UPDATE");

        // a second identical 183 must NOT re-PRACK the same RSeq
        auto a183b = m.OnInviteResponse(R183(m.CallId()));
        Check(Sent(a183b, "PRACK ") == nullptr, "duplicate RSeq not re-PRACKed");

        // 200 OK: ACK + replace receive-only early media with full media + active
        auto a200 = m.OnInviteResponse(R200(m.CallId()));
        const Action* ack = Sent(a200, "ACK ");
        Check(ack != nullptr, "200 -> ACK");
        Check(ack && ack->text.starts_with("ACK sip:termgw@[2001:db8::9]:5060 SIP/2.0"), "ACK targets remote Contact");
        Check(ack && ack->text.contains("To: <sip:1233@ims>;tag=remote7\r\n"), "ACK carries dialog To tag");
        const Action* media = Find(a200, Action::Type::StartMedia);
        Check(media != nullptr, "200 starts media");
        Check(Has(a200, Action::Type::StopMedia), "200 replaces early-media leg");
        Check(media && media->media.transmit, "answered media enables microphone");
        Check(media && media->media.Valid(), "media leg valid");
        Check(media && media->media.remoteIp == "2001:db8::9", "media remote ip");
        Check(media && media->media.remotePort == 6004, "media remote port");
        Check(media && media->media.payloadType == 97, "media pt");
        Check(media && media->media.codec == "AMR-WB", "media codec");
        const Action* active = Find(a200, Action::Type::State);
        Check(active && active->state == "active" && active->reason == "accepted", "200 emits active/accepted");
        Check(m.State() == CallState::Active, "state active");

        // media leg exits code 3 (downlink dried up) -> BYE + terminated
        auto amx = m.OnMediaExit(3);
        Check(Sent(amx, "BYE ") != nullptr, "media far-end hangup sends BYE");
        const Action* term = Find(amx, Action::Type::State);
        Check(term && term->state == "terminated" && term->reason == "remote-hangup", "media exit -> terminated/remote-hangup");
        Check(Has(amx, Action::Type::StopMedia), "stops media");
        Check(Has(amx, Action::Type::Deleted), "emits deleted");
        Check(m.Terminated(), "terminated");
    }

    // ---- ordinary (non-precondition) reliable provisional response
    // PRACK completes the 100rel exchange; it must not manufacture an UPDATE.
    {
        imsd::util::Rng rng(43);
        CallMachine m(c, rng, "ims-call-no-precond", "1233", 50004, false);
        m.Start();
        m.OnInviteResponse(R183(m.CallId()));
        auto aprack = m.OnPrackResponse(Final(m.CallId(), 200, "OK"));
        Check(Sent(aprack, "UPDATE ") == nullptr,
              "non-precondition PRACK response does not send UPDATE");
    }

    // ---- remote BYE mid-call
    {
        imsd::util::Rng rng(7);
        CallMachine m(c, rng, "ims-call-2", "1233", 50004, false);
        m.Start();
        m.OnInviteResponse(R200(m.CallId()));
        Check(m.State() == CallState::Active, "answered");
        std::string bye = std::format(
            "BYE sip:me SIP/2.0\r\nVia: SIP/2.0/TCP [x]:1;branch=z9hG4bKb\r\n"
            "From: <sip:them>;tag=remote7\r\nTo: <sip:me>;tag=it\r\n"
            "Reason: SIP;cause=503;text=\"PT: Session timer expired.\"\r\n"
            "Call-ID: {}\r\nCSeq: 9 BYE\r\nContent-Length: 0\r\n\r\n", m.CallId());
        auto ab = m.OnRequest(bye);
        const Action* resp = Find(ab, Action::Type::SendResponse);
        Check(resp && resp->text.starts_with("SIP/2.0 200 OK"), "remote BYE answered 200");
        Check(resp && resp->text.contains("CSeq: 9 BYE\r\n"), "200 echoes BYE CSeq");
        const Action* t = Find(ab, Action::Type::State);
        Check(t && t->reason == "remote-hangup", "remote BYE -> remote-hangup");
        const Action* lg = Find(ab, Action::Type::Log);
        Check(lg && lg->text.contains("ended: remote-hangup") && lg->text.contains("Reason: SIP;cause=503"), "teardown log names the peer's Reason");
        Check(m.Terminated(), "terminated after remote BYE");
    }

    // ---- local hang-up before answer sends CANCEL; the 200/487 race
    {
        imsd::util::Rng rng(9);
        CallMachine m(c, rng, "ims-call-3", "1233", 50004, false);
        m.Start();
        m.OnInviteResponse(R183(m.CallId()));       // ringing
        auto ah = m.OnHangup();
        Check(Sent(ah, "CANCEL ") != nullptr, "hangup while ringing sends CANCEL");
        Check(Has(ah, Action::Type::SetDeadline), "arms 487 timeout after CANCEL");
        Check(!m.Terminated(), "not terminated until 487");
        // a late 200 wins the race: ACK, then BYE, then terminate local-hangup
        auto ar = m.OnInviteResponse(R200(m.CallId()));
        Check(Sent(ar, "ACK ") != nullptr, "race 200 is ACKed");
        Check(Sent(ar, "BYE ") != nullptr, "race 200 then BYE (we cancelled)");
        const Action* t = Find(ar, Action::Type::State);
        Check(t && t->reason == "local-hangup", "cancel race -> local-hangup");
        // no media for a cancelled call
        Check(!Has(ar, Action::Type::StartMedia), "cancelled call starts no media");
    }

    // ---- 487 after CANCEL
    {
        imsd::util::Rng rng(11);
        CallMachine m(c, rng, "ims-call-4", "1233", 50004, false);
        m.Start();
        m.OnHangup();                                // CANCEL while dialing
        auto a487 = m.OnInviteResponse(Final(m.CallId(), 487, "Request Terminated"));
        Check(Sent(a487, "ACK ") != nullptr, "487 is ACKed (non-2xx)");
        const Action* t = Find(a487, Action::Type::State);
        Check(t && t->reason == "local-hangup", "487 -> local-hangup");
        Check(m.Terminated(), "terminated on 487");
    }

    // ---- busy / declined finals
    {
        for (auto [code, want] : std::vector<std::pair<int, std::string>>{
                 {486, "refused-or-busy"}, {480, "refused-or-busy"},
                 {603, "refused-or-busy"}, {500, "error"}, {403, "error"}}) {
            imsd::util::Rng rng(code);
            CallMachine m(c, rng, "u", "1233", 50004, false);
            m.Start();
            auto a = m.OnInviteResponse(Final(m.CallId(), code, "x"));
            Check(Sent(a, "ACK ") != nullptr, std::format("{} ACKed", code));
            const Action* t = Find(a, Action::Type::State);
            Check(t && t->reason == want, std::format("{} -> {}", code, want));
            Check(m.Terminated(), std::format("{} terminates", code));
        }
    }

    // ---- BYE after answer (local hangup during active call)
    {
        imsd::util::Rng rng(13);
        CallMachine m(c, rng, "ims-call-5", "1233", 50004, false);
        m.Start();
        m.OnInviteResponse(R200(m.CallId()));
        auto a = m.OnHangup();
        const Action* bye = Sent(a, "BYE ");
        Check(bye != nullptr, "active hangup sends BYE");
        Check(bye && bye->text.contains("CSeq: 3 BYE\r\n"), "BYE CSeq after ACK=1");
        const Action* t = Find(a, Action::Type::State);
        Check(t && t->reason == "local-hangup", "active hangup -> local-hangup");
    }

    // ---- setup timeout -> CANCEL, then error
    {
        imsd::util::Rng rng(15);
        CallMachine m(c, rng, "ims-call-6", "1233", 50004, false);
        m.Start();
        auto a1 = m.OnDeadline();                    // ring timeout
        Check(Sent(a1, "CANCEL ") != nullptr, "ring timeout sends CANCEL");
        Check(!m.Terminated(), "not terminated after first timeout");
        auto a2 = m.OnDeadline();                    // 487 never came
        const Action* t = Find(a2, Action::Type::State);
        Check(t && t->reason == "error", "second timeout -> error");
        Check(m.Terminated(), "terminated on second timeout");
    }

    // ================= incoming (terminating / UAS) =======================

    // ---- full accepted incoming call: INVITE -> 100+180, Accept -> 200 with
    //      the answer at the offer's payload types + media, ACK, remote BYE
    {
        imsd::util::Rng rng(21);
        CallMachine m(c, rng, "ims-call-7", IncomingInvite{MtInvite()}, 50004);
        Check(m.Dir() == Direction::Incoming, "direction incoming");
        Check(m.State() == CallState::Incoming, "starts incoming");
        Check(m.Number() == "+31612345678", "caller id from P-Asserted-Identity");
        Check(m.CallId() == "mt-call-abc123", "call id from INVITE");

        auto ai = m.OnInvite();
        const Action* trying = Responded(ai, "SIP/2.0 100 Trying");
        Check(trying != nullptr, "INVITE -> 100 Trying");
        Check(trying && trying->text.contains("To: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org;user=phone>\r\n"), "100 leaves To untagged");
        const Action* ringing = Responded(ai, "SIP/2.0 180 Ringing");
        Check(ringing != nullptr, "INVITE -> 180 Ringing");
        Check(ringing && !ringing->text.contains("RSeq:"), "180 unreliable when 100rel not required");
        Check(Has(ai, Action::Type::SetDeadline), "arms ring timeout");
        // both Vias echoed, in order
        Check(ringing && ringing->text.contains("Via: SIP/2.0/UDP [2001:db8::105]:5100") && ringing->text.contains("Via: SIP/2.0/UDP [2001:db8::9]:5060"), "180 echoes all Vias");
        Check(ringing && ringing->text.find("[2001:db8::105]:5100") < ringing->text.find("[2001:db8::9]:5060"), "180 Via order preserved");
        Check(ringing && ringing->text.contains("Record-Route: <sip:[2001:db8::105]:5100;lr>"), "180 returns the Record-Route set");
        Check(ringing && ringing->text.contains("To: <sip:+31611111111@ims.mnc001.mcc001." "3gppnetwork.org;user=phone>;tag="), "180 mints a To tag");

        // retransmitted INVITE (lost provisional): repeat the 180
        auto art = m.OnRequest(MtInvite());
        Check(Responded(art, "SIP/2.0 180 Ringing") != nullptr, "INVITE retransmit re-sends the 180");
        Check(m.State() == CallState::Incoming, "still incoming");

        auto aa = m.OnAccept();
        const Action* ok = Responded(aa, "SIP/2.0 200 OK");
        Check(ok != nullptr, "Accept -> 200 OK");
        Check(ok && ok->text.contains("m=audio 50004 RTP/AVP 104 105\r\n"), "answer uses the offer's payload types");
        Check(ok && ok->text.contains("a=rtpmap:104 AMR-WB/16000/1\r\n"), "answer keeps AMR-WB at the offer's pt");
        Check(ok && ok->text.contains("a=fmtp:104 octet-align=1\r\n"), "answer mirrors octet-align");
        Check(ok && ok->text.contains("a=rtpmap:105 telephone-event/16000\r\n"), "answer keeps telephone-event");
        Check(ok && ok->text.contains("Session-Expires: 1800;refresher=uac\r\n"), "200 echoes Session-Expires, caller refreshes");
        Check(ok && ok->text.contains("Contact: <sip:001010123456789@"), "200 carries our Contact");
        // the 180 and 200 carry the SAME To tag (one dialog)
        if (ringing && ok) {
            auto tag = [](const std::string& s) {
                std::size_t at = s.find(";tag=", s.find("To:"));
                return s.substr(at, s.find('\r', at) - at);
            };
            Check(tag(ringing->text) == tag(ok->text), "180/200 share the To tag");
        }
        const Action* media = Find(aa, Action::Type::StartMedia);
        Check(media != nullptr, "Accept starts media");
        Check(media && media->media.remoteIp == "2001:db8::30c", "media ip from offer");
        Check(media && media->media.remotePort == 27864, "media port from offer");
        Check(media && media->media.payloadType == 104, "media pt from offer");
        const Action* st = Find(aa, Action::Type::State);
        Check(st && st->state == "active" && st->reason == "accepted", "Accept emits active/accepted");
        Check(m.State() == CallState::Active, "active after Accept");

        // a second Accept is a no-op
        Check(m.OnAccept().empty(), "double Accept is a no-op");

        // the caller's ACK is consumed silently
        std::string ack = std::format(
            "ACK sip:001010123456789@[2001:db8::db43]:45061 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105]:5100;branch=z9hG4bKack\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>;tag=x\r\n"
            "Call-ID: {}\r\nCSeq: 1 ACK\r\nContent-Length: 0\r\n\r\n", m.CallId());
        Check(m.OnRequest(ack).empty(), "ACK gets no response");

        // session-refresh UPDATE without a body: bodyless 200
        std::string upd = std::format(
            "UPDATE sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKu\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>;tag=x\r\n"
            "Call-ID: {}\r\nCSeq: 2 UPDATE\r\nContent-Length: 0\r\n\r\n", m.CallId());
        auto au = m.OnRequest(upd);
        const Action* u200 = Responded(au, "SIP/2.0 200 OK");
        Check(u200 != nullptr, "bodyless UPDATE answered 200");
        Check(u200 && u200->text.contains("Content-Length: 0"), "bodyless UPDATE gets a bodyless 200");
        Check(u200 && !u200->text.contains("Session-Expires"), "no Session-Expires in the UPDATE -> none invented in the 200");

        // session-audit UPDATE with Session-Expires (KPN B2BUA, ~55 s into
        // every call): the 200 MUST echo it (RFC 4028 §9) or the network
        // releases with "Session timer expired"
        std::string se = std::format(
            "UPDATE sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKu2\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>;tag=x\r\n"
            "Min-SE: 90\r\nSession-Expires: 1800;refresher=uac\r\n"
            "Supported: timer\r\n"
            "Call-ID: {}\r\nCSeq: 3 UPDATE\r\nContent-Length: 0\r\n\r\n", m.CallId());
        auto ase = m.OnRequest(se);
        const Action* se200 = Responded(ase, "SIP/2.0 200 OK");
        Check(se200 != nullptr, "refresh UPDATE answered 200");
        Check(se200 && se200->text.contains("Session-Expires: 1800;refresher=uac\r\n"), "200 echoes Session-Expires");
        Check(se200 && se200->text.contains("Require: timer\r\n"), "200 carries Require: timer");

        // remote BYE ends it
        std::string bye = std::format(
            "BYE sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKb\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>;tag=x\r\n"
            "Call-ID: {}\r\nCSeq: 4 BYE\r\nContent-Length: 0\r\n\r\n", m.CallId());
        auto ab = m.OnRequest(bye);
        Check(Responded(ab, "SIP/2.0 200 OK") != nullptr, "remote BYE answered");
        const Action* t = Find(ab, Action::Type::State);
        Check(t && t->reason == "remote-hangup", "remote BYE -> remote-hangup");
        Check(m.Terminated(), "terminated");
    }

    // ---- reliable 180 when the caller requires 100rel; PRACK answered
    {
        imsd::util::Rng rng(23);
        CallMachine m(c, rng, "ims-call-8", IncomingInvite{MtInvite("Require: 100rel\r\n")}, 50004);
        auto ai = m.OnInvite();
        const Action* ringing = Responded(ai, "SIP/2.0 180 Ringing");
        Check(ringing && ringing->text.contains("Require: 100rel\r\n") && ringing->text.contains("RSeq: 1\r\n"), "Require: 100rel makes the 180 reliable");
        std::string prack = std::format(
            "PRACK sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKp\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>;tag=x\r\n"
            "Call-ID: {}\r\nCSeq: 2 PRACK\r\nRAck: 1 1 INVITE\r\n"
            "Content-Length: 0\r\n\r\n", m.CallId());
        auto ap = m.OnRequest(prack);
        const Action* p200 = Responded(ap, "SIP/2.0 200 OK");
        Check(p200 != nullptr, "PRACK answered 200");
        Check(p200 && p200->text.contains("CSeq: 2 PRACK\r\n"), "PRACK 200 echoes CSeq");
        Check(m.State() == CallState::Incoming, "PRACK leaves state incoming");
    }

    // ---- local reject: HangUp while ringing -> 486
    {
        imsd::util::Rng rng(25);
        CallMachine m(c, rng, "ims-call-9", IncomingInvite{MtInvite()}, 50004);
        m.OnInvite();
        auto ah = m.OnHangup();
        const Action* busy = Responded(ah, "SIP/2.0 486 Busy Here");
        Check(busy != nullptr, "reject sends 486");
        Check(busy && busy->text.contains("CSeq: 1 INVITE\r\n"), "486 answers the INVITE");
        const Action* t = Find(ah, Action::Type::State);
        Check(t && t->reason == "local-hangup", "reject -> local-hangup");
        Check(m.Terminated(), "terminated after reject");
    }

    // ---- remote CANCEL while ringing: 200 the CANCEL, 487 the INVITE
    {
        imsd::util::Rng rng(27);
        CallMachine m(c, rng, "ims-call-10", IncomingInvite{MtInvite()}, 50004);
        m.OnInvite();
        std::string cancel = std::format(
            "CANCEL sip:001010123456789@[2001:db8::db43]:45061 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105]:5100;branch=z9hG4bKpcscf1\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>\r\n"
            "Call-ID: {}\r\nCSeq: 1 CANCEL\r\nContent-Length: 0\r\n\r\n", m.CallId());
        auto ac = m.OnRequest(cancel);
        const Action* c200 = Responded(ac, "SIP/2.0 200 OK");
        Check(c200 && c200->text.contains("CSeq: 1 CANCEL\r\n"), "CANCEL answered 200 on its own transaction");
        const Action* r487 = Responded(ac, "SIP/2.0 487 Request Terminated");
        Check(r487 && r487->text.contains("CSeq: 1 INVITE\r\n"), "the INVITE transaction gets the 487");
        const Action* t = Find(ac, Action::Type::State);
        Check(t && t->reason == "remote-hangup", "remote CANCEL -> remote-hangup");
        Check(m.Terminated(), "terminated after remote CANCEL");
    }

    // ---- offer we cannot serve (no AMR-WB): 488 before ringing
    {
        imsd::util::Rng rng(29);
        std::string sdp =
            "v=0\r\no=- 5 5 IN IP6 2001:db8::30c\r\ns=-\r\n"
            "c=IN IP6 2001:db8::30c\r\nt=0 0\r\n"
            "m=audio 27864 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\n";
        std::string inv = std::format(
            "INVITE sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKi\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>\r\n"
            "Call-ID: mt-pcma\r\nCSeq: 1 INVITE\r\n"
            "Contact: <sip:origgw@[2001:db8::9]:5060>\r\n"
            "Content-Type: application/sdp\r\nContent-Length: {}\r\n\r\n{}",
            sdp.size(), sdp);
        CallMachine m(c, rng, "ims-call-11", IncomingInvite{inv}, 50004);
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 488 Not Acceptable Here") != nullptr, "non-AMR-WB offer refused with 488");
        Check(Responded(ai, "SIP/2.0 180 Ringing") == nullptr, "no 180 for a 488");
        Check(m.Terminated(), "terminated on 488");
    }

    // ---- ring timeout: 480, missed call
    {
        imsd::util::Rng rng(31);
        CallMachine m(c, rng, "ims-call-12", IncomingInvite{MtInvite()}, 50004);
        m.OnInvite();
        auto ad = m.OnDeadline();
        Check(Responded(ad, "SIP/2.0 480 Temporarily Unavailable") != nullptr, "ring timeout sends 480");
        const Action* t = Find(ad, Action::Type::State);
        Check(t && t->reason == "remote-hangup", "timeout reads as missed");
        Check(m.Terminated(), "terminated on ring timeout");
    }

    // ---- our BYE after accepting: UAS dialog fields hold
    {
        imsd::util::Rng rng(33);
        CallMachine m(c, rng, "ims-call-13", IncomingInvite{MtInvite()}, 50004);
        m.OnInvite();
        m.OnAccept();
        auto ah = m.OnHangup();
        const Action* bye = Sent(ah, "BYE ");
        Check(bye != nullptr, "hangup after accept sends BYE");
        Check(bye && bye->text.starts_with("BYE sip:origgw@[2001:db8::9]:5060 SIP/2.0"), "BYE targets the caller's Contact");
        Check(bye && bye->text.contains("CSeq: 1 BYE\r\n"), "our UAS CSeq space starts at 1");
        Check(bye && bye->text.contains("To: <sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org;user=phone>" ";tag=caller1\r\n"), "BYE To is the caller (their tag)");
        Check(bye && bye->text.contains("From: <sip:001010123456789@ims.mnc001.mcc001." "3gppnetwork.org>;tag="), "BYE From is us (our tag)");
        // UAS route set: Record-Route in RECEIVED order
        Check(bye && bye->text.contains("Route: <sip:[2001:db8::105]:5100;lr>, " "<sip:[2001:db8::9]:5060;lr>\r\n"), "BYE routes via Record-Route in received order");
        const Action* t = Find(ah, Action::Type::State);
        Check(t && t->reason == "local-hangup", "active hangup -> local-hangup");
    }

    // ---- media death after accept releases the call
    {
        imsd::util::Rng rng(35);
        CallMachine m(c, rng, "ims-call-14", IncomingInvite{MtInvite()}, 50004);
        m.OnInvite();
        m.OnAccept();
        auto amx = m.OnMediaExit(3);
        Check(Sent(amx, "BYE ") != nullptr, "media far-end hangup sends BYE (incoming too)");
        Check(m.Terminated(), "terminated");
    }

    // ================= emergency (stage 1) ================================

    // ---- emergency dial: the INVITE targets the sos URN over the normal
    //      registration; a 200 is answered like any call
    {
        imsd::util::Rng rng(41);
        CallMachine m(c, rng, "ims-call-15", "112", 50004, false, /*emergency=*/true);
        auto start = m.Start();
        const Action* inv = Sent(start, "INVITE ");
        Check(inv && inv->text.starts_with("INVITE urn:service:sos SIP/2.0\r\n"), "emergency R-URI is the sos URN");
        Check(inv && inv->text.contains("To: <urn:service:sos>\r\n"), "emergency To carries the URN");
        Check(inv && inv->text.contains("Route: <sip:[2001:db8::105]:6000;lr>\r\n"), "emergency INVITE rides the registration route");
        Check(inv && inv->text.contains("m=audio"), "emergency INVITE carries an SDP offer");
        Check(Has(start, Action::Type::SetDeadline), "emergency dial arms the timeout");
        auto a200 = m.OnInviteResponse(R200(m.CallId()));
        Check(Sent(a200, "ACK ") != nullptr, "sos 200 is ACKed");
        Check(Has(a200, Action::Type::StartMedia), "sos answer starts media");
        Check(m.State() == CallState::Active, "sos call active");
    }

    // ---- sos rejected -> one fallback: a fresh-dialog plain INVITE of the
    //      digits, whose answer is then taken normally
    {
        imsd::util::Rng rng(43);
        CallMachine m(c, rng, "ims-call-16", "112", 50004, false, true);
        m.Start();
        std::string firstId = m.CallId();
        auto a = m.OnInviteResponse(Final(m.CallId(), 500, "Server Internal Error"));
        Check(Sent(a, "ACK ") != nullptr, "sos final is ACKed");
        const Action* inv2 = Sent(a, "INVITE ");
        Check(inv2 && inv2->text.starts_with("INVITE sip:112;phone-context="), "fallback INVITE dials the digits");
        Check(m.CallId() != firstId, "fallback is a fresh dialog");
        Check(inv2 && inv2->text.contains(std::format("Call-ID: {}\r\n", m.CallId())), "fallback INVITE carries the new Call-ID");
        Check(inv2 && inv2->text.contains("CSeq: 1 INVITE\r\n"), "fallback INVITE restarts CSeq");
        Check(Has(a, Action::Type::SetDeadline), "fallback re-arms the timeout");
        Check(!m.Terminated(), "machine survives into the fallback");
        auto a2 = m.OnInviteResponse(R200(m.CallId()));
        Check(Has(a2, Action::Type::StartMedia), "fallback answer starts media");
        Check(m.State() == CallState::Active, "active on the fallback leg");
    }

    // ---- chain exhausted: the fallback leg's final terminates, no 3rd try
    {
        imsd::util::Rng rng(45);
        CallMachine m(c, rng, "ims-call-17", "112", 50004, false, true);
        m.Start();
        m.OnInviteResponse(Final(m.CallId(), 500, "x"));            // sos fails
        auto a = m.OnInviteResponse(Final(m.CallId(), 486, "Busy Here"));
        Check(Sent(a, "INVITE ") == nullptr, "no third attempt");
        const Action* t = Find(a, Action::Type::State);
        Check(t && t->reason == "refused-or-busy", "fallback final keeps its own reason");
        Check(m.Terminated(), "terminated when the chain is exhausted");
    }

    // ---- user hangup during the sos attempt kills the chain: 487 ends it
    {
        imsd::util::Rng rng(47);
        CallMachine m(c, rng, "ims-call-18", "112", 50004, false, true);
        m.Start();
        auto ah = m.OnHangup();
        Check(Sent(ah, "CANCEL ") != nullptr, "user hangup CANCELs the sos attempt");
        auto a = m.OnInviteResponse(Final(m.CallId(), 487, "Request Terminated"));
        Check(Sent(a, "INVITE ") == nullptr, "user cancel suppresses the fallback");
        const Action* t = Find(a, Action::Type::State);
        Check(t && t->reason == "local-hangup", "user-cancelled sos -> local-hangup");
        Check(m.Terminated(), "terminated");
    }

    // ---- setup timeout on the sos attempt: CANCEL, then the 487 falls back
    {
        imsd::util::Rng rng(49);
        CallMachine m(c, rng, "ims-call-19", "112", 50004, false, true);
        m.Start();
        auto a1 = m.OnDeadline();
        Check(Sent(a1, "CANCEL ") != nullptr, "sos timeout CANCELs");
        auto a2 = m.OnInviteResponse(Final(m.CallId(), 487, "Request Terminated"));
        Check(Sent(a2, "INVITE sip:112;phone-context=") != nullptr, "timeout-cancelled sos falls back to the digits");
        Check(!m.Terminated(), "chain continues after the timeout");
    }

    // ---- timeout CANCEL whose 487 never comes: second deadline falls back
    {
        imsd::util::Rng rng(51);
        CallMachine m(c, rng, "ims-call-20", "112", 50004, false, true);
        m.Start();
        m.OnDeadline();                                  // CANCEL
        auto a = m.OnDeadline();                         // no 487 arrived
        Check(Sent(a, "INVITE sip:112;phone-context=") != nullptr, "silent sos attempt still gets its fallback");
        Check(!m.Terminated(), "not terminated while the chain has a step left");
    }

    // ---- answered during a DEADLINE-initiated CANCEL race: taken, not BYEd
    {
        imsd::util::Rng rng(53);
        CallMachine m(c, rng, "ims-call-21", "112", 50004, false, true);
        m.Start();
        m.OnDeadline();                                  // CANCEL (not the user)
        auto ar = m.OnInviteResponse(R200(m.CallId()));
        Check(Sent(ar, "ACK ") != nullptr, "race 200 is ACKed");
        Check(Sent(ar, "BYE ") == nullptr, "deadline race keeps the answered call");
        Check(Has(ar, Action::Type::StartMedia), "deadline race starts media");
        Check(m.State() == CallState::Active, "active after the deadline race");
    }

    // ---- network 380 with the emergency indication upgrades a plain call
    //      to sos; if that also fails the chain ends (plain already tried)
    {
        imsd::util::Rng rng(55);
        CallMachine m(c, rng, "ims-call-22", "0612345678", 50004, false);   // NOT classified
        m.Start();
        auto a = m.OnInviteResponse(Alt380(m.CallId(), EmergencyXml));
        Check(Sent(a, "ACK ") != nullptr, "380 is ACKed");
        const Action* inv = Sent(a, "INVITE ");
        Check(inv && inv->text.starts_with("INVITE urn:service:sos SIP/2.0\r\n"), "emergency 380 upgrades to the sos URN");
        Check(!m.Terminated(), "call survives into the sos attempt");
        auto a2 = m.OnInviteResponse(Final(m.CallId(), 500, "x"));
        Check(Sent(a2, "INVITE ") == nullptr, "no loop back to the already-tried plain leg");
        Check(m.Terminated(), "terminated after the upgraded attempt fails");
    }

    // ---- a bare 380 (no emergency indication) is an error, never sos
    {
        imsd::util::Rng rng(57);
        CallMachine m(c, rng, "ims-call-23", "0612345678", 50004, false);
        m.Start();
        auto a = m.OnInviteResponse(Alt380(m.CallId(), ""));
        Check(Sent(a, "INVITE ") == nullptr, "bare 380 places no emergency call");
        const Action* t = Find(a, Action::Type::State);
        Check(t && t->reason == "error", "bare 380 -> error");
        Check(m.Terminated(), "terminated on bare 380");
    }

    // ---- Abandon (emergency preemption): terminate bookkeeping only
    {
        imsd::util::Rng rng(59);
        CallMachine m(c, rng, "ims-call-24", "1233", 50004, false);
        m.Start();
        m.OnHangup();                                    // CANCEL sent, awaiting 487
        auto a = m.Abandon("preempted by emergency dial");
        Check(Sent(a, "BYE ") == nullptr && Sent(a, "CANCEL ") == nullptr, "Abandon sends nothing further");
        Check(Has(a, Action::Type::Deleted), "Abandon deletes the call");
        Check(m.Terminated(), "abandoned machine is terminated");
    }

    if (Failures == 0) std::println("Engine: all tests passed");
    return Failures;
}
