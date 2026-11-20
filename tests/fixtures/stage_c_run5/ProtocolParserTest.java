package com.example;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class ProtocolParserTest {

    @Test
    public void testRoundTrip() {
        // Create a sample Protocol
        byte[] body = "Hello, Protocol".getBytes();
        Protocol original = new Protocol(1, 2, body.length, 1700000000L, body);
        
        // Encode it
        ProtocolParser parser = new ProtocolParser();
        byte[] encoded = parser.encode(original);
        
        // Decode it back
        Protocol decoded = parser.decode(encoded);
        
        // Verify all fields match
        assertEquals(original.getProtocolVersion(), decoded.getProtocolVersion());
        assertEquals(original.getSoftwareVersion(), decoded.getSoftwareVersion());
        assertEquals(original.getDataLength(), decoded.getDataLength());
        assertEquals(original.getTime(), decoded.getTime());
        
        // Verify body bytes match
        assertArrayEquals(original.getBody(), decoded.getBody());
    }
    
    @Test
    public void testEmptyBody() {
        // Test with empty body
        Protocol original = new Protocol(1, 2, 0, 1700000000L, new byte[0]);
        
        ProtocolParser parser = new ProtocolParser();
        byte[] encoded = parser.encode(original);
        Protocol decoded = parser.decode(encoded);
        
        assertEquals(original.getProtocolVersion(), decoded.getProtocolVersion());
        assertEquals(original.getSoftwareVersion(), decoded.getSoftwareVersion());
        assertEquals(original.getDataLength(), decoded.getDataLength());
        assertEquals(original.getTime(), decoded.getTime());
        assertArrayEquals(original.getBody(), decoded.getBody());
    }
    
    @Test
    public void testNullBody() {
        // Test with null body
        Protocol original = new Protocol(1, 2, 0, 1700000000L, null);
        
        ProtocolParser parser = new ProtocolParser();
        byte[] encoded = parser.encode(original);
        Protocol decoded = parser.decode(encoded);
        
        assertEquals(original.getProtocolVersion(), decoded.getProtocolVersion());
        assertEquals(original.getSoftwareVersion(), decoded.getSoftwareVersion());
        assertEquals(original.getDataLength(), decoded.getDataLength());
        assertEquals(original.getTime(), decoded.getTime());
        assertNull(decoded.getBody());
    }
}
