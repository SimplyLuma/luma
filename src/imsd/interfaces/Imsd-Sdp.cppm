// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Sdp — AMR-WB SDP offer/answer building + parsing.

The offer is a fixed AMR-WB (octet-aligned) + telephone-event shape with an
optional GSMA IR.92 QoS-precondition block; it is what commercial IMS cores
expect from a VoLTE UE, and its exact bytes are pinned by tests/Sdp. The
parser extracts only what the media plane needs: remote address/port, the
negotiated payload type, and octet-align mode — it reads answers to our
offers and inbound offers alike (same fields either way). BuildAnswer is the
terminating side: it answers an inbound offer with the offer's own payload
type numbers (RFC 3264) and mirrors its octet-align mode (RFC 4867 requires
both directions to use one mode). Pure std C++.
*/

export module Imsd:Sdp;
import std;

namespace imsd::sdp {

    bool Is6(std::string_view addr) { return addr.contains(':'); }

    // First token (up to whitespace/CR/LF) starting at `pos`.
    std::string_view TokenAt(std::string_view body, std::size_t pos) {
        std::size_t end = body.find_first_of(" \t\r\n", pos);
        return body.substr(pos, end == std::string_view::npos ? std::string_view::npos : end - pos);
    }

    std::optional<int> ParseInt(std::string_view s) {
        int v = 0;
        auto [p, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
        if (ec != std::errc{} || p != s.data() + s.size()) return std::nullopt;
        return v;
    }

    // First occurrence of `needle` anywhere in the body; nullopt if absent.
    std::optional<std::size_t> Find(std::string_view body, std::string_view needle) {
        std::size_t at = body.find(needle);
        if (at == std::string_view::npos) return std::nullopt;
        return at;
    }
}

export namespace imsd::sdp {

    struct Offer {
        std::string local;              // connection address (v4 or v6)
        int rtpPort = 0;
        std::uint64_t sessionId = 0;    // o= sess-id/version; caller randomizes
        bool precond = false;           // IR.92 QoS preconditions block
        std::string currLocal = "none"; // initial INVITE has no reserved bearer
        std::string currRemote = "none";  // a=curr:qos remote <this>
        bool mandatory = true;          // mandatory for initial IR.92, optional for fallback UPDATE
    };

    // AMR-WB (octet-aligned) offer. Byte layout is pinned by tests/Sdp.
    std::string BuildOffer(const Offer& o) {
        std::string ipver = Is6(o.local) ? "IP6" : "IP4";
        std::string s;
        s += "v=0\r\n";
        s += std::format("o=- {} {} IN {} {}\r\n", o.sessionId, o.sessionId, ipver, o.local);
        s += "s=-\r\n";
        s += std::format("c=IN {} {}\r\n", ipver, o.local);
        s += "t=0 0\r\n";
        s += std::format("m=audio {} RTP/AVP 97 98\r\n", o.rtpPort);
        s += "b=AS:41\r\n";
        s += "b=RS:512\r\n";
        s += "b=RR:1536\r\n";
        s += "a=rtpmap:97 AMR-WB/16000/1\r\n";
        s += "a=fmtp:97 octet-align=1;mode-change-capability=2;max-red=0\r\n";
        s += "a=rtpmap:98 telephone-event/16000\r\n";
        s += "a=fmtp:98 0-15\r\n";
        s += "a=ptime:20\r\n";
        s += "a=maxptime:240\r\n";
        if (o.precond) {
            s += std::format("a=curr:qos local {}\r\n", o.currLocal);
            s += std::format("a=curr:qos remote {}\r\n", o.currRemote);
            std::string_view strength = o.mandatory ? "mandatory" : "optional";
            s += std::format("a=des:qos {} local sendrecv\r\n", strength);
            s += std::format("a=des:qos {} remote sendrecv\r\n", strength);
            s += "a=conf:qos remote sendrecv\r\n";
        }
        s += "a=sendrecv\r\n";
        return s;
    }

    struct Answer {
        std::string ip;                 // empty = no c= line found
        int port = -1;                  // -1 = no m=audio line found
        int payloadType = -1;
        bool octetAlign = false;
        std::string codec = "AMR-WB";
        // The offer's AMR mode-set, verbatim (e.g. "0,1,2"). RFC 4867 8.1
        // makes mode-set a property of the whole session, so an answer that
        // drops it does not confirm the restriction the offerer asked for.
        std::string modeSet;
    };

