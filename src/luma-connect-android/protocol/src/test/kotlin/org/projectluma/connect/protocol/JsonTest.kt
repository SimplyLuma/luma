package org.projectluma.connect.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class JsonTest {
    private val backslash = '\\'

    @Test
    fun encodesLikePythonSortedCompactAscii() {
        val tricky = "e" + 0xe9.toChar() + "\n" + 1.toChar() + 0x7f.toChar() + "\""
        val value = mapOf("b" to 1L, "a" to listOf(true, null, tricky), "c" to mapOf("z" to "", "y" to -5L))
        // Python: json.dumps(value, sort_keys=True, separators=(",", ":"))
        val expected = "{\"a\":[true,null,\"e${backslash}u00e9${backslash}n${backslash}u0001${backslash}u007f$backslash\"\"],\"b\":1,\"c\":{\"y\":-5,\"z\":\"\"}}"
        assertEquals(expected, Json.encodeToString(value))
        assertEquals(value, Json.parse(expected))
    }

    @Test
    fun roundTripsNonBmpTextAsLowercaseSurrogates() {
        val text = "late " + String(Character.toChars(0x1F600))
        assertEquals("{\"t\":\"late ${backslash}ud83d${backslash}ude00\"}", Json.encodeToString(mapOf("t" to text)))
        assertEquals(text, (Json.parse(Json.encode(mapOf("t" to text))) as Map<*, *>)["t"])
    }

    @Test
    fun rejectsDuplicateKeysTrailingDataAndNonFiniteConstants() {
        assertThrows(ProtocolException::class.java) { Json.parse("{\"a\":1,\"a\":2}") }
        assertThrows(ProtocolException::class.java) { Json.parse("{\"a\":1} x") }
        assertThrows(ProtocolException::class.java) { Json.parse("{\"a\":NaN}") }
        assertThrows(ProtocolException::class.java) { Json.parse("{\"a\":Infinity}") }
        assertThrows(ProtocolException::class.java) { Json.parseObject("[1]".toByteArray()) }
    }

    @Test
    fun refusesFloatingPointOnEncode() {
        assertThrows(ProtocolException::class.java) { Json.encode(mapOf("x" to 1.5)) }
    }
}
