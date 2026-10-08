package org.projectluma.connect.protocol

/**
 * Strict JSON matching `luma_continuity.transport`.
 *
 * Parsing rejects duplicate keys, non-finite constants and trailing data.
 * Encoding reproduces Python's `json.dumps(sort_keys=True, separators=(",", ":"))`
 * byte for byte for the value types the protocol uses (objects, arrays,
 * strings, integers, booleans and null). Floating-point values are refused so a
 * digest never depends on two languages agreeing about number formatting.
 */
object Json {
    fun encode(value: Any?): ByteArray = StringBuilder().also { write(it, value) }.toString().toByteArray(Charsets.US_ASCII)

    fun encodeToString(value: Any?): String = String(encode(value), Charsets.US_ASCII)

    fun parse(data: ByteArray): Any? = Parser(String(data, Charsets.UTF_8)).parseDocument()

    fun parse(text: String): Any? = Parser(text).parseDocument()

    @Suppress("UNCHECKED_CAST")
    fun parseObject(data: ByteArray): Map<String, Any?> =
        parse(data) as? Map<String, Any?> ?: throw ProtocolException("object required")

    private fun write(out: StringBuilder, value: Any?) {
        when (value) {
            null -> out.append("null")
            is Boolean -> out.append(if (value) "true" else "false")
            is Int, is Long, is Short, is Byte -> out.append(value.toString())
            is String -> writeString(out, value)
            is Map<*, *> -> {
                val keys = value.keys.map { it as? String ?: throw ProtocolException("object keys must be strings") }.sorted()
                out.append('{')
                keys.forEachIndexed { index, key ->
                    if (index > 0) out.append(',')
                    writeString(out, key)
                    out.append(':')
                    write(out, value[key])
                }
                out.append('}')
            }
            is Iterable<*> -> {
                out.append('[')
                value.forEachIndexed { index, item ->
                    if (index > 0) out.append(',')
                    write(out, item)
                }
                out.append(']')
            }
            is Array<*> -> write(out, value.asList())
            else -> throw ProtocolException("unsupported JSON value ${value::class.simpleName}")
        }
    }

    private fun writeString(out: StringBuilder, value: String) {
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '' -> out.append("\\f")
                else -> if (ch.code < 0x20 || ch.code > 0x7e) {
                    out.append("\\u").append(String.format("%04x", ch.code))
                } else {
                    out.append(ch)
                }
            }
        }
        out.append('"')
    }

    private class Parser(private val text: String) {
        private var index = 0

        fun parseDocument(): Any? {
            val value = parseValue(0)
            skipWhitespace()
            if (index != text.length) throw ProtocolException("trailing JSON data")
            return value
        }

        private fun parseValue(depth: Int): Any? {
            if (depth > 64) throw ProtocolException("JSON nesting too deep")
            skipWhitespace()
            if (index >= text.length) throw ProtocolException("truncated JSON")
            return when (val ch = text[index]) {
                '{' -> parseObject(depth)
                '[' -> parseArray(depth)
                '"' -> parseString()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else -> if (ch == '-' || ch in '0'..'9') parseNumber() else throw ProtocolException("invalid JSON")
            }
        }

        private fun literal(word: String, value: Any?): Any? {
            if (!text.startsWith(word, index)) throw ProtocolException("invalid JSON literal")
            index += word.length
            return value
        }

        private fun parseObject(depth: Int): Map<String, Any?> {
            index++
            val result = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (peek() == '}') { index++; return result }
            while (true) {
                skipWhitespace()
                if (peek() != '"') throw ProtocolException("object key required")
                val key = parseString()
                if (result.containsKey(key)) throw ProtocolException("duplicate JSON key")
                skipWhitespace()
                expect(':')
                result[key] = parseValue(depth + 1)
                skipWhitespace()
                when (peek()) {
                    ',' -> index++
                    '}' -> { index++; return result }
                    else -> throw ProtocolException("invalid JSON object")
                }
            }
        }

        private fun parseArray(depth: Int): List<Any?> {
            index++
            val result = ArrayList<Any?>()
            skipWhitespace()
            if (peek() == ']') { index++; return result }
            while (true) {
                result.add(parseValue(depth + 1))
                skipWhitespace()
                when (peek()) {
                    ',' -> index++
                    ']' -> { index++; return result }
                    else -> throw ProtocolException("invalid JSON array")
                }
            }
        }

        private fun parseString(): String {
            expect('"')
            val out = StringBuilder()
            while (true) {
                if (index >= text.length) throw ProtocolException("unterminated JSON string")
                val ch = text[index++]
                when {
                    ch == '"' -> return out.toString()
                    ch == '\\' -> {
                        if (index >= text.length) throw ProtocolException("invalid JSON escape")
                        when (val escape = text[index++]) {
                            '"' -> out.append('"')
                            '\\' -> out.append('\\')
                            '/' -> out.append('/')
                            'b' -> out.append('\b')
                            'f' -> out.append('')
                            'n' -> out.append('\n')
                            'r' -> out.append('\r')
                            't' -> out.append('\t')
                            'u' -> {
                                if (index + 4 > text.length) throw ProtocolException("invalid JSON escape")
                                val code = text.substring(index, index + 4).toIntOrNull(16)
                                    ?: throw ProtocolException("invalid JSON escape")
                                out.append(code.toChar())
                                index += 4
                            }
                            else -> throw ProtocolException("invalid JSON escape '$escape'")
                        }
                    }
                    ch.code < 0x20 -> throw ProtocolException("control character in JSON string")
                    else -> out.append(ch)
                }
            }
        }

        private fun parseNumber(): Any {
            val start = index
            if (peek() == '-') index++
            if (peek() == '0') {
                index++
            } else if (peek() in '1'..'9') {
                while (peek() in '0'..'9') index++
            } else {
                throw ProtocolException("invalid JSON number")
            }
            var fractional = false
            if (peek() == '.') {
                fractional = true
                index++
                if (peek() !in '0'..'9') throw ProtocolException("invalid JSON number")
                while (peek() in '0'..'9') index++
            }
            if (peek() == 'e' || peek() == 'E') {
                fractional = true
                index++
                if (peek() == '+' || peek() == '-') index++
                if (peek() !in '0'..'9') throw ProtocolException("invalid JSON number")
                while (peek() in '0'..'9') index++
            }
            val literal = text.substring(start, index)
            if (fractional) {
                val parsed = literal.toDouble()
                if (!parsed.isFinite()) throw ProtocolException("nonfinite JSON")
                return parsed
            }
            return literal.toLongOrNull() ?: throw ProtocolException("JSON integer out of range")
        }

        private fun peek(): Char = if (index < text.length) text[index] else ' '

        private fun expect(ch: Char) {
            if (peek() != ch) throw ProtocolException("expected '$ch'")
            index++
        }

        private fun skipWhitespace() {
            while (index < text.length && text[index] in " \t\n\r") index++
        }
    }
}

open class ProtocolException(message: String) : Exception(message)

class DeniedException(message: String) : ProtocolException(message)