    // Payload-type preference: AMR-WB with octet-align=1 > AMR-WB > AMR with
    // octet-align=1 > AMR. Octet-aligned wins within a codec because it is
    // the media leg's native, call-proven format (bandwidth-efficient is
    // supported but was the s56 static-audio culprit) — when the peer offers
    // both variants, taking the octet-aligned pt keeps real calls on the
    // battle-tested path. With no rtpmap match at all, the first pt is used
    // and codec comes back EMPTY — the body named no AMR variant (e.g. a
    // PCMA offer), and pretending otherwise would defeat codec checks
    // upstream.
    Answer ParseAnswer(std::string_view body) {
        Answer a;
        if (auto at = Find(body, "c=IN IP6 ")) a.ip = TokenAt(body, *at + 9);
        else if (auto at4 = Find(body, "c=IN IP4 ")) a.ip = TokenAt(body, *at4 + 9);

        auto m = Find(body, "m=audio ");
        if (!m) return a;
        std::string_view portTok = TokenAt(body, *m + 8);
        auto port = ParseInt(portTok);
        // both the port and the " RTP/AVP " literal are required
        std::size_t afterPort = *m + 8 + portTok.size();
        constexpr std::string_view Avp = " RTP/AVP ";
        if (!port || !body.substr(afterPort).starts_with(Avp)) return a;
        a.port = *port;

        // payload-type list: digits + spaces up to CR/LF
        std::size_t ptsBegin = afterPort + Avp.size();
        std::size_t ptsEnd = body.find_first_not_of("0123456789 ", ptsBegin);
        std::string_view ptsRun = body.substr(ptsBegin, ptsEnd == std::string_view::npos ? std::string_view::npos : ptsEnd - ptsBegin);
        std::vector<int> pts;
        for (auto part : std::views::split(ptsRun, ' '))
            if (auto v = ParseInt(std::string_view(part))) pts.push_back(*v);
        if (pts.empty()) return a;

        auto hasOctetAlign = [&](int p) {
            auto fm = Find(body, std::format("a=fmtp:{} ", p));
            if (!fm) return false;
            std::size_t lineEnd = body.find_first_of("\r\n", *fm);
            std::string_view line = body.substr(*fm, lineEnd == std::string_view::npos ? std::string_view::npos : lineEnd - *fm);
            return line.contains("octet-align=1");
        };
        int wbOa = -1;
        int wb = -1;
        int nbOa = -1;
        int nb = -1;
        for (int p : pts) {
            std::string key = std::format("a=rtpmap:{} ", p);
            auto at = Find(body, key);
            if (!at) continue;
            // codec name: up to '/', CR or LF
            std::size_t nameBegin = *at + key.size();
            std::size_t nameEnd = body.find_first_of("/\r\n", nameBegin);
            std::string_view name = body.substr(nameBegin, nameEnd == std::string_view::npos ? std::string_view::npos : nameEnd - nameBegin);
            if (name.contains("AMR-WB")) {
                if (wb == -1) wb = p;
                if (wbOa == -1 && hasOctetAlign(p)) wbOa = p;
            } else if (name.contains("AMR")) {
                if (nb == -1) nb = p;
                if (nbOa == -1 && hasOctetAlign(p)) nbOa = p;
            }
        }
        if (wbOa != -1 || wb != -1) {
            a.payloadType = wbOa != -1 ? wbOa : wb;
            a.codec = "AMR-WB";
        } else if (nbOa != -1 || nb != -1) {
            a.payloadType = nbOa != -1 ? nbOa : nb;
            a.codec = "AMR";
        } else {
            a.payloadType = pts.front();
            a.codec.clear();
        }
        a.octetAlign = hasOctetAlign(a.payloadType);
        // mode-set from the chosen pt's fmtp line, if present.
        {
            std::string key = std::format("a=fmtp:{} ", a.payloadType);
            if (auto at = Find(body, key)) {
                std::size_t lineEnd = body.find_first_of("\r\n", *at);
                std::string_view line = body.substr(*at, lineEnd == std::string_view::npos ? std::string_view::npos : lineEnd - *at);
                if (auto ms = line.find("mode-set="); ms != std::string_view::npos) {
                    std::size_t vb = ms + 9;
                    std::size_t ve = line.find_first_not_of("0123456789,", vb);
                    a.modeSet = std::string(line.substr(vb, ve == std::string_view::npos ? std::string_view::npos : ve - vb));
                }
            }
        }
        return a;
    }

