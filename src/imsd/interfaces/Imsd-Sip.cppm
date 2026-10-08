// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Sip — SIP message text layer + TCP framing.

Deliberately a text layer, not a full RFC 3261 parser: the engine works on
whole messages as strings and pulls out exactly what it needs (headers,
status codes, the granted registration lifetime), which keeps every message
loggable and testable verbatim. Edge-case behavior (empty header values,
line anchoring, keepalive discards) is pinned by tests/Sip — a change that
flips one of those tests is a behavior change, not a cleanup.

Pure std C++ (no GLib).
*/

export module Imsd:Sip;
import std;

namespace imsd::sip {

    constexpr char AsciiLower(char c) {
        return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c;
    }

    constexpr bool IEqualsPrefix(std::string_view text, std::string_view prefix) {
        if (text.size() < prefix.size()) return false;
        for (std::size_t i = 0; i < prefix.size(); i++)
            if (AsciiLower(text[i]) != AsciiLower(prefix[i])) return false;
        return true;
    }

    // spaces, tabs, CR, LF off both ends
    constexpr std::string_view Trim(std::string_view s) {
        while (!s.empty() && (s.front() == ' ' || s.front() == '\t' || s.front() == '\r' || s.front() == '\n'))
            s.remove_prefix(1);
        while (!s.empty() && (s.back() == ' ' || s.back() == '\t' || s.back() == '\r' || s.back() == '\n'))
            s.remove_suffix(1);
        return s;
    }

    // Iterate \n-separated lines (any trailing CR stays on the line and is
    // stripped by Trim when a value is extracted).
    template <typename F>
    void ForEachLine(std::string_view msg, F&& f) {
        std::size_t pos = 0;
        while (pos <= msg.size()) {
            std::size_t nl = msg.find('\n', pos);
            std::string_view line = msg.substr(pos, nl == std::string_view::npos ? std::string_view::npos : nl - pos);
            if (!f(line)) return;
            if (nl == std::string_view::npos) return;
            pos = nl + 1;
        }
    }

    // One "Name: value" match: name anchored at line start (case-insensitive,
    // per RFC 3261 header names), immediately followed by the colon — no
    // whitespace before it — and at least one character (even just the CR)
    // after it. A bare "Name:\r" line matches with value ""; a "Name:" that
    // ends the buffer does not match. Matching never crosses the line
    // boundary: an empty-valued header yields "", NOT the next line's
    // content (a regex-based implementation whose whitespace class eats the
    // CRLF gets that wrong — pinned by the "bare header" test).
    std::optional<std::string_view> MatchHeaderLine(std::string_view line, std::string_view name) {
        if (!IEqualsPrefix(line, name)) return std::nullopt;
        if (line.size() <= name.size() || line[name.size()] != ':') return std::nullopt;
        std::string_view rest = line.substr(name.size() + 1);
        if (rest.empty()) return std::nullopt;
        return Trim(rest);
    }

    // First run of ASCII digits starting at `s`; nullopt if none.
    std::optional<long> LeadingDigits(std::string_view s) {
        std::size_t n = 0;
        while (n < s.size() && s[n] >= '0' && s[n] <= '9')
            n++;
        if (n == 0) return std::nullopt;
        long v = 0;
        auto [_, ec] = std::from_chars(s.data(), s.data() + n, v);
        if (ec != std::errc{}) return std::nullopt;
        return v;
    }
}

export namespace imsd::sip {

    // Value of the first `name:` header, trimmed; nullopt if absent.
    std::optional<std::string_view> Header(std::string_view msg, std::string_view name) {
        std::optional<std::string_view> out;
        ForEachLine(msg, [&](std::string_view line) {
            if (auto v = MatchHeaderLine(line, name)) {
                out = *v;
                return false;
            }
            return true;
        });
        return out;
    }

    // Every `name:` header value, in order.
    std::vector<std::string_view> Headers(std::string_view msg, std::string_view name) {
        std::vector<std::string_view> out;
        ForEachLine(msg, [&](std::string_view line) {
            if (auto v = MatchHeaderLine(line, name)) out.push_back(*v);
            return true;
        });
        return out;
    }

    // The 3-digit code of a "SIP/2.0 NNN ..." response start-line; nullopt
    // for requests / anything else. Only matches at the start of the message.
    std::optional<int> Status(std::string_view msg) {
        constexpr std::string_view Prefix = "SIP/2.0 ";
        if (!msg.starts_with(Prefix) || msg.size() < Prefix.size() + 3) return std::nullopt;
        std::string_view d = msg.substr(Prefix.size(), 3);
        for (char c : d)
            if (c < '0' || c > '9') return std::nullopt;
        int v = 0;
        std::from_chars(d.data(), d.data() + 3, v);
        return v;
    }

