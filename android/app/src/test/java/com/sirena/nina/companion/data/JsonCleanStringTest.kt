package com.sirena.nina.companion.data

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class JsonCleanStringTest {
    @Test
    fun jsonCleanString_trimsAndRejectsNullSentinel() {
        val j = JSONObject()
        j.put("a", "  hello  ")
        j.put("b", "null")
        j.put("c", JSONObject.NULL)
        assertEquals("hello", j.jsonCleanString("a"))
        assertNull(j.jsonCleanString("b"))
        assertNull(j.jsonCleanString("c"))
        assertNull(JSONObject().jsonCleanString("missing"))
    }
}