    // Payload type of `telephone-event/<clockRate>` in a body's rtpmap lines
    // (DTMF, RFC 4733); nullopt when the offer doesn't carry one.
    std::optional<int> TelephoneEventPt(std::string_view body, int clockRate) {
        std::string needle = std::format("telephone-event/{}", clockRate);
        std::size_t pos = 0;
        while ((pos = body.find("a=rtpmap:", pos)) != std::string_view::npos) {
            std::size_t ptBegin = pos + 9;
            std::size_t lineEnd = body.find_first_of("\r\n", ptBegin);
            std::size_t sp = body.find(' ', ptBegin);
            if (sp != std::string_view::npos && (lineEnd == std::string_view::npos || sp < lineEnd)) {
                auto pt = ParseInt(body.substr(ptBegin, sp - ptBegin));
                std::string_view name = body.substr(sp + 1, (lineEnd == std::string_view::npos ? body.size() : lineEnd) - sp - 1);
                if (pt && name.starts_with(needle)) return pt;
            }
            pos = ptBegin;
        }
        return std::nullopt;
    }

    struct AnswerSpec {
        std::string local;              // connection address (v4 or v6)
        int rtpPort = 0;
        std::uint64_t sessionId = 0;    // o= sess-id/version; caller randomizes
        int payloadType = 97;           // the offer's pt for the chosen codec
        bool octetAlign = true;         // mirror of the offer's mode
        std::string codec = "AMR-WB";   // "AMR-WB" or "AMR"
        std::optional<int> dtmfPt;      // the offer's telephone-event pt, if any
        std::string modeSet;            // echo of the offer's mode-set, if any
    };

    // SDP answer to an inbound audio offer: single codec at the offer's own
    // payload type, octet-align echoed only when the offer used it (absence
    // = bandwidth-efficient, RFC 4867 §8.1), telephone-event kept when
    // offered. Byte layout pinned by tests/Sdp.
    std::string BuildAnswer(const AnswerSpec& s) {
        std::string ipver = Is6(s.local) ? "IP6" : "IP4";
        int rate = s.codec == "AMR-WB" ? 16000 : 8000;
        std::string out;
        out += "v=0\r\n";
        out += std::format("o=- {} {} IN {} {}\r\n", s.sessionId, s.sessionId, ipver, s.local);
        out += "s=-\r\n";
        out += std::format("c=IN {} {}\r\n", ipver, s.local);
        out += "t=0 0\r\n";
        if (s.dtmfPt) out += std::format("m=audio {} RTP/AVP {} {}\r\n", s.rtpPort, s.payloadType, *s.dtmfPt);
        else
            out += std::format("m=audio {} RTP/AVP {}\r\n", s.rtpPort, s.payloadType);
        out += std::format("b=AS:{}\r\n", s.codec == "AMR-WB" ? 41 : 30);
        out += "b=RS:512\r\n";
        out += "b=RR:1536\r\n";
        out += std::format("a=rtpmap:{} {}/{}/1\r\n", s.payloadType, s.codec, rate);
        // RFC 4867 8.1/8.3.1: mode-set applies to the session, so mirror the
        // offer's value back. Omitting it leaves the restriction unconfirmed
        // and lets the peer configure its decoder from its own assumptions.
        if (s.octetAlign && !s.modeSet.empty())
            out += std::format("a=fmtp:{} octet-align=1; mode-set={}\r\n", s.payloadType, s.modeSet);
        else if (s.octetAlign)
            out += std::format("a=fmtp:{} octet-align=1\r\n", s.payloadType);
        else if (!s.modeSet.empty())
            out += std::format("a=fmtp:{} mode-set={}\r\n", s.payloadType, s.modeSet);
        if (s.dtmfPt) {
            out += std::format("a=rtpmap:{} telephone-event/{}\r\n", *s.dtmfPt, rate);
            out += std::format("a=fmtp:{} 0-15\r\n", *s.dtmfPt);
        }
        out += "a=ptime:20\r\n";
        out += "a=maxptime:240\r\n";
        out += "a=sendrecv\r\n";
        return out;
    }
}
