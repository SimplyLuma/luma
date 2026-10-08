// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Engine — the call state machine as a pure reducer.

A CallMachine owns one call's SIP dialog, outgoing or incoming. It never
touches a socket, a clock, or a subprocess: each event (the INVITE's
responses, an in-dialog request from the network, an accept/hang-up command,
the media leg exiting, a setup timeout) is a method that mutates the dialog
state and returns a list of Actions for the daemon shell to carry out — send
this on the client flow, answer the request on the channel it came in on,
start/stop the media leg, emit this D-Bus state change, arm this deadline.
That keeps the whole call FSM — PRACK on reliable 18x, ACK of 2xx vs non-2xx
finals, CANCEL/answer races, remote BYE, media-plane far-end-hangup, the
emergency attempt chain (urn:service:sos first for a classified number, one
fallback to a plain INVITE of the digits; a 380 Alternative Service upgrade
the other way), and the terminating (UAS) side: 100/180 on an inbound INVITE,
200-with-answer on Accept, 486 reject, the remote-CANCEL 200+487 pair —
unit-testable against recorded network dialogs with no I/O (tests/Engine).

State/reason strings are the frozen D-Bus vocabulary the dialer maps on
(dialing/ringing/incoming/active/terminated; outgoing/incoming/accepted/
local-hangup/remote-hangup/refused-or-busy/error). Pure std C++.
*/

export module Imsd:Engine;
import std;
import :Util;
import :Sip;
import :Sdp;
import :Messages;

export namespace imsd::engine {

    enum class CallState { Dialing, Ringing, Incoming, Active, Terminated };
    enum class Direction { Outgoing, Incoming };

    inline std::string_view StateStr(CallState s) {
        switch (s) {
            case CallState::Dialing:    return "dialing";
            case CallState::Ringing:    return "ringing";
            case CallState::Incoming:   return "incoming";
            case CallState::Active:     return "active";
            case CallState::Terminated: return "terminated";
        }
        return "terminated";
    }

    inline std::string_view DirectionStr(Direction d) {
        return d == Direction::Incoming ? "incoming" : "outgoing";
    }

    // Parsed SDP answer needed to bring up the media leg.
    struct MediaLeg {
        std::string remoteIp;
        int remotePort = 0;
        int payloadType = -1;
        bool octetAlign = false;
        std::string codec = "AMR-WB";
        std::string modeSet;
        // A provisional P-Early-Media: sendonly response authorizes downlink
        // early media, not microphone transmission before the peer answers.
        bool transmit = true;
        bool Valid() const { return !remoteIp.empty() && remotePort > 0 && payloadType >= 0; }
    };

    struct Action {
        enum class Type {
            SendClient,    // send `text` on the client TCP flow
            SendResponse,  // answer the just-handled inbound request on its origin channel
            StartMedia,    // bring up the media leg described by `media`
            StopMedia,     // tear the media leg down
            State,         // emit CallStateChanged(uni, `state`, `reason`)
            Deleted,       // emit CallDeleted(uni); drop this machine
            SetDeadline,   // arm a setup timer for `seconds`; fire calls OnDeadline()
            Log,
        };
        Type type;
        std::string text;
        std::string state;
        std::string reason;
        MediaLeg media;
        double seconds = 0;
    };

    // ring/answer budget, and how long to wait for a 487 after CANCEL — the
    // imsd.py INVITE_TIMEOUT / CANCEL_487_TIMEOUT constants.
    // Carriers normally terminate unanswered originating calls sooner, but a
    // generous local ceiling avoids racing delayed UI/assistive answer flows.
    // The network remains authoritative and any final response still ends the
    // transaction immediately.
    inline constexpr double InviteTimeout = 300.0;
    inline constexpr double Cancel487Timeout = 10.0;
    // How long an unanswered incoming call may ring before we 480 it — a
    // safety net behind the network's own CANCEL, so generous.
    inline constexpr double IncomingRingTimeout = 120.0;

    // Constructor tag: this machine is the UAS of the given inbound INVITE.
    struct IncomingInvite {
        std::string_view invite;
    };