    // Where a UDP response to this request must go (RFC 3261 18.2.2): the
    // top Via's sent-by port — unless the Via carries rport, in which case
    // the caller should reply to the request's source port instead
    // (nullopt). Also nullopt when the Via has no explicit port (5060 is
    // the default, but our P-CSCF flows always carry one). Handles
    // "[v6]:port" and "host:port" sent-by forms.
    std::optional<int> ViaResponsePort(std::string_view msg) {
        auto via = Header(msg, "Via");
        if (!via) return std::nullopt;
        std::string_view v = *via;
        if (std::size_t sc = v.find(';'); sc != std::string_view::npos) {
            if (v.substr(sc).find("rport") != std::string_view::npos) return std::nullopt;
            v = v.substr(0, sc);
        }
        std::size_t colon;
        if (std::size_t br = v.rfind(']'); br != std::string_view::npos)
            colon = v.find(':', br);          // v6: port only after the ]
        else
            colon = v.rfind(':');             // v4/hostname (transport token
                                              // has no colon of its own)
        if (colon == std::string_view::npos) return std::nullopt;
        if (auto p = LeadingDigits(Trim(v.substr(colon + 1))))
            if (*p > 0 && *p < 65536) return static_cast<int>(*p);
        return std::nullopt;
    }

    // Registration lifetime granted by a REGISTER 200 OK: the Contact
    // header's expires= parameter (the registrar's answer for our binding),
    // else the Expires header; nullopt if absent. "expires=" is searched
    // anywhere in each Contact value, not parameter-parsed.
    std::optional<long> GrantedExpires(std::string_view msg) {
        for (std::string_view contact : Headers(msg, "Contact")) {
            std::size_t at = contact.find("expires=");
            if (at != std::string_view::npos)
                if (auto v = LeadingDigits(contact.substr(at + 8))) return v;
        }
        if (auto e = Header(msg, "Expires")) {
            std::string_view v = *e;
            if (!v.empty() && v.find_first_not_of("0123456789") == std::string_view::npos) return LeadingDigits(v);
        }
        return std::nullopt;
    }

    // Caller identity of an inbound request: the number/user of the first
    // P-Asserted-Identity (the network-asserted identity, RFC 3325), falling
    // back to From. Understands "<tel:+31612345678>", "<sip:+31612345678@
    // host;user=phone>" (user part) and bare values; returns "" if neither
    // header yields one. Anonymous callers come out as "anonymous".
    std::string CallerId(std::string_view msg) {
        auto extract = [](std::string_view v) -> std::optional<std::string> {
            std::string_view uri = v;
            std::size_t lt = v.find('<');
            if (lt != std::string_view::npos) {
                std::size_t gt = v.find('>', lt);
                if (gt != std::string_view::npos) uri = v.substr(lt + 1, gt - lt - 1);
            } else {
                std::size_t semi = v.find(';');
                if (semi != std::string_view::npos) uri = v.substr(0, semi);
                uri = Trim(uri);
            }
            if (uri.starts_with("tel:")) {
                std::string_view n = uri.substr(4);
                std::size_t semi = n.find(';');
                if (semi != std::string_view::npos) n = n.substr(0, semi);
                return std::string(n);
            }
            if (uri.starts_with("sip:") || uri.starts_with("sips:")) {
                std::string_view n = uri.substr(uri.find(':') + 1);
                std::size_t end = n.find_first_of("@;");
                if (end == std::string_view::npos) return std::nullopt;
                return std::string(n.substr(0, end));
            }
            return std::nullopt;
        };
        for (std::string_view h : Headers(msg, "P-Asserted-Identity"))
            if (auto n = extract(h)) return *n;
        if (auto f = Header(msg, "From"))
            if (auto n = extract(*f)) return *n;
        return "";
    }

    // SIP-over-TCP stream framing, transport-free: feed received bytes into
    // Append(), pull complete messages (headers + Content-Length body) out
    // of TryExtract(). Leading CRLFs (RFC 5626 keepalive pongs) are
    // discarded.
    class FrameBuffer {
    public:
        void Append(std::string_view data) { buf_.append(data); }

        // One complete SIP message off the front of the buffer, or nullopt
        // if more bytes are needed. Content-Length is located with the same
        // line-anchored matching rules as Header().
        std::optional<std::string> TryExtract() {
            std::size_t keep = buf_.find_first_not_of("\r\n");
            buf_.erase(0, keep == std::string::npos ? buf_.size() : keep);
            std::size_t idx = buf_.find("\r\n\r\n");
            if (idx == std::string::npos) return std::nullopt;
            std::string_view head = std::string_view(buf_).substr(0, idx);
            long clen = 0;
            if (auto v = Header(head, "Content-Length")) clen = LeadingDigits(*v).value_or(0);
            std::size_t total = idx + 4 + static_cast<std::size_t>(clen);
            if (buf_.size() < total) return std::nullopt;
            std::string msg = buf_.substr(0, total);
            buf_.erase(0, total);
            return msg;
        }

        std::size_t Size() const { return buf_.size(); }

    private:
        std::string buf_;
    };
}
