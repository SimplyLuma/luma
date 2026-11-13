// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Sdp — SDP offer/answer building + parsing for the voice media plane.

The offer is by default an AMR-WB (octet-aligned) + AMR + telephone-event
shape with an optional GSMA IR.92 QoS-precondition block; it is what
commercial IMS cores expect from a VoLTE UE, and its exact bytes are pinned by
tests/Sdp. The parser extracts only what the media plane needs: remote
address/port, the negotiated payload type, the codec and octet-align mode, and
the telephone-event payload type among the m-line's formats at that codec's
clock (DTMF, RFC 4733) — it
reads answers to our offers and inbound offers alike (same fields either way).
The codecs it knows are the ones imsd-media can play: AMR-WB, AMR (narrowband)
and G.711 (PCMA/PCMU) — the last two are what a PSTN gateway offers when a
landline calls. An explicit codec list (the daemon's CODECS override) replaces
both the offered set and the acceptance preference: it is how the narrowband
path is exercised against a network whose gateway transcodes every caller up
to AMR-WB. BuildAnswer is the terminating side: it answers an inbound
offer with the offer's own payload type numbers (RFC 3264) and mirrors its
octet-align mode (RFC 4867 requires both directions to use one mode). Pure
std C++.
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

    // The codec names imsd-media plays, in the default acceptance order:
    // wideband beats narrowband, AMR beats G.711 (a mobile-to-mobile call
    // must never land on a G.711 leg just because the gateway listed it).
    constexpr std::array<std::string_view, 4> kCodecs{"AMR-WB", "AMR", "PCMA", "PCMU"};

    // A codec preference list from its textual form ("PCMA, amr-nb"):
    // case-insensitive, spaces ignored, AMR-NB accepted for AMR, unknown
    // names and repeats dropped. Empty in = empty out = the defaults.
    std::vector<std::string> ParseCodecList(std::string_view spec) {
        std::vector<std::string> out;
        for (auto part : std::views::split(spec, ',')) {
            std::string n;
            for (char c : std::string_view(part))
                if (c != ' ' && c != '\t') n += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            if (n == "AMR-NB") n = "AMR";
            if (std::ranges::find(kCodecs, n) == kCodecs.end()) continue;
            if (std::ranges::find(out, n) == out.end()) out.push_back(n);
        }
        return out;
    }

    struct Offer {
        std::string local;              // connection address (v4 or v6)
        int rtpPort = 0;
        std::uint64_t sessionId = 0;    // o= sess-id/version; caller randomizes
        bool precond = false;           // IR.92 QoS preconditions block
        std::string currLocal = "none"; // initial INVITE has no reserved bearer
        std::string currRemote = "none";  // a=curr:qos remote <this>
        bool mandatory = true;          // mandatory for initial IR.92, optional for fallback UPDATE
        std::vector<std::string> codecs;  // offered codecs in order; empty = AMR-WB, AMR
    };

    // The payload type our offer gives telephone-event at a clock rate: the
    // number an answerer sends with (RFC 3264 §6.1), whatever the answer
    // renumbered ours to.
    int OfferedTelephoneEventPt(int clockRate) { return clockRate == 16000 ? 98 : 100; }

    // Our offer: AMR-WB (octet-aligned) first, AMR narrowband second — the
    // 3GPP-mandatory codec every IMS core and PSTN gateway can answer, so a
    // call we place TO a landline no longer depends on the far side
    // transcoding up to wideband — each with telephone-event at its own
    // clock. Byte layout is pinned by tests/Sdp. An explicit `codecs` list
    // offers exactly those, in that order, with fixed payload types (97
    // AMR-WB, 99 AMR, 8 PCMA, 0 PCMU) and telephone-event only at the clocks
    // in use. G.711 is not offered by default: a VoLTE UE offering PCMA
    // deviates from stock shapes for no gain.
    std::string BuildOffer(const Offer& o) {
        static const std::vector<std::string> kDefaultOffer{"AMR-WB", "AMR"};
        const std::vector<std::string>& codecs = o.codecs.empty() ? kDefaultOffer : o.codecs;
        bool wb = std::ranges::find(codecs, "AMR-WB") != codecs.end();
        bool amr = std::ranges::find(codecs, "AMR") != codecs.end();
        bool nb = codecs.size() > (wb ? 1u : 0u);   // anything besides AMR-WB is 8 kHz
        auto pt = [](const std::string& c) { return c == "AMR-WB" ? 97 : c == "AMR" ? 99 : c == "PCMA" ? 8 : 0; };
        std::string ipver = Is6(o.local) ? "IP6" : "IP4";
        std::string s;
        s += "v=0\r\n";
        s += std::format("o=- {} {} IN {} {}\r\n", o.sessionId, o.sessionId, ipver, o.local);
        s += "s=-\r\n";
        s += std::format("c=IN {} {}\r\n", ipver, o.local);
        s += "t=0 0\r\n";
        std::string pts;
        for (const auto& c : codecs) pts += std::format(" {}", pt(c));
        if (wb) pts += std::format(" {}", OfferedTelephoneEventPt(16000));
        if (nb) pts += std::format(" {}", OfferedTelephoneEventPt(8000));
        s += std::format("m=audio {} RTP/AVP{}\r\n", o.rtpPort, pts);
        s += std::format("b=AS:{}\r\n", wb ? 41 : amr ? 30 : 80);
        s += "b=RS:512\r\n";
        s += "b=RR:1536\r\n";
        for (const auto& c : codecs) {
            if (c == "AMR-WB") {
                s += "a=rtpmap:97 AMR-WB/16000/1\r\n";
                s += "a=fmtp:97 octet-align=1;mode-change-capability=2;max-red=0\r\n";
            } else if (c == "AMR") {
                s += "a=rtpmap:99 AMR/8000/1\r\n";
                s += "a=fmtp:99 octet-align=1;mode-change-capability=2;max-red=0\r\n";
            } else {
                s += std::format("a=rtpmap:{} {}/8000/1\r\n", pt(c), c);
            }
        }
        if (wb) {
            s += std::format("a=rtpmap:{} telephone-event/16000\r\n", OfferedTelephoneEventPt(16000));
            s += std::format("a=fmtp:{} 0-15\r\n", OfferedTelephoneEventPt(16000));
        }
        if (nb) {
            s += std::format("a=rtpmap:{} telephone-event/8000\r\n", OfferedTelephoneEventPt(8000));
            s += std::format("a=fmtp:{} 0-15\r\n", OfferedTelephoneEventPt(8000));
        }
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
        std::string codec = "AMR-WB";   // "AMR-WB" | "AMR" | "PCMA" | "PCMU" | "" (none we play)
        std::optional<int> dtmfPt;      // telephone-event at the codec's clock rate; nullopt = no DTMF
        // The offer's AMR mode-set, verbatim (e.g. "0,1,2"). RFC 4867 8.1
        // makes mode-set a property of the whole session, so an answer that
        // drops it does not confirm the restriction the offerer asked for.
        std::string modeSet;
    };

    // Sample rate of a codec we play: AMR-WB is 16 kHz, everything else
    // (AMR, G.711) is narrowband 8 kHz. Also the RTP clock rate.
    int CodecRate(std::string_view codec) { return codec == "AMR-WB" ? 16000 : 8000; }
    constexpr std::string_view Avp = " RTP/AVP ";   // after the m=audio port: the one profile we speak

    // The m=audio line's payload types in its order; empty without the line,
    // its port or the " RTP/AVP " literal.
    std::vector<int> AudioFormats(std::string_view body) {
        std::vector<int> pts;
        auto m = Find(body, "m=audio ");
        if (!m) return pts;
        std::string_view portTok = TokenAt(body, *m + 8);
        std::size_t afterPort = *m + 8 + portTok.size();
        if (!ParseInt(portTok) || !body.substr(afterPort).starts_with(Avp)) return pts;
        // digits + spaces up to CR/LF
        std::size_t ptsBegin = afterPort + Avp.size();
        std::size_t ptsEnd = body.find_first_not_of("0123456789 ", ptsBegin);
        std::string_view ptsRun = body.substr(ptsBegin, ptsEnd == std::string_view::npos ? std::string_view::npos : ptsEnd - ptsBegin);
        for (auto part : std::views::split(ptsRun, ' '))
            if (auto v = ParseInt(std::string_view(part))) pts.push_back(*v);
        return pts;
    }

    // A payload type's rtpmap encoding, `NAME/CLOCK[/CHANNELS]` upper-cased
    // (RFC 4566 §6: encoding names are case-insensitive); empty without one.
    std::string Rtpmap(std::string_view body, int pt) {
        const std::string key = std::format("a=rtpmap:{} ", pt);
        auto at = Find(body, key);
        if (!at) return "";
        std::size_t begin = *at + key.size();
        std::size_t end = body.find_first_of("\r\n", begin);
        std::string enc(body.substr(begin, end == std::string_view::npos ? std::string_view::npos : end - begin));
        for (char& c : enc)
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        return enc;
    }

    // Payload type of `telephone-event/<clockRate>` among the m=audio line's
    // formats (DTMF, RFC 4733); nullopt when the line lists none. An rtpmap
    // the line does not list is not negotiated: nothing is sent with a format
    // the answer does not carry (RFC 3264 §8.3.2). The clock is matched
    // whole: `/8000` is not `/80000`.
    std::optional<int> TelephoneEventPt(std::string_view body, int clockRate) {
        const std::string needle = std::format("TELEPHONE-EVENT/{}", clockRate);
        for (int pt : AudioFormats(body)) {
            const std::string enc = Rtpmap(body, pt);
            const bool whole = enc.size() == needle.size() || (enc.size() > needle.size() && (enc[needle.size()] == '/' || enc[needle.size()] == ' '));
            if (enc.starts_with(needle) && whole) return pt;
        }
        return std::nullopt;
    }

    // The encoding name an offer gives a payload type: its a=rtpmap line,
    // upper-cased (RFC 4566 §6: encoding names are case-insensitive), else
    // the static assignment for 0/8 (RFC 3551 §6 — a G.711 offer may carry
    // no rtpmap at all). Empty when neither applies.
    std::string CodecName(std::string_view body, int pt) {
        const std::string enc = Rtpmap(body, pt);
        if (!enc.empty()) return enc.substr(0, enc.find('/'));
        if (pt == 0) return "PCMU";
        if (pt == 8) return "PCMA";
        return "";
    }

    // The offer's audio payload types with their names, "8 PCMA, 96 AMR,
    // 101 telephone-event" — for the log line of a refused offer, so a
    // field journal says WHAT the gateway offered without a raw SIP dump.
    std::string OfferedCodecs(std::string_view body) {
        auto m = Find(body, "m=audio ");
        if (!m) return "no m=audio line";
        std::size_t lineEnd = body.find_first_of("\r\n", *m);
        std::string_view line = body.substr(*m, lineEnd == std::string_view::npos ? std::string_view::npos : lineEnd - *m);
        std::string out;
        int field = 0;
        for (auto part : std::views::split(line, ' ')) {
            std::string_view tok(part);
            if (field++ < 3 || tok.empty()) continue;  // m=audio <port> <proto> <pt>...
            auto pt = ParseInt(tok);
            if (!pt) continue;
            std::string name = CodecName(body, *pt);
            if (!out.empty()) out += ", ";
            out += name.empty() ? std::format("{}", *pt) : std::format("{} {}", *pt, name);
        }
        return out.empty() ? std::string(line) : out;
    }

    // Payload-type preference: AMR-WB with octet-align=1 > AMR-WB > AMR with
    // octet-align=1 > AMR > PCMA > PCMU (kCodecs order). Octet-aligned wins
    // within a codec because it is the media leg's native, call-proven
    // format (bandwidth-efficient is supported but was the s56 static-audio
    // culprit) — when the peer offers both variants, taking the
    // octet-aligned pt keeps real calls on the battle-tested path. A
    // non-empty `prefs` replaces the codec order (and restricts acceptance
    // to the codecs named) — the CODECS override. With no acceptable codec
    // at all, the first pt is used and codec comes back EMPTY — pretending
    // otherwise would defeat codec checks upstream. The telephone-event pt
    // is the one at the chosen codec's clock (an event's rate is its rtpmap
    // clock, RFC 4733 §2.4.1; an offer with two codec clocks lists
    // telephone-event at each), so a tone goes out on the stream's own clock.
    Answer ParseAnswer(std::string_view body, std::span<const std::string> prefs = {}) {
        Answer a;
        if (auto at = Find(body, "c=IN IP6 ")) a.ip = TokenAt(body, *at + 9);
        else if (auto at4 = Find(body, "c=IN IP4 ")) a.ip = TokenAt(body, *at4 + 9);

        auto m = Find(body, "m=audio ");
        if (!m) return a;
        std::string_view portTok = TokenAt(body, *m + 8);
        auto port = ParseInt(portTok);
        // both the port and the " RTP/AVP " literal are required
        std::size_t afterPort = *m + 8 + portTok.size();
        if (!port || !body.substr(afterPort).starts_with(Avp)) return a;
        a.port = *port;

        std::vector<int> pts = AudioFormats(body);
        if (pts.empty()) return a;

        auto hasOctetAlign = [&](int p) {
            auto fm = Find(body, std::format("a=fmtp:{} ", p));
            if (!fm) return false;
            std::size_t lineEnd = body.find_first_of("\r\n", *fm);
            std::string_view line = body.substr(*fm, lineEnd == std::string_view::npos ? std::string_view::npos : lineEnd - *fm);
            return line.contains("octet-align=1");
        };
        struct Seen { int first = -1; int oa = -1; };
        std::map<std::string, Seen> seen;   // codec name -> first pt, first octet-aligned pt
        for (int p : pts) {
            std::string name = CodecName(body, p);
            if (std::ranges::find(kCodecs, name) == kCodecs.end()) continue;
            Seen& s = seen[name];
            if (s.first == -1) s.first = p;
            if (s.oa == -1 && name.starts_with("AMR") && hasOctetAlign(p)) s.oa = p;
        }
        auto pick = [&](std::string_view name) {
            auto it = seen.find(std::string(name));
            if (it == seen.end()) return false;
            a.payloadType = it->second.oa != -1 ? it->second.oa : it->second.first;
            a.codec = std::string(name);
            return true;
        };
        bool found = false;
        if (prefs.empty()) {
            for (std::string_view n : kCodecs) if ((found = pick(n))) break;
        } else {
            for (const std::string& n : prefs) if ((found = pick(n))) break;
        }
        if (!found) {
            a.payloadType = pts.front();
            a.codec.clear();
        }
        a.octetAlign = a.codec.starts_with("AMR") && hasOctetAlign(a.payloadType);
        if (!a.codec.empty()) a.dtmfPt = TelephoneEventPt(body, CodecRate(a.codec));
        // mode-set from the chosen AMR pt's fmtp line, if present.
        if (a.codec.starts_with("AMR")) {
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

    struct AnswerSpec {
        std::string local;              // connection address (v4 or v6)
        int rtpPort = 0;
        std::uint64_t sessionId = 0;    // o= sess-id/version; caller randomizes
        int payloadType = 97;           // the offer's pt for the chosen codec
        bool octetAlign = true;         // mirror of the offer's mode (AMR codecs only)
        std::string codec = "AMR-WB";   // "AMR-WB" | "AMR" | "PCMA" | "PCMU"
        std::optional<int> dtmfPt;      // the offer's telephone-event pt, if any
        std::string modeSet;            // echo of the offer's mode-set, if any
    };

    // SDP answer to an inbound audio offer: single codec at the offer's own
    // payload type, octet-align echoed only when the offer used it (absence
    // = bandwidth-efficient, RFC 4867 §8.1; G.711 has no fmtp at all),
    // telephone-event kept when the offer's m-line lists one at the codec's
    // clock (ParseAnswer's rule). b=AS is the codec's peak payload
    // rate plus IP/UDP/RTP overhead at 20 ms. Byte layout pinned by tests/Sdp.
    std::string BuildAnswer(const AnswerSpec& s) {
        std::string ipver = Is6(s.local) ? "IP6" : "IP4";
        int rate = CodecRate(s.codec);
        int as = s.codec == "AMR-WB" ? 41 : (s.codec == "AMR" ? 30 : 80);
        std::string out;
        out += "v=0\r\n";
        out += std::format("o=- {} {} IN {} {}\r\n", s.sessionId, s.sessionId, ipver, s.local);
        out += "s=-\r\n";
        out += std::format("c=IN {} {}\r\n", ipver, s.local);
        out += "t=0 0\r\n";
        if (s.dtmfPt) out += std::format("m=audio {} RTP/AVP {} {}\r\n", s.rtpPort, s.payloadType, *s.dtmfPt);
        else
            out += std::format("m=audio {} RTP/AVP {}\r\n", s.rtpPort, s.payloadType);
        out += std::format("b=AS:{}\r\n", as);
        out += "b=RS:512\r\n";
        out += "b=RR:1536\r\n";
        out += std::format("a=rtpmap:{} {}/{}/1\r\n", s.payloadType, s.codec, rate);
        // RFC 4867 8.1/8.3.1: mode-set applies to the session, so mirror the
        // offer's value back. Omitting it leaves the restriction unconfirmed
        // and lets the peer configure its decoder from its own assumptions.
        if (s.codec.starts_with("AMR")) {
            if (s.octetAlign && !s.modeSet.empty())
                out += std::format("a=fmtp:{} octet-align=1; mode-set={}\r\n", s.payloadType, s.modeSet);
            else if (s.octetAlign)
                out += std::format("a=fmtp:{} octet-align=1\r\n", s.payloadType);
            else if (!s.modeSet.empty())
                out += std::format("a=fmtp:{} mode-set={}\r\n", s.payloadType, s.modeSet);
        }
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