    class CallMachine {
    public:
        // `emergency`: the shell classified the dialled string as an
        // emergency number — the first attempt targets urn:service:sos.
        CallMachine(const imsd::msg::Context& ctx, imsd::util::Rng& rng, std::string uni, std::string number, int rtpPort, bool precond, bool emergency = false)
            : ctx_(ctx), rng_(rng), uni_(std::move(uni)), number_(std::move(number)),
              rtpPort_(rtpPort), precond_(precond), attemptSos_(emergency) {
            ResetDialog();
        }

        CallMachine(const imsd::msg::Context& ctx, imsd::util::Rng& rng, std::string uni, IncomingInvite in, int rtpPort)
            : ctx_(ctx), rng_(rng), uni_(std::move(uni)), rtpPort_(rtpPort),
              precond_(false), direction_(Direction::Incoming),
              state_(CallState::Incoming), reason_("incoming"),
              invite_(in.invite) {
            number_ = imsd::sip::CallerId(invite_);
            d_.callid = std::string(imsd::sip::Header(invite_, "Call-ID").value_or(""));
            d_.itag = rng_.Token(10);        // the To tag we mint = our dialog tag
            d_.invBranch = rng_.Token(20);   // unused as UAS; kept initialized
            if (auto from = imsd::sip::Header(invite_, "From")) d_.dialogTo = std::string(*from);
            if (auto ct = imsd::sip::Header(invite_, "Contact")) d_.remoteTarget = AngleUri(*ct);
            d_.ruri = d_.remoteTarget.value_or("");
            // The UAS route set is the Record-Route in RECEIVED order
            // (RFC 3261 12.1.1); DialogRoute() reverses for the UAC case, so
            // store it reversed here to cancel that out.
            auto rr = imsd::sip::Headers(invite_, "Record-Route");
            for (std::size_t i = rr.size(); i-- > 0;)
                d_.recordRoute.emplace_back(rr[i]);
            answerSdp_ = std::string(Body(invite_));   // the caller's offer
            nextCseq_ = 0;                   // our first in-dialog request gets CSeq 1
        }

        const std::string& Uni() const { return uni_; }
        const std::string& Number() const { return number_; }
        const std::string& CallId() const { return d_.callid; }
        CallState State() const { return state_; }
        std::string_view StateStr() const { return engine::StateStr(state_); }
        Direction Dir() const { return direction_; }
        std::string_view DirectionStr() const { return engine::DirectionStr(direction_); }
        const std::string& Reason() const { return reason_; }
        bool Terminated() const { return state_ == CallState::Terminated; }

        // Build + queue the INVITE for the current attempt and arm the ring
        // timeout.
        std::vector<Action> Start() {
            if (attemptSos_) sosTried_ = true;
            else
                plainTried_ = true;
            imsd::sdp::Offer offer;
            offer.local = ctx_.local;
            offer.rtpPort = rtpPort_;
            offer.sessionId = rng_.UInt(1000000, 9999999);
            offer.precond = precond_;
            std::string sdp = imsd::sdp::BuildOffer(offer);
            std::vector<Action> a;
            a.push_back(Send(imsd::msg::BuildInvite(ctx_, d_, sdp, precond_, sessionExpires_)));
            a.push_back(Deadline(InviteTimeout));
            return a;
        }

