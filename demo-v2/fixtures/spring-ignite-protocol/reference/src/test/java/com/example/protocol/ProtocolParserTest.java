package com.example.protocol;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class ProtocolParserTest {
    private final ProtocolParser parser = new ProtocolParser();

    @Test
    void roundTripPreservesEveryField() {
        Protocol original = new Protocol(1, 2, 1700000000L, "Hello, Protocol".getBytes(StandardCharsets.UTF_8));
        Protocol decoded = parser.decode(parser.encode(original));
        assertEquals(original, decoded);
        assertEquals(15, decoded.getDataLength());
        assertEquals(1700000000L, decoded.getTime());
    }

    @Test
    void encodeWritesTheNineByteBigEndianHeader() {
        byte[] encoded = parser.encode(new Protocol(1, 2, 0x01020304L, new byte[] {9, 8, 7}));
        assertArrayEquals(new byte[] {1, 2, 0, 0, 3, 1, 2, 3, 4, 9, 8, 7}, encoded);
    }

    @Test
    void decodeRejectsAMismatchedLength() {
        byte[] encoded = parser.encode(new Protocol(1, 2, 5L, new byte[] {1, 2, 3}));
        byte[] truncated = new byte[encoded.length - 1];
        System.arraycopy(encoded, 0, truncated, 0, truncated.length);
        assertThrows(IllegalArgumentException.class, () -> parser.decode(truncated));
    }
}
