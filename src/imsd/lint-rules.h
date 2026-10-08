// SPDX-License-Identifier: LGPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
#pragma once

// This repo's house-style lint rules. Deliberately project-local — the rule
// set is NOT part of the crafter-build library, so downstream consumers are
// never nudged toward one team's style. Include from project.cpp AFTER
// `import std;` and `import Crafter.Build;` (the header uses both and has no
// includes of its own), then call AddProjectLintRules(cfg).
//
// Report-only rules are registered first so their file:line numbers always
// match the on-disk file; transforms run last because each one sees the
// previous transform's output, which can shift line numbers.
//
// All detection runs on ctx.CommentStripped() — comments and string/char
// literal bodies are blanked to spaces with newlines preserved, so byte
// offsets and line numbers in the stripped text are valid in ctx.content.
// Transforms use that property directly: find in stripped, edit in content.

namespace ProjectLint {

inline bool IsCppFile(const Crafter::LintContext& ctx) {
    std::string ext = ctx.file.extension().string();
    return ext == ".cpp" || ext == ".cppm" || ext == ".h";
}

inline std::vector<std::string_view> Lines(std::string_view text) {
    std::vector<std::string_view> lines;
    std::size_t start = 0;
    while (start <= text.size()) {
        std::size_t end = text.find('\n', start);
        if (end == std::string_view::npos) {
            if (start < text.size()) lines.push_back(text.substr(start));
            break;
        }
        lines.push_back(text.substr(start, end - start));
        start = end + 1;
    }
    return lines;
}

inline std::string_view Trim(std::string_view s) {
    while (!s.empty() && (s.front() == ' ' || s.front() == '\t' || s.front() == '\r')) s.remove_prefix(1);
    while (!s.empty() && (s.back() == ' ' || s.back() == '\t' || s.back() == '\r')) s.remove_suffix(1);
    return s;
}

inline bool IsWordChar(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
}

// Net '(' minus ')' on one stripped line.
inline std::int64_t ParenDelta(std::string_view s) {
    std::int64_t d = 0;
    for (char c : s) {
        if (c == '(') ++d;
        if (c == ')') --d;
    }
    return d;
}

inline bool IsPascalCase(std::string_view name) {
    return !name.empty() && name[0] >= 'A' && name[0] <= 'Z' && !name.contains('_');
}

inline bool IsCamelCase(std::string_view name) {
    // One trailing underscore is the member-shadowing-a-keyword convention
    // (requires_, label_) — allowed.
    if (name.ends_with('_')) name.remove_suffix(1);
    return !name.empty() && ((name[0] >= 'a' && name[0] <= 'z') || name[0] == '_') && name.find('_', 1) == std::string_view::npos;
}

// The identifier ending right before position `pos` (exclusive), or empty.
inline std::string_view WordBefore(std::string_view s, std::size_t pos) {
    std::size_t end = pos;
    while (end > 0 && (s[end - 1] == ' ' || s[end - 1] == '\t')) --end;
    std::size_t begin = end;
    while (begin > 0 && (IsWordChar(s[begin - 1]) || s[begin - 1] == '~')) --begin;
    return s.substr(begin, end - begin);
}

inline void AddProjectLintRules(Crafter::Configuration& cfg) {
    using Crafter::LintContext;

    // ---------------- report-only rules ----------------

    // License header: SPDX identifier on line 1, copyright on line 2, blank
    // line 3.
    cfg.AddLintRule("spdx-header", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        if (!ctx.Line(1).starts_with("// SPDX-License-Identifier:")) {
            ctx.Report(1, "first line must be // SPDX-License-Identifier: ...");
        }
        if (!ctx.Line(2).starts_with("// SPDX-FileCopyrightText:")) {
            ctx.Report(2, "second line must be // SPDX-FileCopyrightText: ...");
        }
        if (ctx.lines.size() >= 3 && !Trim(ctx.Line(3)).empty()) {
            ctx.Report(3, "third line must be blank (separates the license header)");
        }
    });