        // UAS: answer the inbound INVITE with 100 Trying + 180 Ringing (made
        // reliable only when the caller Requires 100rel) and arm the ring
        // timeout. An offer we cannot serve — no audio line, or no AMR-WB
        // (the media plane is AMR-WB-only) — is refused with 488 up front,
        // before the UI ever rings.
        std::vector<Action> OnInvite() {
            std::vector<Action> a;
            if (direction_ != Direction::Incoming || state_ != CallState::Incoming) return a;
            MediaLeg leg = ParseAnswer();
            if (!leg.Valid() || leg.codec != "AMR-WB") {
                a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 488, "Not Acceptable Here", d_.itag)));
                Append(a, Finish("error", "incoming offer unusable (need AMR-WB audio); 488 sent"));
                return a;
            }
            a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 100, "Trying", d_.itag)));
            std::string require(imsd::sip::Header(invite_, "Require").value_or(""));
            if (require.contains("precondition")) a.push_back(LogAct("caller requires preconditions; answering without them"));
            std::vector<std::string> extra;
            if (require.contains("100rel")) {
                extra.push_back("Require: 100rel");
                extra.push_back("RSeq: 1");
            }
            last180_ = imsd::msg::BuildInviteResponse(ctx_, invite_, 180, "Ringing", d_.itag, std::nullopt, extra);
            a.push_back(Respond(last180_));
            a.push_back(Deadline(IncomingRingTimeout));
            return a;
        }

        // UAS: the user answered. 200 OK with the SDP answer (the offer's own
        // payload types, RFC 3264), media up, active. The caller's ACK needs
        // no action when it arrives (OnRequest ignores ACK).
        std::vector<Action> OnAccept() {
            std::vector<Action> a;
            if (direction_ != Direction::Incoming || state_ != CallState::Incoming) return a;
            MediaLeg leg = ParseAnswer();
            imsd::sdp::AnswerSpec spec;
            spec.local = ctx_.local;
            spec.rtpPort = rtpPort_;
            spec.sessionId = rng_.UInt(1000000, 9999999);
            spec.payloadType = leg.payloadType;
            spec.octetAlign = leg.octetAlign;
            spec.codec = leg.codec;
            spec.modeSet = leg.modeSet;
            spec.dtmfPt = imsd::sdp::TelephoneEventPt(Body(invite_), 16000);
            localSdp_ = imsd::sdp::BuildAnswer(spec);
            // session timers: echo the caller's Session-Expires; with no
            // refresher stated, make the caller (uac) the refresher — its
            // re-INVITE/UPDATE refreshes get 200d by OnRequest.
            std::vector<std::string> extra;
            if (auto se = imsd::sip::Header(invite_, "Session-Expires")) {
                std::string v(*se);
                if (!v.contains("refresher=")) v += ";refresher=uac";
                extra.push_back(std::format("Session-Expires: {}", v));
                extra.push_back("Require: timer");
            }
            a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 200, "OK", d_.itag, localSdp_, extra)));
            a.push_back(StartMediaAct(leg));
            state_ = CallState::Active;
            a.push_back(StateAct("accepted"));
            return a;
        }

        // A response to our INVITE (the shell already matched Call-ID + CSeq).
        std::vector<Action> OnInviteResponse(std::string_view msg) {
            std::vector<Action> a;
            if (direction_ == Direction::Incoming)
                return a;   // a UAS has no INVITE transaction of its own
            AbsorbDialog(msg);
            auto st = imsd::sip::Status(msg);
            if (!st) return a;
            int code = *st;
            if (code > 100 && code < 200) {
                if (state_ == CallState::Dialing) {
                    state_ = CallState::Ringing;
                    a.push_back(StateAct("outgoing"));
                }
                auto rseq = imsd::sip::Header(msg, "RSeq");
                std::string require = std::string(imsd::sip::Header(msg, "Require").value_or(""));
                if (rseq && require.contains("100rel") && !pracked_.contains(std::string(*rseq))) {
                    pracked_.insert(std::string(*rseq));
                    std::array<std::string, 1> extra = {
                        std::format("RAck: {} 1 INVITE", *rseq)};
                    a.push_back(Send(imsd::msg::BuildInDialog(ctx_, d_, "PRACK", nextCseq_, rng_.Token(20), extra)));
                    nextCseq_++;
                }
                // TS 24.229/RFC 5009: when the trusted network explicitly
                // authorizes early media and supplies an SDP answer, open the
                // RTP receive leg now. Besides delivering network ringback,
                // this establishes the IMS media flow before answer. Keep the
                // microphone closed until the final 2xx.
                std::string early = std::string(imsd::sip::Header(msg, "P-Early-Media").value_or(""));
                if (!earlyMediaStarted_ && (early.contains("sendonly") || early.contains("sendrecv"))) {
                    MediaLeg leg = ParseAnswer();
                    if (leg.Valid()) {
                        leg.transmit = false;
                        a.push_back(StartMediaAct(leg));
                        earlyMediaStarted_ = true;
                        a.push_back(LogAct("authorized downlink early-media leg started"));
                    }
                }
                return a;
            }
            if (code < 200)
                return a;  // 1xx other than 100<code<200 (only 100): nothing
            if (code == 200) {
                a.push_back(Send(imsd::msg::BuildAck2xx(ctx_, d_, rng_.Token(20), std::string(imsd::sip::Header(msg, "To").value_or("")))));
                if (cancelled_ && userCancelled_) {
                    // answered in the user's CANCEL race — we no longer want it
                    nextCseq_++;
                    a.push_back(Send(imsd::msg::BuildInDialog(ctx_, d_, "BYE", nextCseq_, rng_.Token(20))));
                    Append(a, Finish("local-hangup", "answered during our CANCEL; BYE sent"));
                    return a;
                }
                // An answer racing a DEADLINE-initiated CANCEL is still
                // wanted — the user never hung up. Matters most when the sos
                // attempt is answered at the wire just as the timeout fires:
                // BYEing an answering PSAP is the worst possible outcome.
                MediaLeg leg = ParseAnswer();
                if (earlyMediaStarted_)
                    a.push_back(Action{Action::Type::StopMedia});
                a.push_back(StartMediaAct(leg));
                earlyMediaStarted_ = false;
                state_ = CallState::Active;
                a.push_back(StateAct("accepted"));
                return a;
            }
            // non-2xx final
            a.push_back(Send(imsd::msg::BuildAckNon2xx(ctx_, d_, std::string(imsd::sip::Header(msg, "To").value_or("")))));
            std::string fin = WithReason(std::format("INVITE final {}", code), msg);
            // RFC 4028 7.4: 422 means our Session-Expires was below the
            // network's Min-SE, which the response carries. Re-offer once at
            // that minimum instead of failing the call. Without this the
            // AT&T-family core rejected every outgoing INVITE outright.
            if (code == 422 && !seRetried_) {
                if (auto minSe = imsd::sip::Header(msg, "Min-SE")) {
                    int v = 0;
                    auto sv = *minSe;
                    std::size_t i = 0;
                    while (i < sv.size() && (sv[i] == ' ' || sv[i] == '\t')) i++;
                    while (i < sv.size() && sv[i] >= '0' && sv[i] <= '9') { v = v * 10 + (sv[i] - '0'); i++; }
                    if (v > 0) {
                        seRetried_ = true;
                        sessionExpires_ = v;
                        Append(a, RetrySameTarget(std::format("{} (Min-SE {})", fin, v)));
                        return a;
                    }
                }
            }
            if (auto sos = FallbackTo(code, msg)) {
                Append(a, Retry(*sos, fin));
                return a;
            }
            if (cancelled_ || code == 487) Append(a, Finish("local-hangup", fin));
            else if (code == 486 || code == 480 || code == 603) Append(a, Finish("refused-or-busy", fin));
            else
                Append(a, Finish("error", fin));
            return a;
        }

        // A reliable provisional response closes with 200(PRACK). For an
        // IMS call using QoS preconditions, that is the point at which the UE
        // reports its media resources ready in an UPDATE. The network then
        // releases alerting/final-answer signalling. Bound this to one UPDATE
        // per attempt so retransmitted PRACK responses cannot duplicate it.
        std::vector<Action> OnPrackResponse(std::string_view msg) {
            std::vector<Action> a;
            // A post-PRACK UPDATE is part of the QoS-precondition state
            // machine only. Sending one on an ordinary offer invents a new
            // offer while the INVITE transaction is still provisional; the
            // T-Mobile core leaves that transaction unanswered and never
            // delivers the final INVITE response.
            if (direction_ != Direction::Outgoing || !precond_ ||
                resourceUpdateSent_)
                return a;
            auto st = imsd::sip::Status(msg);
            if (!st || *st != 200 || answerSdp_.empty()) return a;
            imsd::sdp::Offer offer;
            offer.local = ctx_.local;
            offer.rtpPort = rtpPort_;
            offer.sessionId = rng_.UInt(1000000, 9999999);
            // Initial SDP declares both directions unavailable. After the
            // reliable 183/PRACK exchange, the live RTP path proves the local
            // media resources are ready; the peer remains pending until its
            // 200(UPDATE). This is the stock IR.92 precondition progression.
            offer.precond = precond_;
            offer.currLocal = "sendrecv";
            offer.currRemote = "none";
            offer.mandatory = true;
            std::string sdp = imsd::sdp::BuildOffer(offer);
            std::array<std::string, 1> extra = {
                precond_ ? "Supported: precondition, timer" : "Supported: timer"};
            a.push_back(Send(imsd::msg::BuildInDialog(
                ctx_, d_, "UPDATE", nextCseq_, rng_.Token(20), extra, sdp,
                "application/sdp")));
            nextCseq_++;
            resourceUpdateSent_ = true;
            a.push_back(LogAct("IMS media-resource UPDATE sent after PRACK"));
            return a;
        }

        // An in-dialog request from the network (the shell matched Call-ID).
        std::vector<Action> OnRequest(std::string_view msg) {
            std::vector<Action> a;
            std::string_view method = Method(msg);
            if (method == "ACK")
                return a;   // ACK is never responded to (RFC 3261 17.1.1.2)
            std::string_view body = Body(msg);
            if (body.contains("m=audio")) answerSdp_ = std::string(body);
            if (direction_ == Direction::Incoming && state_ == CallState::Incoming) {
                if (method == "CANCEL") {
                    // the caller gave up: 200 the CANCEL transaction, 487 the
                    // INVITE transaction (RFC 3261 9.2), done.
                    a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg)));
                    a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 487, "Request Terminated", d_.itag)));
                    Append(a, Finish("remote-hangup", WithReason("CANCEL before answer", msg)));
                } else if (method == "INVITE") {
                    // retransmitted INVITE (our provisional got lost): repeat it
                    a.push_back(Respond(last180_));
                } else {
                    // PRACK of our reliable 180, or anything else: 200 echo
                    a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg)));
                }
                return a;
            }
            if (method == "UPDATE" || method == "INVITE") {
                std::optional<std::string> ans;
                if (direction_ == Direction::Incoming) {
                    // session-refresh: re-state the answer we gave if the
                    // refresh carries an offer; a bodyless refresh gets a
                    // bodyless 200 (RFC 3311/4028)
                    if (body.contains("m=audio")) ans = localSdp_;
                } else {
                    imsd::sdp::Offer offer;
                    offer.local = ctx_.local;
                    offer.rtpPort = rtpPort_;
                    offer.sessionId = rng_.UInt(1000000, 9999999);
                    offer.precond = precond_;
                    offer.currRemote = "sendrecv";
                    ans = imsd::sdp::BuildOffer(offer);
                }
                // RFC 4028 §9: a 2xx to a refresh MUST echo Session-Expires,
                // or the refresher treats the session as unrefreshed. KPN's
                // B2BUA audits ~55 s into every call with such an UPDATE and
                // releases ("PT: Session timer expired") on a bare 200.
                std::vector<std::string> extra;
                if (auto se = imsd::sip::Header(msg, "Session-Expires")) {
                    std::string v(*se);
                    if (!v.contains("refresher=")) v += ";refresher=uac";
                    extra.push_back(std::format("Session-Expires: {}", v));
                    extra.push_back("Require: timer");
                }
                a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg, ans ? std::optional<std::string_view>(*ans) : std::nullopt, extra)));
            } else if (method == "BYE") {
                a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg)));
                Append(a, Finish("remote-hangup", WithReason("remote BYE", msg)));
            } else if (method == "CANCEL") {
                a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg)));
                Append(a, Finish("remote-hangup", WithReason("remote CANCEL", msg)));
            } else {
                a.push_back(Respond(imsd::msg::BuildResponse200(ctx_, msg)));
            }
            return a;
        }

        std::vector<Action> OnHangup() {
            std::vector<Action> a;
            if (direction_ == Direction::Incoming && state_ == CallState::Incoming) {
                // reject the unanswered call
                a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 486, "Busy Here", d_.itag)));
                Append(a, Finish("local-hangup", "rejected (486 sent)"));
                return a;
            }
            if (state_ == CallState::Active) {
                nextCseq_++;
                a.push_back(Send(imsd::msg::BuildInDialog(ctx_, d_, "BYE", nextCseq_, rng_.Token(20))));
                Append(a, Finish("local-hangup", "HangUp; BYE sent"));
            } else if (state_ == CallState::Dialing || state_ == CallState::Ringing) {
                // even when a deadline already CANCELled: the user's intent
                // also kills the emergency fallback chain
                userCancelled_ = true;
                if (!cancelled_) {
                    a.push_back(Send(imsd::msg::BuildCancel(ctx_, d_)));
                    cancelled_ = true;
                    a.push_back(Deadline(Cancel487Timeout));
                }
            }
            return a;
        }

        // The shell is abandoning this machine (an emergency dial preempts
        // it while it waits on a CANCEL's 487): terminate bookkeeping only —
        // the CANCEL already sent stands, and the late final for this
        // Call-ID is ignored by dispatch.
        std::vector<Action> Abandon(std::string_view detail) {
            return Finish("local-hangup", detail);
        }

        // The media subprocess exited. code 3 = the downlink RTP dried up
        // (far-end hangup); anything else = the leg died. Either way, if the
        // call was up we release it network-side and tear down.
        std::vector<Action> OnMediaExit(int code) {
            std::vector<Action> a;
            if (state_ != CallState::Active) return a;
            nextCseq_++;
            a.push_back(Send(imsd::msg::BuildInDialog(ctx_, d_, "BYE", nextCseq_, rng_.Token(20))));
            Append(a, Finish("remote-hangup", code == 3 ? "far-end hangup (downlink RTP stopped); BYE sent" : std::format("media leg died (code {}); BYE sent", code)));
            return a;
        }

        // A setup deadline fired (never armed once Active).
        std::vector<Action> OnDeadline() {
            std::vector<Action> a;
            if (state_ == CallState::Active) return a;
            if (direction_ == Direction::Incoming) {
                if (state_ == CallState::Incoming) {
                    // rang unanswered past the safety net; the missed call
                    // reads as remote-hangup, like a network CANCEL would
                    a.push_back(Respond(imsd::msg::BuildInviteResponse(ctx_, invite_, 480, "Temporarily Unavailable", d_.itag)));
                    Append(a, Finish("remote-hangup", "rang unanswered past the deadline (480 sent)"));
                }
                return a;
            }
            if (!cancelled_ && (state_ == CallState::Dialing || state_ == CallState::Ringing)) {
                a.push_back(Send(imsd::msg::BuildCancel(ctx_, d_)));
                cancelled_ = true;
                a.push_back(Deadline(Cancel487Timeout));
            } else if (auto sos = FallbackTo(0, {})) {
                // the 487 never came; an abandoned sos attempt still gets
                // its digits fallback rather than dying silently
                Append(a, Retry(*sos, "no final after CANCEL"));
            } else {
                Append(a, Finish("error", "setup deadline expired"));
            }
            return a;
        }

        // The shell couldn't send (socket error) — collapse the call.
        std::vector<Action> Fail(std::string_view reason) {
            std::vector<Action> a;
            Append(a, Finish(reason, "client-flow send failed"));
            return a;
        }

    private:
        const imsd::msg::Context& ctx_;
        imsd::util::Rng& rng_;
        std::string uni_, number_;
        int rtpPort_;
        bool precond_;
        Direction direction_ = Direction::Outgoing;
        CallState state_ = CallState::Dialing;
        std::string reason_ = "outgoing";
        std::string invite_;    // incoming only: the inbound INVITE verbatim
        std::string last180_;   // incoming only: our provisional, for retransmits
        std::string localSdp_;  // incoming only: the SDP answer we sent
        imsd::msg::Dialog d_;
        int nextCseq_ = 2;
        bool cancelled_ = false;
        // The emergency attempt chain (outgoing only). attemptSos_ is what
        // the CURRENT attempt targets; the tried flags bound the chain to
        // one step in each direction, never a loop.
        bool attemptSos_ = false;
        bool sosTried_ = false;
        bool plainTried_ = false;
        bool userCancelled_ = false;   // CANCEL came from OnHangup, not a deadline
        bool earlyMediaStarted_ = false;
        int sessionExpires_ = 1800;   // RFC 4028; raised by a 422 Min-SE
        bool seRetried_ = false;      // one 422 re-offer per call
        bool resourceUpdateSent_ = false;
        std::set<std::string> pracked_;
        std::string answerSdp_;

        // Fresh dialog identity for the current attempt. A fallback INVITE
        // after a final response is a new transaction AND a clean new dialog
        // attempt (fresh Call-ID) — the shell's dispatch then ignores late
        // responses to the abandoned attempt by Call-ID mismatch.
        void ResetDialog() {
            d_ = {};
            d_.ruri = attemptSos_ ? std::string(imsd::msg::SosUrn)
                                  : imsd::msg::RuriFor(number_, ctx_.id.domain);
            d_.callid = std::format("{}@{}", rng_.Token(20), ctx_.local);
            d_.itag = rng_.Token(10);
            d_.invBranch = rng_.Token(20);
            nextCseq_ = 2;
            pracked_.clear();
            answerSdp_.clear();
            cancelled_ = false;
            userCancelled_ = false;
            earlyMediaStarted_ = false;
            resourceUpdateSent_ = false;
        }

        // The emergency fallback chain — where the next attempt (if any)
        // points. `msg` may be empty (deadline paths).
        //  - a sos INVITE that fails for any reason other than the user
        //    hanging up is re-attempted as a plain INVITE of the dialled
        //    digits: the pre-emergency-support behavior, so classification
        //    can only improve on the status quo, never block a call the old
        //    code would have placed.
        //  - a plain INVITE answered 380 Alternative Service whose body
        //    names emergency (TS 24.229 5.1.6: the network classified a
        //    number we didn't) is re-attempted as urn:service:sos. A 380
        //    without that indication terminates as an error: promoting an
        //    arbitrary redirect would put a non-emergency call through to a
        //    PSAP.
        std::optional<bool> FallbackTo(int code, std::string_view msg) const {
            if (userCancelled_) return std::nullopt;
            if (attemptSos_) return plainTried_ ? std::nullopt : std::optional(false);
            if (code == 380 && !sosTried_ && Body(msg).contains("emergency")) return true;
            return std::nullopt;
        }

        // Switch the attempt and send its INVITE. Same uni: the dialer keeps
        // seeing one continuous call, back in "dialing".
        // Re-offer the same target after a correctable rejection (422). The
        // emergency/plain choice is unchanged -- only the offer is revised.
        std::vector<Action> RetrySameTarget(std::string_view why) {
            ResetDialog();
            state_ = CallState::Dialing;
            std::vector<Action> a;
            a.push_back(LogAct(std::format("call {}: {} — re-offering", uni_, why)));
            a.push_back(StateAct("outgoing"));
            Append(a, Start());
            return a;
        }

        std::vector<Action> Retry(bool sos, std::string_view why) {
            attemptSos_ = sos;
            ResetDialog();
            state_ = CallState::Dialing;
            std::vector<Action> a;
            a.push_back(LogAct(std::format("call {}: {} — retrying as {}", uni_, why, sos ? "emergency (urn:service:sos)" : "plain INVITE of the digits")));
            a.push_back(StateAct("outgoing"));
            Append(a, Start());
            return a;
        }

        static std::string_view Body(std::string_view msg) {
            std::size_t at = msg.find("\r\n\r\n");
            return at == std::string_view::npos ? std::string_view{}
                                                : msg.substr(at + 4);
        }
        static std::string_view Method(std::string_view msg) {
            std::size_t sp = msg.find(' ');
            return sp == std::string_view::npos ? msg : msg.substr(0, sp);
        }
        static std::optional<std::string> AngleUri(std::string_view v) {
            std::size_t lt = v.find('<');
            if (lt == std::string_view::npos) return std::string(v);
            std::size_t gt = v.find('>', lt);
            if (gt == std::string_view::npos) return std::string(v);
            return std::string(v.substr(lt + 1, gt - lt - 1));
        }

        // Fold a response's dialog-establishing headers into the dialog.
        void AbsorbDialog(std::string_view msg) {
            if (auto to = imsd::sip::Header(msg, "To"))
                if (to->contains("tag=")) d_.dialogTo = std::string(*to);
            auto rr = imsd::sip::Headers(msg, "Record-Route");
            if (!rr.empty()) {
                d_.recordRoute.clear();
                for (auto v : rr)
                    d_.recordRoute.emplace_back(v);
            }
            if (auto ct = imsd::sip::Header(msg, "Contact")) d_.remoteTarget = AngleUri(*ct);
            std::string_view body = Body(msg);
            if (body.contains("m=audio")) answerSdp_ = std::string(body);
        }

        MediaLeg ParseAnswer() const {
            imsd::sdp::Answer ans = imsd::sdp::ParseAnswer(answerSdp_);
            MediaLeg leg;
            leg.remoteIp = ans.ip;
            leg.remotePort = ans.port < 0 ? 0 : ans.port;
            leg.payloadType = ans.payloadType;
            leg.octetAlign = ans.octetAlign;
            leg.codec = ans.codec;
            leg.modeSet = ans.modeSet;
            return leg;
        }

        // Terminate: state change, media teardown, deletion — in that order,
        // so the UI leaves the call page before the (slow) media teardown.
        // One journald line per teardown — who ended the call and why
        // (the s59 60-second-drop hunt ran blind for lack of exactly
        // this; the network's Reason header named the cause all along).
        // `detail` carries the SIP-level cause when the caller knows it.
        std::vector<Action> Finish(std::string_view reason, std::string_view detail = "") {
            state_ = CallState::Terminated;
            reason_ = std::string(reason);
            std::vector<Action> a;
            a.push_back(LogAct(detail.empty() ? std::format("call {} ended: {}", uni_, reason) : std::format("call {} ended: {} — {}", uni_, reason, detail)));
            a.push_back(StateAct(reason));
            a.push_back(Action{Action::Type::StopMedia});
            a.push_back(Action{Action::Type::Deleted});
            return a;
        }

        // "remote BYE, Reason: SIP;cause=503;text=..." — the peer's own
        // stated cause, when it sent one.
        static std::string WithReason(std::string_view what, std::string_view msg) {
            auto r = imsd::sip::Header(msg, "Reason");
            return r ? std::format("{}, Reason: {}", what, *r) : std::string(what);
        }

        Action Send(std::string text) {
            return Action{Action::Type::SendClient, std::move(text)};
        }
        Action Respond(std::string text) {
            return Action{Action::Type::SendResponse, std::move(text)};
        }
        Action StateAct(std::string_view reason) {
            reason_ = std::string(reason);
            Action a{Action::Type::State};
            a.state = std::string(engine::StateStr(state_));
            a.reason = reason_;
            return a;
        }
        Action StartMediaAct(const MediaLeg& leg) {
            Action a{Action::Type::StartMedia};
            a.media = leg;
            return a;
        }
        Action Deadline(double s) {
            Action a{Action::Type::SetDeadline};
            a.seconds = s;
            return a;
        }
        Action LogAct(std::string text) {
            return Action{Action::Type::Log, std::move(text)};
        }
        static void Append(std::vector<Action>& into, std::vector<Action> more) {
            for (auto& m : more)
                into.push_back(std::move(m));
        }
    };
}
