package com.example;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class ProtocolParserTest {

    @Test
    public void testRoundTrip() {
        // Create a sample protocol with the specified values
        int protocolVersion = 1;
        int softwareVersion = 2;
        long time = 1700000000L;
        String bodyString = "Hello, Protocol";
        byte[] body = bodyString.getBytes();
        int dataLength = body.length;

        // Create the original protocol
        Protocol original = new Protocol(protocolVersion, softwareVersion, dataLength, time, body);

        // Encode it
        byte[] encoded = ProtocolParser.encode(original);
        
        // Decode it back
        Protocol decoded = ProtocolParser.decode(encoded);

        // Verify all fields match
        assertEquals(protocolVersion, decoded.getProtocolVersion());
        assertEquals(softwareVersion, decoded.getSoftwareVersion());
        assertEquals(dataLength, decoded.getDataLength());
        assertEquals(time, decoded.getTime());
        
        // Verify body bytes match
        assertArrayEquals(body, decoded.getBody());
    }
}
