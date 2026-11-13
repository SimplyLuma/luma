// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Engine unit tests — the call state machine driven through recorded
// network dialogs. Outgoing: reliable-18x PRACK, 2xx ACK + media start,
// media-plane far-end hangup, local hang-up (CANCEL before answer, BYE
// after), the CANCEL/answer race, and busy/error finals. Incoming: 100+180
// on the INVITE (reliable when the caller requires 100rel), Accept -> 200
// with the SDP answer at the offer's payload types + media, ACK silence,
// reject -> 486, remote CANCEL -> 200+487, unusable offer -> 488 (naming
// what was offered), a landline gateway's G.711-only and AMR-NB offers
// ring and are answered at the offer's own payload types, ring timeout ->
// 480, BYE both ways. DTMF: the tones reach the media leg on an active
// call that negotiated telephone-event, and are dropped — with the reason
// — before the answer, after the call, without the negotiation or with a
// character that is no tone. Then RegRecovery, the registration recovery policy:
// the refusal and silence thresholds, local refusals, the reconnect and
// fresh backoffs, the throttle schedule, Retry-After, which waits a Dial
// may cut short, when the old registration counts as gone, the holds a
// restart takes over (Holds/Restore), and a binding the network terminated
// (OnBindingTerminated). Pure reducers, no I/O.
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
    // A 200 OK around an arbitrary SDP answer, Content-Length from it — a
    // message the shell's framing would deliver whole.
    std::string R200Sdp(std::string_view callid, std::string_view sdp) {
        return std::format(
            "SIP/2.0 200 OK\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45061;branch=z9hG4bKx\r\n"
            "From: <sip:001010123456789@ims.mnc001.mcc001.3gppnetwork.org>;tag=it\r\n"
            "To: <sip:1233@ims>;tag=remote7\r\n"
            "Call-ID: {}\r\n"
            "CSeq: 1 INVITE\r\n"
            "Contact: <sip:termgw@[2001:db8::9]:5060>\r\n"
            "Content-Type: application/sdp\r\n"
            "Content-Length: {}\r\n\r\n{}", callid, sdp.size(), sdp);
    }
    constexpr std::string_view SdpHead = "v=0\r\no=- 1 1 IN IP6 2001:db8::9\r\ns=-\r\nc=IN IP6 2001:db8::9\r\nt=0 0\r\n";
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
        Check(Sent(start, "INVITE ")->text.contains("\r\nFrom: <tel:+31611111111>;tag="), "MO INVITE From is our public (tel:) identity, not the IMSI IMPU");
        Check(!Sent(start, "INVITE ")->text.contains("From: <sip:001010123456789@"), "MO INVITE From never the temporary IMPU once an identity is known");
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
        Check(update && update->text.contains("m=audio 50004 RTP/AVP 97 99 98 100\r\n"), "UPDATE carries the INVITE's media offer");
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
        Check(media && !media->media.dtmfPt, "an answer without telephone-event: the leg has no DTMF pt");
        const Action* active = Find(a200, Action::Type::State);
        Check(active && active->state == "active" && active->reason == "accepted", "200 emits active/accepted");
        Check(m.State() == CallState::Active, "state active");

        // a retransmitted 200 (RFC 3261 13.3.1.4: the UAS repeats it until our
        // ACK arrives) is ACKed again and nothing else — no second media leg
        auto a200b = m.OnInviteResponse(R200(m.CallId()));
        Check(Sent(a200b, "ACK ") != nullptr, "retransmitted 200 -> ACK again");
        Check(!Has(a200b, Action::Type::StartMedia) && !Has(a200b, Action::Type::State), "retransmitted 200: no second media start, no state action");
        Check(m.State() == CallState::Active, "state still active after the retransmit");

        // DTMF on a call whose answer negotiated no telephone-event: dropped, said why
        auto adt = m.OnDtmf("1");
        Check(!Has(adt, Action::Type::SendDtmf), "no telephone-event negotiated: no SendDtmf");
        const Action* dtLog = Find(adt, Action::Type::Log);
        Check(dtLog && dtLog->text == "call ims-call-1: DTMF '1' dropped: no telephone-event negotiated for this call", "...and the line says so");

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

    // ---- DTMF, outgoing: the answer echoes our telephone-event/16000 at pt
    //      98; the tones go to the leg once active, in upper case, and are
    //      dropped before the answer, with a character that is no tone,
    //      and after the call
    {
        imsd::util::Rng rng(43);
        CallMachine m(c, rng, "ims-call-1d", "1233", 50004, false);
        m.Start();
        auto early = m.OnDtmf("1");
        Check(!Has(early, Action::Type::SendDtmf), "DTMF while dialing: not sent");
        const Action* earlyLog = Find(early, Action::Type::Log);
        Check(earlyLog && earlyLog->text == "call ims-call-1d: DTMF '1' dropped: the call is dialing", "...the line names the state");
        auto a200 = m.OnInviteResponse(R200Sdp(m.CallId(), std::format("{}m=audio 6004 RTP/AVP 97 98 100\r\na=rtpmap:97 AMR-WB/16000/1\r\na=rtpmap:98 telephone-event/16000\r\na=rtpmap:100 telephone-event/8000\r\n", SdpHead)));
        const Action* media = Find(a200, Action::Type::StartMedia);
        Check(media && media->media.dtmfPt == 98, "the leg carries the answer's telephone-event pt at the codec's clock (98, not the narrowband 100)");
        Check(media && media->media.dtmfRxPt == 98, "outgoing: the far end sends at our offer's number for that clock");
        Check(m.State() == CallState::Active, "active");
        auto sent = m.OnDtmf("12#*a");
        const Action* dt = Find(sent, Action::Type::SendDtmf);
        Check(dt && dt->text == "12#*A", "active + negotiated: SendDtmf with the tones, letters upper-cased");
        const Action* dtLog = Find(sent, Action::Type::Log);
        Check(dtLog && dtLog->text == "call ims-call-1d: DTMF '12#*A' -> media leg (telephone-event pt 98)", "...and the line names the pt");
        auto bad = m.OnDtmf("1x");
        Check(!Has(bad, Action::Type::SendDtmf), "a character that is no tone: nothing sent, not even the '1'");
        const Action* badLog = Find(bad, Action::Type::Log);
        Check(badLog && badLog->text == "call ims-call-1d: DTMF '1x' dropped: 'x' is not a tone", "...the line names the character");
        Check(m.OnDtmf("").empty(), "an empty string: nothing, silently");
        const std::string atMost(DtmfMaxTones, '1');
        auto most = m.OnDtmf(atMost);
        const Action* maxDt = Find(most, Action::Type::SendDtmf);
        Check(maxDt && maxDt->text == atMost, "a string of DtmfMaxTones tones goes");
        auto tooMany = m.OnDtmf(std::format("{}1", atMost));
        Check(!Has(tooMany, Action::Type::SendDtmf) && Find(tooMany, Action::Type::Log) && Find(tooMany, Action::Type::Log)->text == std::format("call ims-call-1d: DTMF of {} tones dropped: more than {}", DtmfMaxTones + 1, DtmfMaxTones), "one more: dropped, the line names the bound");
        m.OnHangup();
        auto late = m.OnDtmf("5");
        Check(!Has(late, Action::Type::SendDtmf) && Find(late, Action::Type::Log) && Find(late, Action::Type::Log)->text.ends_with("the call is terminated"), "DTMF after the call: dropped, the line names the state");
    }

    // ---- DTMF, outgoing, the answer renumbered telephone-event (RFC 3264
    //      allows it): we send with its number, the far end with our offer's
    {
        imsd::util::Rng rng(44);
        CallMachine m(c, rng, "ims-call-1e", "1233", 50004, false);
        m.Start();
        auto a200 = m.OnInviteResponse(R200Sdp(m.CallId(), std::format("{}m=audio 6004 RTP/AVP 97 101\r\na=rtpmap:97 AMR-WB/16000/1\r\na=rtpmap:101 telephone-event/16000\r\n", SdpHead)));
        const Action* media = Find(a200, Action::Type::StartMedia);
        Check(media && media->media.dtmfPt == 101 && media->media.dtmfRxPt == 98, "renumbered answer: send with 101, listen on our 98");
        // a narrowband answer: AMR at 99 with telephone-event/8000 at 102
        imsd::util::Rng rng2(45);
        CallMachine n(c, rng2, "ims-call-1f", "1233", 50004, false);
        n.Start();
        auto b200 = n.OnInviteResponse(R200Sdp(n.CallId(), std::format("{}m=audio 6004 RTP/AVP 99 102\r\na=rtpmap:99 AMR/8000/1\r\na=rtpmap:102 telephone-event/8000\r\n", SdpHead)));
        const Action* nbMedia = Find(b200, Action::Type::StartMedia);
        Check(nbMedia && nbMedia->media.codec == "AMR" && nbMedia->media.dtmfPt == 102 && nbMedia->media.dtmfRxPt == 100, "narrowband answer: send with its 102, listen on our offer's 100");
        // an answer numbering for itself that gives the audio our offer's
        // telephone-event number: the receive number is the answer's, never
        // the audio's (every downlink frame would be taken for an event)
        imsd::util::Rng rng3(46);
        CallMachine o(c, rng3, "ims-call-1g", "1233", 50004, false);
        o.Start();
        auto c200 = o.OnInviteResponse(R200Sdp(o.CallId(), std::format("{}m=audio 6004 RTP/AVP 100 101\r\na=rtpmap:100 AMR/8000/1\r\na=rtpmap:101 telephone-event/8000\r\n", SdpHead)));
        const Action* colMedia = Find(c200, Action::Type::StartMedia);
        Check(colMedia && colMedia->media.payloadType == 100 && colMedia->media.dtmfPt == 101 && colMedia->media.dtmfRxPt == 101, "the audio at our telephone-event number: listen on the answer's 101, not the audio's 100");
        auto again = m.OnInviteResponse(R200(m.CallId()));
        Check(!Find(again, Action::Type::StartMedia), "a retransmitted 200 starts no second leg");
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
        const Action* mtMedia = Find(aa, Action::Type::StartMedia);
        Check(mtMedia && mtMedia->media.dtmfPt == 105, "incoming: the leg carries the offer's telephone-event pt");
        Check(mtMedia && mtMedia->media.dtmfRxPt == 105, "incoming: the far end sends at the number we echoed");
        auto mtDtmf = m.OnDtmf("9");
        const Action* mtDt = Find(mtDtmf, Action::Type::SendDtmf);
        Check(mtDt && mtDt->text == "9", "incoming, accepted: the tones go to the leg");
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

    // an inbound INVITE around an arbitrary offer (the gateway shapes below)
    auto MtInviteSdp = [](std::string_view callid, std::string_view sdp) {
        return std::format(
            "INVITE sip:me SIP/2.0\r\nVia: SIP/2.0/UDP [x]:1;branch=z9hG4bKi\r\n"
            "From: <sip:+31612345678@ims;user=phone>;tag=caller1\r\n"
            "To: <sip:+31611111111@ims;user=phone>\r\n"
            "Call-ID: {}\r\nCSeq: 1 INVITE\r\n"
            "Contact: <sip:origgw@[2001:db8::9]:5060>\r\n"
            "Content-Type: application/sdp\r\nContent-Length: {}\r\n\r\n{}",
            callid, sdp.size(), sdp);
    };

    // ---- a landline caller: the PSTN gateway offers G.711 only, by static
    // payload type with no rtpmap (the shape that used to be 488'd). It
    // rings, and Accept answers PCMA at the offer's pt with narrowband DTMF.
    {
        imsd::util::Rng rng(29);
        std::string sdp =
            "v=0\r\no=- 5 5 IN IP6 2001:db8::30c\r\ns=-\r\n"
            "c=IN IP6 2001:db8::30c\r\nt=0 0\r\n"
            "m=audio 27864 RTP/AVP 8 0 101\r\na=rtpmap:101 telephone-event/8000\r\na=ptime:20\r\n";
        CallMachine m(c, rng, "ims-call-11", IncomingInvite{MtInviteSdp("mt-pcma", sdp)}, 50004);
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 488 Not Acceptable Here") == nullptr, "G.711 offer is not refused");
        Check(Responded(ai, "SIP/2.0 180 Ringing") != nullptr, "G.711 offer rings");
        Check(!m.Terminated(), "G.711 call alive after INVITE");
        auto aa = m.OnAccept();
        const Action* ok = Responded(aa, "SIP/2.0 200 OK");
        Check(ok != nullptr, "Accept answers 200");
        Check(ok && ok->text.contains("m=audio 50004 RTP/AVP 8 101\r\n"), "answer keeps the offer's PCMA + DTMF pts");
        Check(ok && ok->text.contains("a=rtpmap:8 PCMA/8000/1\r\n"), "answer names PCMA");
        Check(ok && ok->text.contains("a=rtpmap:101 telephone-event/8000\r\n"), "DTMF at the narrowband clock");
        Check(ok && !ok->text.contains("octet-align"), "no octet-align for G.711");
        const Action* med = Find(aa, Action::Type::StartMedia);
        Check(med && med->media.codec == "PCMA" && med->media.payloadType == 8 && !med->media.octetAlign, "media leg starts PCMA pt 8");
        Check(med && med->media.remoteIp == "2001:db8::30c" && med->media.remotePort == 27864, "media leg targets the gateway");
    }

    // ---- a gateway offering narrowband AMR (octet-aligned) + G.711: AMR wins
    {
        imsd::util::Rng rng(31);
        std::string sdp =
            "v=0\r\no=- 6 6 IN IP6 2001:db8::30c\r\ns=-\r\n"
            "c=IN IP6 2001:db8::30c\r\nt=0 0\r\n"
            "m=audio 27866 RTP/AVP 8 100 110\r\n"
            "a=rtpmap:100 AMR/8000/1\r\na=fmtp:100 octet-align=1;mode-set=0,2,4,7\r\n"
            "a=rtpmap:110 telephone-event/8000\r\n";
        CallMachine m(c, rng, "ims-call-12", IncomingInvite{MtInviteSdp("mt-amrnb", sdp)}, 50004);
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 180 Ringing") != nullptr, "AMR-NB offer rings");
        auto aa = m.OnAccept();
        const Action* ok = Responded(aa, "SIP/2.0 200 OK");
        Check(ok && ok->text.contains("m=audio 50004 RTP/AVP 100 110\r\n"), "answer picks AMR over PCMA");
        Check(ok && ok->text.contains("a=rtpmap:100 AMR/8000/1\r\n") && ok->text.contains("a=fmtp:100 octet-align=1; mode-set=0,2,4,7\r\n"), "answer mirrors octet-aligned AMR and its mode-set");
        const Action* med = Find(aa, Action::Type::StartMedia);
        Check(med && med->media.codec == "AMR" && med->media.payloadType == 100 && med->media.octetAlign, "media leg starts octet-aligned AMR pt 100");
    }

    // ---- a mobile caller offering everything: still AMR-WB (no regression)
    {
        imsd::util::Rng rng(33);
        std::string sdp =
            "v=0\r\no=- 7 7 IN IP6 2001:db8::310\r\ns=-\r\n"
            "c=IN IP6 2001:db8::310\r\nt=0 0\r\n"
            "m=audio 27868 RTP/AVP 116 107 8 100 111 110 13\r\n"
            "a=rtpmap:116 AMR-WB/16000/1\r\na=fmtp:116 mode-change-capability=2;max-red=0\r\n"
            "a=rtpmap:107 AMR-WB/16000/1\r\na=fmtp:107 octet-align=1;mode-change-capability=2;max-red=0\r\n"
            "a=rtpmap:100 AMR/8000/1\r\na=rtpmap:111 telephone-event/16000\r\n"
            "a=rtpmap:110 telephone-event/8000\r\na=rtpmap:13 CN/8000\r\n";
        CallMachine m(c, rng, "ims-call-13", IncomingInvite{MtInviteSdp("mt-mobile", sdp)}, 50004);
        m.OnInvite();
        auto aa = m.OnAccept();
        const Action* ok = Responded(aa, "SIP/2.0 200 OK");
        Check(ok && ok->text.contains("m=audio 50004 RTP/AVP 107 111\r\n"), "mobile caller answered with octet-aligned AMR-WB + wideband DTMF");
        const Action* med = Find(aa, Action::Type::StartMedia);
        Check(med && med->media.codec == "AMR-WB" && med->media.payloadType == 107, "media leg stays AMR-WB for a mobile caller");
    }

    // ---- offer we cannot serve (nothing we play): 488 before ringing, and
    // the log names what was offered
    {
        imsd::util::Rng rng(35);
        std::string sdp =
            "v=0\r\no=- 8 8 IN IP6 2001:db8::30c\r\ns=-\r\n"
            "c=IN IP6 2001:db8::30c\r\nt=0 0\r\n"
            "m=audio 27864 RTP/AVP 18 101\r\na=rtpmap:18 G729/8000\r\na=rtpmap:101 telephone-event/8000\r\n";
        CallMachine m(c, rng, "ims-call-14", IncomingInvite{MtInviteSdp("mt-g729", sdp)}, 50004);
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 488 Not Acceptable Here") != nullptr, "G729-only offer refused with 488");
        Check(Responded(ai, "SIP/2.0 180 Ringing") == nullptr, "no 180 for a 488");
        Check(m.Terminated(), "terminated on 488");
        bool named = false;
        for (const auto& x : ai)
            if (x.type == Action::Type::Log && x.text.contains("offered: 18 G729, 101 TELEPHONE-EVENT")) named = true;
        Check(named, "488 log names the offered codecs");
    }
    // ---- no audio line at all: 488 too
    {
        imsd::util::Rng rng(37);
        CallMachine m(c, rng, "ims-call-15", IncomingInvite{MtInviteSdp("mt-noaudio", "v=0\r\no=- 9 9 IN IP6 2001:db8::30c\r\ns=-\r\nc=IN IP6 2001:db8::30c\r\nt=0 0\r\n")}, 50004);
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 488 Not Acceptable Here") != nullptr, "offer without m=audio refused with 488");
        Check(m.Terminated(), "terminated on 488 (no audio line)");
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
        Check(bye && bye->text.contains("From: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org;user=phone>;tag="), "UAS BYE From is the INVITE's To URI (our tag), not the IMSI IMPU");
        Check(bye && !bye->text.contains("001010123456789@ims"), "UAS BYE carries no IMSI-derived identity");
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

    // ---- CODECS override, terminating side: the transcoding gateway's
    //      mixed offer (KPN 2026-09-08) answered with G.711 A-law because the
    //      list says so — the on-air narrowband bench path
    {
        imsd::util::Rng rng(51);
        std::string sdp =
            "v=0\r\no=LucentPCSF 1 1 IN IP6 2001:db8::309\r\ns=-\r\n"
            "c=IN IP6 2001:db8::309\r\nt=0 0\r\n"
            "m=audio 41166 RTP/AVP 8 0 100 96 101 13 106\r\n"
            "a=rtpmap:8 PCMA/8000\r\na=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:100 AMR/8000\r\na=fmtp:100 mode-change-period=2\r\n"
            "a=rtpmap:96 AMR-WB/16000\r\na=fmtp:96 mode-change-period=2\r\n"
            "a=rtpmap:101 telephone-event/8000\r\na=fmtp:101 0-15\r\n"
            "a=rtpmap:106 telephone-event/16000\r\na=fmtp:106 0-15\r\n"
            "a=sendrecv\r\na=ptime:20\r\na=maxptime:60\r\n";
        CallMachine m(c, rng, "ims-call-20", IncomingInvite{MtInviteSdp("mt-kpn-pcma", sdp)}, 50004, {"PCMA"});
        auto ai = m.OnInvite();
        Check(Responded(ai, "SIP/2.0 180 Ringing") != nullptr, "CODECS=PCMA: mixed offer rings");
        auto aa = m.OnAccept();
        const Action* ok = Responded(aa, "SIP/2.0 200 OK");
        Check(ok && ok->text.contains("m=audio 50004 RTP/AVP 8 101\r\n"), "CODECS=PCMA: answer is PCMA + narrowband DTMF at the offer's pts");
        Check(ok && ok->text.contains("a=rtpmap:8 PCMA/8000/1\r\n") && !ok->text.contains("AMR"), "CODECS=PCMA: no AMR in the answer");
        const Action* med = Find(aa, Action::Type::StartMedia);
        Check(med && med->media.codec == "PCMA" && med->media.payloadType == 8 && !med->media.octetAlign, "CODECS=PCMA: media leg starts G.711 A-law");

        // same offer, CODECS=AMR: narrowband AMR, bandwidth-efficient as offered
        imsd::util::Rng rng2(52);
        CallMachine m2(c, rng2, "ims-call-21", IncomingInvite{MtInviteSdp("mt-kpn-amr", sdp)}, 50004, {"AMR"});
        m2.OnInvite();
        auto aa2 = m2.OnAccept();
        const Action* ok2 = Responded(aa2, "SIP/2.0 200 OK");
        Check(ok2 && ok2->text.contains("m=audio 50004 RTP/AVP 100 101\r\n") && !ok2->text.contains("octet-align"), "CODECS=AMR: answer is AMR-NB BE + narrowband DTMF");
        const Action* med2 = Find(aa2, Action::Type::StartMedia);
        Check(med2 && med2->media.codec == "AMR" && med2->media.payloadType == 100 && !med2->media.octetAlign, "CODECS=AMR: media leg starts AMR-NB BE");

        // same offer, no override: unchanged — AMR-WB
        imsd::util::Rng rng3(53);
        CallMachine m3(c, rng3, "ims-call-22", IncomingInvite{MtInviteSdp("mt-kpn-default", sdp)}, 50004);
        m3.OnInvite();
        auto aa3 = m3.OnAccept();
        const Action* med3 = Find(aa3, Action::Type::StartMedia);
        Check(med3 && med3->media.codec == "AMR-WB" && med3->media.payloadType == 96, "no override: the same offer still lands on AMR-WB");
    }

    // ---- CODECS override, originating side: the INVITE offers only what the
    //      list names (G.711 = a landline-shaped offer toward the network)
    {
        imsd::util::Rng rng(54);
        CallMachine m(c, rng, "ims-call-23", "0101234567", 50004, false, false, {"PCMA", "PCMU"});
        auto start = m.Start();
        const Action* inv = Sent(start, "INVITE ");
        Check(inv && inv->text.contains("m=audio 50004 RTP/AVP 8 0 100\r\n"), "CODECS=PCMA,PCMU: INVITE offers G.711 only");
        Check(inv && !inv->text.contains("AMR"), "CODECS=PCMA,PCMU: no AMR in the offer");
        Check(inv && inv->text.contains("a=rtpmap:100 telephone-event/8000\r\n") && !inv->text.contains("telephone-event/16000"), "CODECS=PCMA,PCMU: narrowband DTMF only");
    }

    // ---- MO with the registered sip: identity learned (the normal case on a
    //      live network): From, CANCEL and BYE all carry it, never the IMSI
    {
        Context ca = MakeCtx();
        ca.aor = "sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org";
        imsd::util::Rng rng(61);
        CallMachine m(ca, rng, "ims-call-30", "1233", 50004, false);
        auto start = m.Start();
        const Action* inv = Sent(start, "INVITE ");
        Check(inv && inv->text.contains("\r\nFrom: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org>;tag="), "MO INVITE From is the registered sip: identity");
        auto cancel = m.OnHangup();
        const Action* can = Sent(cancel, "CANCEL ");
        Check(can && can->text.contains("\r\nFrom: <sip:+31611111111@ims.mnc001.mcc001.3gppnetwork.org>;tag="), "CANCEL From is the same identity");
        Check(can && !can->text.contains("001010123456789@ims"), "CANCEL carries no IMSI");
    }

    // ---- RegRecovery: when a registration stops working after bring-up.
    // The clock is ours: seconds, starting at 1000.
    {
        using F = RegRecovery::Failure;
        using S = RegRecovery::Step;
        auto reconnectAt = [](const RegRecovery& r, double now, double at) { auto p = r.Next(now); return p.step == S::Reconnect && p.at == at; };
        auto freshAt = [](const RegRecovery& r, double now, double at) { auto p = r.Next(now); return p.step == S::Fresh && p.at == at; };
        RegRecovery r;
        Check(!r.Fresh() && r.Reconnectable() && reconnectAt(r, 1000, 0), "the first step is a reconnect, at once");
        r.OnLocalRefusal(1000);
        Check(r.refusals == 0 && r.silentCycles == 0 && r.reconnectDelay == 5 && reconnectAt(r, 1000, 1005), "a local connect refusal: retried in 5 s, not counted, backoff untouched");
        // O2 2026-09-24, 09:29: the keepalive refresh refused (503), then a
        // reconnect's refresh refused again -> fresh, not a third refresh
        r.OnReconnectFailure(1000, F::Refused);
        Check(!r.Fresh() && reconnectAt(r, 1000, 1005), "first refusal: reconnect after 5 s, not yet an escalation");
        r.OnReconnectFailure(1005, F::Refused);
        Check(r.Fresh() && r.refusals == 2 && freshAt(r, 1005, 1015), "second refusal: escalated, the fresh attempt after the doubled backoff");
        Check(r.Reconnectable(), "…and the old SA is still there");
        r.OnSuccess();
        Check(!r.Fresh() && r.refusals == 0 && r.reconnectDelay == 5, "success resets the policy");
        // Retry-After is the network's word: it holds every attempt for its
        // own length, next to the backoff, and a Dial cuts only the backoff
        r.OnReconnectFailure(1000, F::Refused, 90);
        Check(r.Fresh() && r.Reconnectable() && freshAt(r, 1000, 1090), "a refusal with a Retry-After escalates at once: the fresh attempt behind the network's wait (O2 2026-10-08), the old SA kept");
        r.OnDial();
        Check(freshAt(r, 1000, 1090), "…and a Dial does not cut it");
        r.OnReconnectFailure(1090, F::Refused, 3);
        Check(r.Fresh() && freshAt(r, 1090, 1100), "a Retry-After shorter than the backoff does not shorten it");
        r.OnDial();
        Check(freshAt(r, 1090, 1093), "…but a Dial cuts the backoff down to it, no further");
        r.OnSuccess();
        // a 403 once a fresh registration is due: the old registration is gone
        {
            RegRecovery k;
            k.OnReconnectFailure(1000, F::Refused, 0, 403);
            Check(!k.Fresh() && k.Reconnectable(), "a first 403 is a refusal like the others");
            k.OnReconnectFailure(1005, F::Refused, 0, 403);
            Check(k.Fresh() && !k.Reconnectable() && freshAt(k, 1005, 1015), "a 403 once fresh is due: gone, the fresh attempt after the backoff");
            k.OnSuccess();
            Check(k.Reconnectable(), "success forgets it");
        }
        // a 401 to a refresh asks for re-authentication over temporary SAs,
        // which imsd does not do: the fresh registration at once, the old
        // SA kept (the network still holds the registration)
        {
            RegRecovery j;
            j.OnReconnectFailure(1000, F::Refused, 0, 401);
            Check(j.Fresh() && j.Reconnectable() && j.refusals == 1 && freshAt(j, 1000, 1005), "a 401 on the first refusal: the fresh attempt after the backoff");
            RegRecovery jr;
            jr.OnReconnectFailure(1000, F::Refused, 0, 503);
            Check(!jr.Fresh() && reconnectAt(jr, 1000, 1005), "a bare 503 (no Retry-After) is still the hiccup case: one more refresh first");
        }
        // O2 10:43: the flow killed 8 s after the 200, every SYN unanswered
        r.OnReconnectFailure(1000, F::Silent);
        Check(!r.Fresh() && reconnectAt(r, 1000, 1005), "first silent cycle: retry in 5 s");
        r.OnReconnectFailure(1005, F::Silent);
        Check(r.Fresh() && r.silentCycles == 2, "two silent cycles escalate");
        // a mix: one refusal + one silence keeps reconnecting (neither
        // threshold reached)
        RegRecovery m;
        m.OnReconnectFailure(1000, F::Refused);
        m.OnReconnectFailure(1000, F::Silent);
        Check(!m.Fresh(), "one refusal + one silence: still reconnecting");
        m.OnReconnectFailure(1000, F::Silent);
        Check(m.Fresh(), "…until a threshold is reached");
        // the reconnect backoff caps at 5 min
        RegRecovery b;
        for (int i = 0; i < 10; i++) b.OnReconnectFailure(1000, F::Silent);
        Check(b.reconnectDelay == 300 && b.Next(1000).at == 1300, "reconnect backoff caps at 300 s");
        // fresh attempts: silence backs off 30 s -> 10 min (a Dial may cut
        // it); a refusal or a throttle holds on the throttle schedule (21
        // min growing to 60), the network's word, and the old SA is
        // reconnected meanwhile
        RegRecovery f;
        f.OldGone();
        Check(f.Fresh() && !f.Reconnectable() && freshAt(f, 1000, 0), "the old registration gone: fresh now, nothing to reconnect to");
        f.OnFreshFailure(1000, F::Silent);
        Check(freshAt(f, 1000, 1030), "silent fresh attempt: 30 s");
        f.OnDial();
        Check(freshAt(f, 1000, 0), "…which a Dial cuts");
        f.OnFreshFailure(1030, F::Silent);
        Check(freshAt(f, 1030, 1090), "…then 60 s");
        double last = 0;
        for (int i = 0; i < 8; i++) { f.OnFreshFailure(2000, F::Silent); last = f.Next(2000).at - 2000; }
        Check(last == 600 && f.freshAttempts == 10, "silent fresh backoff caps at 600 s; attempts counted");
        RegRecovery g;
        g.OnReconnectFailure(1000, F::Refused); g.OnReconnectFailure(1000, F::Refused);   // escalated, backoff 5 then 10
        g.OnFreshFailure(1010, F::Refused);
        Check(g.Reconnectable() && reconnectAt(g, 1010, 1030) && g.freshHoldUntil == 1010 + 21 * 60, "refused fresh attempt: the old SA is reconnected after the backoff, the next fresh attempt held 21 min");
        Check(freshAt(g, 1010 + 21 * 60, 1010 + 21 * 60), "…and runs once the hold ends");
        g.OnDial();
        Check(g.Next(1010).step == S::Reconnect && g.Next(1010).at <= 1010, "a Dial meanwhile: the reconnect at once, never the held fresh attempt");
        g.OnFreshFailure(2000, F::Throttled);
        Check(g.freshHoldUntil == 2000 + 31 * 60, "throttled next: 31 min (×1.5)");
        g.OnFreshFailure(3000, F::Refused);
        Check(g.freshHoldUntil == 3000 + 46 * 60, "then 46 min");
        g.OnFreshFailure(4000, F::Refused);
        Check(g.freshHoldUntil == 4000 + 60 * 60, "capped at 60 min");
        g.OnFreshFailure(5000, F::Refused);
        Check(g.freshHoldUntil == 5000 + 60 * 60, "stays at 60 min");
        g.OnFreshFailure(6000, F::Refused, 7200);
        Check(g.freshHoldUntil == 6000 + 3600, "a Retry-After beyond an hour is cut to the hour, the schedule's own ceiling");
        RegRecovery ra;
        ra.OnFreshFailure(1000, F::Refused, 2700);
        Check(ra.freshHoldUntil == 3700, "a Retry-After longer than the schedule's wait holds for its own length");
        ra.OnFreshFailure(5000, F::Refused, 86400);
        Check(ra.freshHoldUntil == 5000 + 3600, "…a day's for an hour: the refusal is heard again then");
        RegRecovery rb;
        rb.OnReconnectFailure(1000, F::Refused, 86400);
        Check(freshAt(rb, 1000, 1000 + 3600), "a refresh's Retry-After is cut to the hour the same, the fresh attempt behind it");
        RegRecovery h;
        h.OnFreshFailure(1000, F::Refused, 60);
        Check(h.freshHoldUntil == 1060, "a refused fresh attempt with Retry-After 60 waits 60 s, not the schedule");
        h.OnFreshFailure(1000, F::Refused, 5);
        Check(h.freshHoldUntil == 1030, "…but never under the 30-s floor");
        Check(h.throttleMinutes == 46, "the schedule still advances underneath (21 -> 31 -> 46)");
        h.OnFreshFailure(1000, F::Silent, 600);
        Check(h.freshHoldUntil == 1030 && h.Next(1000).at == 1030, "Retry-After is a refusal's word; silence keeps its own backoff");
        Check(g.Fresh(), "a failed fresh attempt stays on the fresh step");
        g.OnSuccess();
        Check(!g.Fresh() && g.throttleMinutes == 21 && g.freshDelay == 30, "success resets the fresh schedule too");
        // an attempt that got past the SA setup took the old SAs with it
        {
            RegRecovery s;
            s.OnFreshFailure(1000, F::Silent, 0, /*saReplaced=*/true);
            Check(!s.Reconnectable() && freshAt(s, 1000, 1030), "no reply past the SA setup: the old registration is gone, the next fresh attempt on the short backoff");
            RegRecovery t;
            t.OnFreshFailure(1000, F::Refused, 0, /*saReplaced=*/true);
            Check(!t.Reconnectable() && freshAt(t, 1000, 1000 + 21 * 60), "refused past the SA setup: gone, and on the schedule");
        }
        // the network terminated our binding
        {
            RegRecovery n;
            n.OnBindingTerminated(1000, "expired");
            Check(n.Fresh() && !n.Reconnectable() && freshAt(n, 1000, 0), "expired: fresh now, nothing to reconnect to");
            RegRecovery o;
            o.OnBindingTerminated(1000, "probation", 300);
            Check(freshAt(o, 1000, 1300), "probation: after the network's retry-after");
            o.OnDial();
            Check(freshAt(o, 1000, 1300), "…which a Dial does not cut");
            RegRecovery p;
            p.OnBindingTerminated(1000, "probation");
            Check(freshAt(p, 1000, 1001), "probation without a retry-after: at least 1 s");
            RegRecovery q;
            q.OnBindingTerminated(1000, "rejected");
            Check(freshAt(q, 1000, 1000 + 21 * 60) && !q.Reconnectable(), "rejected: on the refusal schedule, a hold, the old SA not retried");
            q.OnBindingTerminated(1001, "rejected");
            q.OnBindingTerminated(2000, "unregistered");
            Check(q.freshHoldUntil == 1000 + 21 * 60 && q.throttleMinutes == 31, "the same termination reported again (another dialog, a retransmission): counted once");
            RegRecovery u;
            u.OnBindingTerminated(1000, "deactivated");
            u.OnFreshFailure(1000, F::Silent);
            u.OnBindingTerminated(1005, "deactivated");
            Check(freshAt(u, 1005, 1030), "…nor does a repeat cut the backoff of the attempt made since");
            RegRecovery v;
            v.OnFreshFailure(1000, F::Refused, 3000);
            v.OnBindingTerminated(1010, "rejected");
            Check(!v.Reconnectable() && freshAt(v, 1010, 4000) && v.throttleMinutes == 46, "a rejected binding never shortens the network's Retry-After");
            RegRecovery w;
            w.OnBindingTerminated(1000, "probation", 3000);
            w.OnBindingTerminated(1010, "probation", 60);
            Check(freshAt(w, 1010, 4000), "…nor a second probation report the first one's retry-after");
            RegRecovery x;
            x.OnBindingTerminated(1000, "probation", 86400);
            Check(freshAt(x, 1000, 1000 + 3600), "probation's retry-after is cut to the hour like a Retry-After");
            // terminated while a reconnect cycle waited for its re-REGISTER final: the cycle's failure is no news
            RegRecovery y;
            y.OnReconnectFailure(1000, F::Silent);
            y.OnBindingTerminated(1010, "expired");
            y.OnReconnectFailure(1011, F::Silent);
            Check(y.silentCycles == 1 && freshAt(y, 1011, 0), "the flow closed under the cycle: not a silent cycle, the fresh registration now");
            y.OnReconnectFailure(1012, F::Refused, 90, 503);
            Check(y.refusals == 0 && freshAt(y, 1012, 1102), "…a refusal with a Retry-After on it: not counted, the network's hold kept");
        }
        // a terminated binding's verdict outlives the process with its hold
        {
            RegRecovery tg;
            tg.OnBindingTerminated(1000, "rejected");
            auto htg = tg.Holds(1100);
            Check(htg.Any() && htg.oldGone && htg.freshHold == 1000 + 21 * 60 - 1100, "a terminated binding's holds carry that the old registration is gone");
            RegRecovery th;
            th.Restore(50, htg);
            Check(!th.Reconnectable() && freshAt(th, 50, 50 + htg.freshHold), "…and the restart does not reconnect over its SAs: the fresh attempt waits what was left");
            RegRecovery ti;
            ti.OnBindingTerminated(1000, "probation", 1800);
            RegRecovery tj;
            tj.Restore(0, ti.Holds(1300));
            Check(!tj.Reconnectable() && freshAt(tj, 0, 1500), "probation's retry-after survives a restart the same way");
        }
        // the network's holds outlive the process
        {
            RegRecovery a;
            a.OnReconnectFailure(1000, F::Refused); a.OnReconnectFailure(1000, F::Refused);
            a.OnFreshFailure(1010, F::Throttled);
            auto h = a.Holds(1100);
            Check(h.Any() && h.hold == 0 && h.freshHold == 1010 + 21 * 60 - 1100 && h.throttleMinutes == 31, "holds: the seconds still to run and the schedule's place");
            RegRecovery b;
            b.Restore(50, h);
            Check(reconnectAt(b, 50, 0) && b.throttleMinutes == 31, "restored fresh hold: the resume (a reconnect) is free at once");
            b.OldGone();
            Check(freshAt(b, 50, 50 + h.freshHold), "…and the fresh step waits what was left of it");
            b.OnDial();
            Check(freshAt(b, 50, 50 + h.freshHold), "…which a Dial does not cut");
            RegRecovery c;
            c.OnReconnectFailure(1000, F::Refused, 600);
            RegRecovery d;
            d.Restore(10, c.Holds(1200));
            Check(reconnectAt(d, 10, 10 + 400), "restored Retry-After: no step before it ends");
            d.Restore(20, RegRecovery::Held{30, 0, 21});
            Check(reconnectAt(d, 20, 10 + 400), "a restore never shortens a hold");
            Check(!RegRecovery{}.Holds(5).Any(), "no hold, nothing to keep");
            auto k = a.Holds(1010 + 21 * 60 + 5);
            Check(k.Any() && k.hold == 0 && k.freshHold == 0 && k.throttleMinutes == 31, "the hold run out, the schedule's place is still kept");
            RegRecovery e;
            e.Restore(10, k);
            Check(reconnectAt(e, 10, 0) && e.throttleMinutes == 31, "…and a restart takes it over: no wait, the next refused or throttled fresh attempt on the advanced schedule");
            e.OldGone(); e.OnFreshFailure(20, F::Throttled);
            Check(freshAt(e, 20, 20 + 31 * 60), "…31 min, not 21");
            RegRecovery g;
            g.OnReconnectFailure(1000, F::Refused); g.OnReconnectFailure(1000, F::Refused, 0, 403);
            auto hg = g.Holds(1100);
            Check(hg.Any() && hg.oldGone, "a 403 once fresh is due: the holds carry that the old registration is gone");
            RegRecovery h2;
            h2.Restore(50, hg);
            Check(!h2.Reconnectable() && h2.Fresh() && freshAt(h2, 50, 0), "…and the restart does not reconnect over its SAs: fresh at once");
            RegRecovery i;
            i.OnFreshFailure(1000, F::Throttled, 0, true);
            auto hi = i.Holds(1100);
            RegRecovery j;
            j.Restore(50, hi);
            Check(hi.oldGone && !j.Reconnectable() && freshAt(j, 50, 50 + hi.freshHold), "an attempt past the SA setup: gone travels with the hold, the restart waits it out without resuming");
            RegRecovery m;
            m.OldGone();
            Check(m.Holds(5).Any() && m.Holds(5).oldGone && m.Holds(5).freshHold == 0, "gone with no hold left is still kept");
            RegRecovery n;
            n.Restore(5, m.Holds(5));
            Check(!n.Reconnectable() && freshAt(n, 5, 0), "…so the next start registers fresh at once instead of resuming");
            Check(!RegRecovery::Held{0, 0, 21, false}.Any(), "nothing gone, nothing held: nothing to keep");
            // the refusal count travels: a resume refused without a Retry-After at a start with no fresh target is one
            // refusal, the restart's is the second — without the travel every restart would be the first (F16's loop)
            RegRecovery rq;
            rq.OnReconnectFailure(10, F::Refused, 0, 503);
            auto hq = rq.Holds(20);
            Check(hq.Any() && hq.refusals == 1 && !hq.oldGone && hq.hold == 0, "a refusal without a Retry-After is held as a count, nothing else");
            RegRecovery sq;
            sq.Restore(100, hq);
            Check(!sq.Fresh() && sq.Reconnectable(), "the count alone gives nothing up");
            sq.OnReconnectFailure(100, F::Refused, 0, 503);
            Check(sq.Fresh() && sq.Reconnectable(), "the second refusal, across the restart, gives the resume up (fresh) without writing the registration off");
            RegRecovery tq;
            tq.OnReconnectFailure(10, F::Refused, 0, 401);
            Check(tq.Fresh() && tq.Reconnectable() && tq.Holds(20).refusals == 1, "a 401 gives the resume up at once; its count travels too");
            RegRecovery x;
            x.Restore(2000, RegRecovery::Held{86400, 86400, 21});
            Check(x.holdUntil == 2000 + 3600 && x.freshHoldUntil == 2000 + 3600, "a restored hold is cut to the hour like the Retry-After it came from");
        }
        // bring-up: a resume that failed leaves nothing to reconnect over
        {
            RegRecovery u;
            u.OldGone();
            u.OnFreshFailure(1000, F::Throttled);
            Check(!u.Reconnectable() && freshAt(u, 1000, 1000 + 21 * 60), "a throttled bring-up: the next fresh attempt in 21 min, nothing meanwhile");
            u.OnDial();
            Check(freshAt(u, 1000, 1000 + 21 * 60), "…a Dial changes nothing");
        }
    }

    if (Failures == 0) std::println("Engine: all tests passed");
    return Failures != 0;   // not the count: an exit status keeps 8 bits, and this suite has more checks than that
}