    cfg.AddLintRule("no-tabs", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        for (std::size_t n = 1; n <= ctx.lines.size(); ++n) {
            if (ctx.Line(n).contains('\t')) ctx.Report(n, "tab character (use spaces)");
        }
    });

    // Naming: functions/types PascalCase, variables camelCase, statics and
    // namespace-scope globals PascalCase. A line-based scope tracker decides
    // whether a declaration sits at namespace scope (global) or inside a
    // function/type (local/member). Heuristic by nature — report-only.
    cfg.AddLintRule("naming", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> lines = Lines(ctx.CommentStripped());

        enum class Scope { Namespace, Type, Function, Other };
        std::vector<Scope> stack;
        auto currentScope = [&]() { return stack.empty() ? Scope::Namespace : stack.back(); };

        static const std::regex typeDecl(R"(\b(?:class|struct|union)\s+(?:CRAFTER_API\s+)?([A-Za-z_]\w*))");
        static const std::regex enumDecl(R"(\benum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*))");
        static const std::regex usingDecl(R"(^\s*using\s+([A-Za-z_]\w*)\s*=)");
        static const std::regex varDecl(
            R"(^\s*((?:static|constexpr|const|inline|mutable|thread_local|export|CRAFTER_API)\s+)*)"
            R"((?:std::)?[A-Za-z_][\w:]*(?:<[^;={]*>)?(?:\s*[&*])*\s+([A-Za-z_]\w*)\s*(=|;|\{))");
        static const std::unordered_set<std::string_view> keywords = {
            "if", "for", "while", "switch", "catch", "return", "else", "do", "case", "goto",
            "new", "delete", "throw", "using", "namespace", "template", "typedef", "friend",
            "public", "private", "protected", "class", "struct", "enum", "union", "import",
            "module", "export", "break", "continue", "co_return", "co_await", "co_yield",
            "sizeof", "alignof", "decltype", "static_assert", "operator", "try", "requires",
        };

        // Cumulative paren depth at the start of each line: declaration and
        // function checks only run at depth 0, so wrapped parameter lists and
        // continuation lines (`) {` closers) never look like declarations.
        std::int64_t parenDepth = 0;
        for (std::size_t i = 0; i < lines.size(); ++i) {
            std::string_view trimmed = Trim(lines[i]);
            std::string lineStr(lines[i]);
            std::smatch m;
            bool atDepth0 = parenDepth == 0;
            parenDepth = std::max<std::int64_t>(0, parenDepth + ParenDelta(lines[i]));

            if (!trimmed.starts_with('#') && atDepth0) {
                // Type / alias names must be PascalCase.
                if (std::regex_search(lineStr, m, typeDecl) || std::regex_search(lineStr, m, enumDecl)) {
                    std::string name = m[1].str();
                    if (!keywords.contains(name) && !IsPascalCase(name)) {
                        ctx.Report(i + 1, std::format("type '{}' should be PascalCase", name));
                    }
                }
                if (std::regex_search(lineStr, m, usingDecl) && !IsPascalCase(m[1].str())) {
                    ctx.Report(i + 1, std::format("type alias '{}' should be PascalCase", m[1].str()));
                }

                // Function definitions: identifier before the first '(' on a
                // line that ends where a body opens or closes — a multi-line
                // def's trailing '{' or a one-liner's trailing '}'. Statement
                // calls with inline lambda arguments look similar
                // (`bool x = std::any_of(..., [](T v) { ... });`) but end in
                // ';' and/or carry '=' before the name — both excluded.
                // Lambdas ("](") and control keywords are skipped; ctors/
                // dtors pass the Pascal check by construction; `main` and
                // operators exempt.
                std::size_t bodyBrace = trimmed.find('{');
                bool functionShaped = trimmed.ends_with('{') || trimmed.ends_with('}');
                if (functionShaped && currentScope() != Scope::Function && !trimmed.starts_with("return")) {
                    std::size_t paren = trimmed.find('(');
                    if (paren != std::string_view::npos && paren > 0 && paren < bodyBrace) {
                        std::string_view name = WordBefore(trimmed, paren);
                        char before = trimmed[paren - 1];
                        bool looksLikeDef = !name.empty() && IsWordChar(before)
                            && !keywords.contains(name) && name != "main"
                            && !name.starts_with('~');
                        // Require a type token before the name (or a
                        // qualified Class::Name), with no '=' in front —
                        // plain calls and initializations don't match.
                        if (looksLikeDef) {
                            std::size_t nameStart = trimmed.rfind(name, paren);
                            std::string_view prefix = Trim(trimmed.substr(0, nameStart));
                            looksLikeDef = !prefix.empty() && !prefix.contains('=')
                                && (IsWordChar(prefix.back()) || prefix.back() == '>'
                                    || prefix.back() == '*' || prefix.back() == '&'
                                    || prefix.ends_with("::"));
                        }
                        if (looksLikeDef && !IsPascalCase(name)) {
                            ctx.Report(i + 1, std::format("function '{}' should be PascalCase", name));
                        }
                    }
                }

                // Variable declarations: camelCase locally, PascalCase for
                // statics and namespace-scope globals.
                if (std::regex_search(lineStr, m, varDecl)) {
                    std::string name = m[2].str();
                    std::string_view typeToken = Trim(std::string_view(lineStr).substr(0, static_cast<std::size_t>(m.position(2))));
                    std::string_view firstWord = typeToken.substr(0, typeToken.find_first_of(" \t<"));
                    if (!keywords.contains(firstWord) && !keywords.contains(name)) {
                        bool isStatic = lineStr.contains("static ");
                        // constexpr variables are compile-time constants —
                        // constant naming (PascalCase) like statics/globals.
                        bool isConstexpr = lineStr.contains("constexpr ");
                        bool global = currentScope() == Scope::Namespace;
                        if ((isStatic || global || isConstexpr) && !IsPascalCase(name)) {
                            ctx.Report(i + 1, std::format("{} '{}' should be PascalCase",
                                                          isStatic ? "static variable"
                                                          : isConstexpr ? "constexpr constant"
                                                          : "global variable", name));
                        } else if (!isStatic && !global && !isConstexpr && !IsCamelCase(name)) {
                            ctx.Report(i + 1, std::format("variable '{}' should be camelCase", name));
                        }
                    }
                }
            }

            // Scope tracking: classify each '{' opened on this line; pop on '}'.
            for (std::size_t c = 0; c < lines[i].size(); ++c) {
                if (lines[i][c] == '{') {
                    Scope kind = Scope::Other;
                    std::string_view upTo = Trim(lines[i].substr(0, c));
                    if (trimmed.starts_with("namespace") || upTo.contains("namespace ")) {
                        kind = Scope::Namespace;
                    } else if (std::regex_search(lineStr, typeDecl) || std::regex_search(lineStr, enumDecl)) {
                        kind = Scope::Type;
                    } else if (upTo.ends_with(')') || upTo.ends_with("const") || upTo.ends_with("noexcept")
                               || upTo.ends_with("->") || trimmed.starts_with("extern")) {
                        kind = Scope::Function; // function/lambda/control body — all non-global
                    }
                    stack.push_back(kind);
                } else if (lines[i][c] == '}') {
                    if (!stack.empty()) stack.pop_back();
                }
            }
        }
    });

    cfg.AddLintRule("enum-class", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> lines = Lines(ctx.CommentStripped());
        static const std::regex plainEnum(R"(\benum\s+(?!class\b|struct\b)[A-Za-z_])");
        for (std::size_t i = 0; i < lines.size(); ++i) {
            std::string lineStr(lines[i]);
            if (std::regex_search(lineStr, plainEnum)) {
                ctx.Report(i + 1, "use enum class instead of plain enum");
            }
        }
    });

    cfg.AddLintRule("no-iostream-print", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> lines = Lines(ctx.CommentStripped());
        for (std::size_t i = 0; i < lines.size(); ++i) {
            if (lines[i].contains("std::cout")) {
                ctx.Report(i + 1, "use std::println instead of std::cout");
            } else if (lines[i].contains("std::cerr") && lines[i].contains("<<")) {
                ctx.Report(i + 1, "use std::println(std::cerr, ...) instead of streaming to std::cerr");
            }
        }
    });

    // Prefer std::string / std::string_view over char*. OS interop stays:
    // getenv returns char*, argv is char**, extern "C" prototypes mirror C.
    cfg.AddLintRule("no-char-pointer", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> lines = Lines(ctx.CommentStripped());
        static const std::regex charPtr(R"(\bchar\s*\*)");
        for (std::size_t i = 0; i < lines.size(); ++i) {
            std::string lineStr(lines[i]);
            // C-interop stays char*: argv, getenv/setenv, dlerror, C APIs fed
            // by c_str()/data(), binary IO reinterpret_casts, extern "C"
            // prototypes. (extern " matches with the literal body blanked.)
            if (lineStr.contains("argv") || lineStr.contains("getenv") || lineStr.contains("setenv")
                || lineStr.contains("dlerror") || lineStr.contains("c_str") || lineStr.contains(".data(")
                || lineStr.contains("reinterpret_cast") || lineStr.contains("extern \"")) continue;
            if (std::regex_search(lineStr, charPtr)) {
                ctx.Report(i + 1, "prefer std::string / std::string_view over char*");
            }
        }
    });

    // String building via `+` with a literal operand → std::format. Plain
    // `a += b` accumulation (builder pattern) stays legal. Single-line chains
    // of simple operands (identifier / member / call chains, parenthesized
    // groups, literals) are REWRITTEN automatically; anything the operand
    // scanner can't prove safe — raw-string lines, ternaries, mixed
    // operators, multi-line expressions — is reported for a human instead.
    cfg.AddLintRule("format-concat", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        const std::string& code = ctx.CommentStripped();
        std::vector<std::string_view> stripped = Lines(code);

        // Walk one operand leftwards from `from` (exclusive). Returns the
        // operand's begin, or npos when the shape isn't a simple postfix
        // chain (then the whole chain bails to a report).
        auto operandLeft = [](std::string_view s, std::size_t from) -> std::size_t {
            std::size_t i = from;
            while (i > 0 && (s[i - 1] == ' ' || s[i - 1] == '\t')) --i;
            bool any = false;
            for (;;) {
                if (i == 0) break;
                char c = s[i - 1];
                if (c == ')' || c == ']') {
                    char open = c == ')' ? '(' : '[';
                    std::size_t depth = 0;
                    do {
                        --i;
                        if (s[i] == c) ++depth;
                        if (s[i] == open) --depth;
                        if (depth == 0) break;
                    } while (i > 0);
                    if (depth != 0) return std::string_view::npos;
                    any = true;
                    // A group directly preceded by an identifier (or another
                    // group) is a call/index postfix — keep consuming the
                    // callee: `path.string()` is ONE operand, not `()`.
                    if (i > 0 && (IsWordChar(s[i - 1]) || s[i - 1] == ')' || s[i - 1] == ']')) continue;
                } else if (c == '"') {
                    --i; // closing quote; interior is blanked, find the opener
                    while (i > 0 && s[i - 1] != '"') --i;
                    if (i == 0) return std::string_view::npos;
                    --i;
                    any = true;
                } else if (IsWordChar(c)) {
                    while (i > 0 && IsWordChar(s[i - 1])) --i;
                    any = true;
                } else if (c == '.' && any) {
                    --i;
                    continue;
                } else if (c == ':' && i > 1 && s[i - 2] == ':' && any) {
                    i -= 2;
                    continue;
                } else if (c == '>' && i > 1 && s[i - 2] == '-' && any) {
                    i -= 2;
                    continue;
                } else {
                    break;
                }
                // After a primary, only connectors continue the operand.
                if (i > 0 && (s[i - 1] == '.' || (s[i - 1] == ':' && i > 1 && s[i - 2] == ':')
                              || (s[i - 1] == '>' && i > 1 && s[i - 2] == '-'))) continue;
                break;
            }
            if (!any) return std::string_view::npos;
            // A unary operator, ternary, or other-precedence operator in
            // front means expression structure we don't reason about — bail.
            std::size_t b = i;
            while (b > 0 && (s[b - 1] == ' ' || s[b - 1] == '\t')) --b;
            if (b > 0 && std::string_view("!*&~-?:<>/%^|").contains(s[b - 1])) return std::string_view::npos;
            return i;
        };

        // Walk one operand rightwards from `from` (inclusive). Returns one
        // past the operand's end, or npos on bail.
        auto operandRight = [](std::string_view s, std::size_t from) -> std::size_t {
            std::size_t i = from;
            while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) ++i;
            bool any = false;
            for (;;) {
                if (i >= s.size()) break;
                char c = s[i];
                if (c == '(' || c == '[') {
                    char close = c == '(' ? ')' : ']';
                    std::size_t depth = 0;
                    while (i < s.size()) {
                        if (s[i] == c) ++depth;
                        if (s[i] == close && --depth == 0) { ++i; break; }
                        ++i;
                    }
                    if (depth != 0) return std::string_view::npos;
                    any = true;
                } else if (c == '"') {
                    ++i;
                    while (i < s.size() && s[i] != '"') ++i;
                    if (i >= s.size()) return std::string_view::npos;
                    ++i;
                    any = true;
                } else if (IsWordChar(c)) {
                    while (i < s.size() && IsWordChar(s[i])) ++i;
                    any = true;
                } else if (any && c == '.') {
                    ++i;
                    continue;
                } else if (any && c == ':' && i + 1 < s.size() && s[i + 1] == ':') {
                    i += 2;
                    continue;
                } else if (any && c == '-' && i + 1 < s.size() && s[i + 1] == '>') {
                    i += 2;
                    continue;
                } else {
                    break;
                }
                if (i < s.size() && (s[i] == '(' || s[i] == '[' || s[i] == '.'
                                     || (s[i] == ':' && i + 1 < s.size() && s[i + 1] == ':')
                                     || (s[i] == '-' && i + 1 < s.size() && s[i + 1] == '>'))) continue;
                break;
            }
            return any ? i : std::string_view::npos;
        };

        struct Edit { std::size_t begin; std::size_t end; std::string replacement; };
        std::vector<Edit> edits; // offsets into ctx.content
        std::size_t lineStart = 0;
        for (std::size_t li = 0; li < stripped.size(); ++li) {
            std::string_view line = stripped[li];
            std::size_t lineOff = lineStart;
            lineStart += line.size() + 1;
            std::string_view trimmed = Trim(line);
            if (trimmed.starts_with('#')) continue;
            // Raw strings defeat the comment stripper's quote tracking; any
            // literal-+ pattern on such a line is a manual fix.
            bool hasRaw = std::string_view(ctx.content).substr(lineOff, line.size()).contains("R\"");

            std::size_t searchFrom = 0;
            while (searchFrom < line.size()) {
                // A candidate `+` that isn't ++ / += and touches a literal.
                std::size_t plus = line.find('+', searchFrom);
                if (plus == std::string_view::npos) break;
                searchFrom = plus + 1;
                if (plus + 1 < line.size() && (line[plus + 1] == '+' || line[plus + 1] == '=')) { ++searchFrom; continue; }
                if (plus > 0 && line[plus - 1] == '+') continue;
                std::size_t leftEnd = plus;
                while (leftEnd > 0 && (line[leftEnd - 1] == ' ' || line[leftEnd - 1] == '\t')) --leftEnd;
                std::size_t rightBegin = plus + 1;
                while (rightBegin < line.size() && (line[rightBegin] == ' ' || line[rightBegin] == '\t')) ++rightBegin;
                bool literalAdjacent = (leftEnd > 0 && line[leftEnd - 1] == '"')
                    || (rightBegin < line.size() && line[rightBegin] == '"');
                if (!literalAdjacent) continue;
                if (hasRaw) {
                    ctx.Report(li + 1, "use std::format instead of string concatenation with + (raw-string line, fix manually)");
                    break;
                }

                // Expand to the full chain: operands joined by `+`.
                std::size_t chainBegin = operandLeft(line, plus);
                std::size_t chainEnd = operandRight(line, plus + 1);
                bool bail = chainBegin == std::string_view::npos || chainEnd == std::string_view::npos;
                while (!bail) {
                    std::size_t b = chainBegin;
                    while (b > 0 && (line[b - 1] == ' ' || line[b - 1] == '\t')) --b;
                    if (b > 0 && line[b - 1] == '+' && !(b > 1 && line[b - 2] == '+')) {
                        std::size_t prev = operandLeft(line, b - 1);
                        if (prev == std::string_view::npos) { bail = true; break; }
                        chainBegin = prev;
                    } else break;
                }
                while (!bail) {
                    std::size_t e = chainEnd;
                    while (e < line.size() && (line[e] == ' ' || line[e] == '\t')) ++e;
                    if (e < line.size() && line[e] == '+' && !(e + 1 < line.size() && (line[e + 1] == '+' || line[e + 1] == '='))) {
                        std::size_t next = operandRight(line, e + 1);
                        if (next == std::string_view::npos) { bail = true; break; }
                        chainEnd = next;
                    } else break;
                }
                if (bail) {
                    ctx.Report(li + 1, "use std::format instead of string concatenation with + (not auto-fixable)");
                    break;
                }

                // Split the chain at depth-0 '+' into parts; literals feed the
                // format string, everything else becomes an argument.
                std::string_view chain = line.substr(chainBegin, chainEnd - chainBegin);
                std::string_view rawChain = std::string_view(ctx.content).substr(lineOff + chainBegin, chainEnd - chainBegin);
                std::string fmt;
                std::vector<std::string_view> args;
                std::size_t partBegin = 0;
                std::int64_t depth = 0;
                bool inLit = false;
                for (std::size_t p = 0; p <= chain.size(); ++p) {
                    if (p < chain.size()) {
                        char c = chain[p];
                        if (c == '"') inLit = !inLit;
                        if (inLit) continue;
                        if (c == '(' || c == '[') ++depth;
                        if (c == ')' || c == ']') --depth;
                        if (!(c == '+' && depth == 0)) continue;
                    }
                    std::string_view part = Trim(chain.substr(partBegin, p - partBegin));
                    std::string_view rawPart = Trim(rawChain.substr(partBegin, p - partBegin));
                    if (part.starts_with('"') && part.ends_with('"') && part.size() >= 2
                        && std::count(part.begin(), part.end(), '"') == 2) {
                        for (char c : rawPart.substr(1, rawPart.size() - 2)) {
                            fmt += c;
                            if (c == '{') fmt += '{';
                            if (c == '}') fmt += '}';
                        }
                    } else if (part.contains('"') && part.contains('+')) {
                        bail = true; // nested concat inside an operand — human territory
                        break;
                    } else {
                        fmt += "{}";
                        args.push_back(rawPart);
                    }
                    partBegin = p + 1;
                }
                if (bail || args.empty()) {
                    ctx.Report(li + 1, "use std::format instead of string concatenation with + (not auto-fixable)");
                    break;
                }
                std::string replacement = std::format("std::format(\"{}\"", fmt);
                for (std::string_view a : args) replacement += std::format(", {}", a);
                replacement += ")";
                edits.push_back({lineOff + chainBegin, lineOff + chainEnd, std::move(replacement)});
                searchFrom = chainEnd;
            }
        }
        if (edits.empty()) return;
        std::string out = ctx.content;
        std::sort(edits.begin(), edits.end(), [](const Edit& a, const Edit& b) { return a.begin > b.begin; });
        for (const Edit& e : edits) out.replace(e.begin, e.end - e.begin, e.replacement);
        ctx.SetContent(std::move(out));
    });

    // ---------------- transforms (auto-fixed by `crafter-build format`) ----------------

    // short/int/long → fixed-width types. Lines mentioning main/argc/argv or
    // extern "C" keep their C-conventional ints.
    cfg.AddLintRule("fixed-width-types", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        const std::string& code = ctx.CommentStripped();
        struct Rep { std::size_t pos; std::size_t len; std::string to; };
        std::vector<Rep> reps;
        // Builtin integer type specifiers combine in any order (`unsigned
        // long`, `long unsigned int`, ...), so match whole RUNS of these
        // keywords and classify the run, rather than the words one by one.
        static const std::unordered_set<std::string_view> IntWords = {
            "unsigned", "signed", "short", "long", "int", "char",
        };
        std::size_t lineStart = 0;
        while (lineStart <= code.size()) {
            std::size_t lineEnd = code.find('\n', lineStart);
            if (lineEnd == std::string::npos) lineEnd = code.size();
            std::string_view line(code.data() + lineStart, lineEnd - lineStart);
            // `int main` / argc / argv keep their C-conventional type; so do
            // extern "C" prototypes (the literal body is blanked in the
            // stripped text, so match `extern "`).
            bool exempt = line.contains("int main") || line.contains("argc") || line.contains("argv")
                || line.contains("extern \"");
            std::size_t pos = 0;
            while (!exempt && pos < line.size()) {
                if (!IsWordChar(line[pos])) { ++pos; continue; }
                std::size_t wordEnd = pos;
                while (wordEnd < line.size() && IsWordChar(line[wordEnd])) ++wordEnd;
                if (!IntWords.contains(line.substr(pos, wordEnd - pos))) { pos = wordEnd; continue; }

                // Extend the run over consecutive specifier keywords.
                std::size_t runBegin = pos;
                std::size_t runEnd = wordEnd;
                bool hasUnsigned = false;
                bool hasSigned = false;
                bool hasShort = false;
                bool hasChar = false;
                bool hasLong = false;
                std::string_view nextWord;
                for (;;) {
                    std::string_view word = line.substr(pos, wordEnd - pos);
                    if (word == "unsigned") hasUnsigned = true;
                    else if (word == "signed") hasSigned = true;
                    else if (word == "short") hasShort = true;
                    else if (word == "char") hasChar = true;
                    else if (word == "long") hasLong = true;
                    runEnd = wordEnd;
                    pos = wordEnd;
                    while (pos < line.size() && (line[pos] == ' ' || line[pos] == '\t')) ++pos;
                    wordEnd = pos;
                    while (wordEnd < line.size() && IsWordChar(line[wordEnd])) ++wordEnd;
                    nextWord = line.substr(pos, wordEnd - pos);
                    if (!IntWords.contains(nextWord)) break;
                }
                pos = runEnd;

                // Bare `char` is text, not an integer — only signed/unsigned
                // char is byte arithmetic. `long double` is a floating type.
                if (hasChar && !hasUnsigned && !hasSigned) continue;
                if (nextWord == "double") continue;

                std::string_view width = hasChar ? "8" : hasShort ? "16" : hasLong ? "64" : "32";
                reps.push_back({lineStart + runBegin, runEnd - runBegin,
                                std::format("std::{}int{}_t", hasUnsigned ? "u" : "", width)});
            }
            lineStart = lineEnd + 1;
        }
        if (reps.empty()) return;
        std::string out = ctx.content;
        std::sort(reps.begin(), reps.end(), [](const Rep& a, const Rep& b) { return a.pos > b.pos; });
        for (const Rep& r : reps) out.replace(r.pos, r.len, r.to);
        ctx.SetContent(std::move(out));
    });

    // One declaration per statement. Auto-splits the simple initialized form
    // `Type a = x, b = y;`; anything with pointers/references, parens, or
    // templates in the declarators is only reported (splitting `int* a, b;`
    // would change b's type).
    cfg.AddLintRule("single-declaration", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> stripped = Lines(ctx.CommentStripped());
        // The declarator char class excludes quotes: string-literal bodies are
        // blanked in the stripped text, so reconstructing them would corrupt
        // the file — those lines are left alone.
        static const std::regex simpleMulti(
            R"(^(\s*)((?:std::)?[A-Za-z_][\w:]*)\s+([A-Za-z_]\w*\s*=\s*[^,;()<>*&"]+(?:,\s*[A-Za-z_]\w*\s*=\s*[^,;()<>*&"]+)+);\s*$)");
        std::string out;
        bool changed = false;
        for (std::size_t i = 0; i < stripped.size(); ++i) {
            std::string lineStr(stripped[i]);
            std::smatch m;
            std::string_view raw = i + 1 <= ctx.lines.size() ? ctx.Line(i + 1) : std::string_view{};
            if (!raw.contains("//") && !ctx.Suppressed("single-declaration", i + 1)
                && std::regex_match(lineStr, m, simpleMulti)) {
                std::string indent = m[1].str(), type = m[2].str(), decls = m[3].str();
                std::size_t start = 0;
                bool first = true;
                while (start < decls.size()) {
                    std::size_t comma = decls.find(',', start);
                    std::string_view d = Trim(std::string_view(decls).substr(start, comma == std::string::npos ? std::string::npos : comma - start));
                    if (!first) out += '\n';
                    out += std::format("{}{} {};", indent, type, d);
                    first = false;
                    if (comma == std::string::npos) break;
                    start = comma + 1;
                }
                changed = true;
            } else {
                out += raw;
            }
            if (i + 1 < stripped.size() || ctx.content.ends_with('\n')) out += '\n';
        }
        if (changed) ctx.SetContent(std::move(out));
    });

    // K&R braces: `{` on its own line after a `)`/else/do/try header joins
    // onto the header line. Standalone scope blocks (previous line ends with
    // `;`, `{`, a comment, …) are intentional and stay.
    cfg.AddLintRule("brace-style", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> stripped = Lines(ctx.CommentStripped());
        std::vector<std::string> outLines;
        outLines.reserve(ctx.lines.size());
        bool changed = false;
        for (std::size_t i = 0; i < ctx.lines.size(); ++i) {
            std::string_view trimmed = Trim(stripped[i]);
            if (trimmed == "{" && !outLines.empty()) {
                std::string_view prevTrim = i > 0 ? Trim(stripped[i - 1]) : std::string_view{};
                bool headerBefore = prevTrim.ends_with(')') || prevTrim == "else" || prevTrim == "do" || prevTrim == "try";
                bool hasComment = ctx.Line(i + 1).contains("//") || (i > 0 && ctx.Line(i).contains("//"));
                bool suppressed = ctx.Suppressed("brace-style", i) || ctx.Suppressed("brace-style", i + 1);
                if (headerBefore && !hasComment && !suppressed) {
                    std::string& prev = outLines.back();
                    while (!prev.empty() && (prev.back() == ' ' || prev.back() == '\t')) prev.pop_back();
                    prev += " {";
                    changed = true;
                    continue;
                }
            }
            outLines.emplace_back(ctx.Line(i + 1));
        }
        if (!changed) return;
        std::string out;
        for (std::size_t i = 0; i < outLines.size(); ++i) {
            out += outLines[i];
            if (i + 1 < outLines.size() || ctx.content.ends_with('\n')) out += '\n';
        }
        ctx.SetContent(std::move(out));
    });

    // Single-statement if bodies join onto the if line: `if (x)\n  y;` →
    // `if (x) y;` — when the condition's parens balance on one line, the body
    // is one `;`-terminated statement, neither line carries a comment, and
    // the joined line stays ≤ 250 columns.
    cfg.AddLintRule("if-single-line", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::vector<std::string_view> stripped = Lines(ctx.CommentStripped());
        static const std::regex ifHeader(R"(^\s*(?:\}?\s*else\s+)?if\s*\(.*\)\s*$)");
        std::vector<std::string> outLines;
        bool changed = false;
        for (std::size_t i = 0; i < ctx.lines.size(); ++i) {
            std::string lineStr(stripped[i]);
            if (i + 1 < ctx.lines.size() && std::regex_match(lineStr, ifHeader) && ParenDelta(stripped[i]) == 0) {
                std::string_view body = Trim(stripped[i + 1]);
                bool joinable = !body.empty() && body != "{" && !body.starts_with("if") && body.ends_with(';')
                    && !ctx.Line(i + 1).contains("//") && !ctx.Line(i + 2).contains("//")
                    && !ctx.Suppressed("if-single-line", i + 1) && !ctx.Suppressed("if-single-line", i + 2);
                std::string joined = std::string(ctx.Line(i + 1));
                while (!joined.empty() && (joined.back() == ' ' || joined.back() == '\t')) joined.pop_back();
                joined += " ";
                joined += Trim(ctx.Line(i + 2));
                if (joinable && joined.size() <= 250) {
                    outLines.push_back(std::move(joined));
                    ++i; // consume the body line
                    changed = true;
                    continue;
                }
            }
            outLines.emplace_back(ctx.Line(i + 1));
        }
        if (!changed) return;
        std::string out;
        for (std::size_t i = 0; i < outLines.size(); ++i) {
            out += outLines[i];
            if (i + 1 < outLines.size() || ctx.content.ends_with('\n')) out += '\n';
        }
        ctx.SetContent(std::move(out));
    });

    // Wrapped call arguments join back onto one line when the whole
    // expression fits in 250 columns. Lambda bodies (lines ending `{`),
    // comments, raw strings, and preprocessor lines are left alone.
    // Operator-style continuations (next line starts with && / || / |) join
    // under the same length limit.
    cfg.AddLintRule("wrap-join", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        // Fixpoint loop: a join can enable another (joining an inner paren
        // wrap balances the line an operator continuation hangs off), so one
        // pass is not idempotent. Iterate until a pass changes nothing; the
        // cap is a safety net — joins strictly reduce the line count, so
        // termination is guaranteed anyway.
        for (std::int32_t pass = 0; pass < 16; ++pass) {
        std::vector<std::string_view> stripped = Lines(ctx.CommentStripped());
        std::vector<std::string> outLines;
        bool changed = false;
        for (std::size_t i = 0; i < ctx.lines.size(); ++i) {
            std::string_view trimmed = Trim(stripped[i]);
            std::int64_t delta = ParenDelta(stripped[i]);
            bool candidate = delta > 0 && !trimmed.empty() && !trimmed.starts_with('#')
                && !trimmed.ends_with('{') && !ctx.Line(i + 1).contains("//") && !ctx.Line(i + 1).contains("R\"");
            if (candidate) {
                std::string joined(ctx.Line(i + 1));
                std::size_t j = i + 1;
                bool ok = true;
                while (delta > 0 && j < ctx.lines.size() && j - i <= 4) {
                    std::string_view next = Trim(stripped[j]);
                    // A `{`-ending line is fine when it closes the expression
                    // (a control header's `) {`); mid-expression it means a
                    // lambda body starts — leave those wrapped.
                    bool closes = delta + ParenDelta(stripped[j]) <= 0;
                    if (next.empty() || (next.ends_with('{') && !closes)
                        || ctx.Line(j + 1).contains("//") || ctx.Line(j + 1).contains("R\"")) {
                        ok = false;
                        break;
                    }
                    while (!joined.empty() && (joined.back() == ' ' || joined.back() == '\t')) joined.pop_back();
                    std::string_view fragment = Trim(ctx.Line(j + 1));
                    // No separator right after an opening paren or before a
                    // closing one — joining must not manufacture `( x` / `x )`.
                    if (!joined.ends_with('(') && !fragment.starts_with(')') && !fragment.starts_with(',')) joined += " ";
                    joined += fragment;
                    delta += ParenDelta(stripped[j]);
                    ++j;
                }
                for (std::size_t l = i + 1; ok && l <= j; ++l) ok = !ctx.Suppressed("wrap-join", l);
                if (ok && delta <= 0 && joined.size() <= 250) {
                    outLines.push_back(std::move(joined));
                    i = j - 1; // consumed through line j-1 (0-based i)
                    changed = true;
                    continue;
                }
            } else if (delta == 0 && i + 1 < ctx.lines.size() && !trimmed.starts_with('#')
                       && !ctx.Line(i + 1).contains("//") && !ctx.Line(i + 1).contains("R\"")) {
                auto isOpStart = [](std::string_view s) {
                    return s.starts_with("&&") || s.starts_with("||") || s.starts_with("| ");
                };
                // Operator-style continuation chain (`a\n && b\n && c`).
                // Joined when the WHOLE chain stays within 250 columns —
                // longer conditions legitimately wrap. Same mechanics as the
                // paren join: chain lines must be comment-free, raw-string
                // free, and paren-balanced.
                if (isOpStart(Trim(stripped[i + 1])) && !isOpStart(trimmed)) {
                    std::string joined(ctx.Line(i + 1));
                    std::size_t j = i + 1;
                    bool ok = true;
                    while (j < ctx.lines.size() && isOpStart(Trim(stripped[j]))) {
                        if (ParenDelta(stripped[j]) != 0 || ctx.Line(j + 1).contains("//") || ctx.Line(j + 1).contains("R\"")) {
                            ok = false;
                            break;
                        }
                        while (!joined.empty() && (joined.back() == ' ' || joined.back() == '\t')) joined.pop_back();
                        joined += " ";
                        joined += Trim(ctx.Line(j + 1));
                        ++j;
                    }
                    for (std::size_t l = i + 1; ok && l <= j; ++l) ok = !ctx.Suppressed("wrap-join", l);
                    if (ok && joined.size() <= 250) {
                        outLines.push_back(std::move(joined));
                        i = j - 1; // consumed the chain
                        changed = true;
                        continue;
                    }
                }
            }
            outLines.emplace_back(ctx.Line(i + 1));
        }
        if (!changed) return;
        std::string out;
        for (std::size_t i = 0; i < outLines.size(); ++i) {
            out += outLines[i];
            if (i + 1 < outLines.size() || ctx.content.ends_with('\n')) out += '\n';
        }
        ctx.SetContent(std::move(out));
        }
    });

    // No space padding inside parens: `( x` / `x )` → `(x` / `x)`. Only
    // intra-line (a line legitimately ends with '(' when a call wraps);
    // string/comment interiors are excluded via the stripped text.
    cfg.AddLintRule("paren-spacing", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        const std::string& code = ctx.CommentStripped();
        std::vector<std::pair<std::size_t, std::size_t>> cuts; // [begin, end) spans of spaces to delete
        for (std::size_t i = 0; i < code.size(); ++i) {
            if (code[i] == '(' ) {
                std::size_t j = i + 1;
                while (j < code.size() && code[j] == ' ') ++j;
                if (j > i + 1 && j < code.size() && code[j] != '\n' && code[j] != '\r') cuts.push_back({i + 1, j});
            } else if (code[i] == ')') {
                std::size_t j = i;
                while (j > 0 && code[j - 1] == ' ') --j;
                // Only single-space padding: multi-space runs are usually
                // deliberate alignment columns.
                if (j == i - 1 && j > 0 && code[j - 1] != '\n' && code[j - 1] != ',') cuts.push_back({j, i});
            }
        }
        if (cuts.empty()) return;
        std::string out = ctx.content;
        for (auto it = cuts.rbegin(); it != cuts.rend(); ++it) out.erase(it->first, it->second - it->first);
        ctx.SetContent(std::move(out));
    });

    cfg.AddLintRule("trim-trailing-ws", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        std::string out;
        out.reserve(ctx.content.size());
        for (std::size_t n = 1; n <= ctx.lines.size(); ++n) {
            std::string_view line = ctx.Line(n);
            // Byte fidelity on CRLF files: peel the \r, trim, put it back.
            bool crlf = line.ends_with('\r');
            if (crlf) line.remove_suffix(1);
            while (line.ends_with(' ') || line.ends_with('\t')) line.remove_suffix(1);
            out += line;
            if (crlf) out += '\r';
            // The last line only had a newline if the file ended with one —
            // preserve that byte exactly so this rule doesn't shadow
            // final-newline.
            if (n < ctx.lines.size() || ctx.content.ends_with('\n')) out += '\n';
        }
        ctx.SetContent(std::move(out));
    });

    cfg.AddLintRule("final-newline", [](LintContext& ctx) {
        if (!IsCppFile(ctx)) return;
        if (!ctx.content.empty() && !ctx.content.ends_with('\n')) {
            ctx.SetContent(ctx.content + '\n');
        }
    });
}

} // namespace ProjectLint
