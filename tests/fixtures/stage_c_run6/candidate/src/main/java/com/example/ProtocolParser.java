package com.example;

public class ProtocolParser {
    
    public static byte[] encode(Protocol protocol) {
        if (protocol == null) {
            return null;
        }
        
        int headerLength = 9; // 1 + 1 + 3 + 4 bytes
        int bodyLength = protocol.getBody() != null ? protocol.getBody().length : 0;
        byte[] encoded = new byte[headerLength + bodyLength];
        
        // Write protocolVersion (1 byte, big-endian)
        encoded[0] = (byte) protocol.getProtocolVersion();
        
        // Write softwareVersion (1 byte, big-endian)
        encoded[1] = (byte) protocol.getSoftwareVersion();
        
        // Write dataLength (3 bytes, big-endian)
        encoded[2] = (byte) ((bodyLength >> 16) & 0xFF);
        encoded[3] = (byte) ((bodyLength >> 8) & 0xFF);
        encoded[4] = (byte) (bodyLength & 0xFF);
        
        // Write time (4 bytes, big-endian)
        long time = protocol.getTime();
        encoded[5] = (byte) ((time >> 24) & 0xFF);
        encoded[6] = (byte) ((time >> 16) & 0xFF);
        encoded[7] = (byte) ((time >> 8) & 0xFF);
        encoded[8] = (byte) (time & 0xFF);
        
        // Write body bytes
        if (protocol.getBody() != null && bodyLength > 0) {
            System.arraycopy(protocol.getBody(), 0, encoded, headerLength, bodyLength);
        }
        
        return encoded;
    }
    
    public static Protocol decode(byte[] bytes) {
        if (bytes == null || bytes.length < 9) {
            return null;
        }
        
        // Read protocolVersion (1 byte)
        int protocolVersion = bytes[0] & 0xFF;
        
        // Read softwareVersion (1 byte)
        int softwareVersion = bytes[1] & 0xFF;
        
        // Read dataLength (3 bytes, big-endian)
        int dataLength = ((bytes[2] & 0xFF) << 16) |
                         ((bytes[3] & 0xFF) << 8) |
                         (bytes[4] & 0xFF);
        
        // Read time (4 bytes, big-endian)
        long time = ((long)(bytes[5] & 0xFF) << 24) |
                    ((long)(bytes[6] & 0xFF) << 16) |
                    ((long)(bytes[7] & 0xFF) << 8) |
                    (long)(bytes[8] & 0xFF);
        
        // Extract body bytes
        byte[] body = new byte[dataLength];
        if (dataLength > 0) {
            System.arraycopy(bytes, 9, body, 0, dataLength);
        }
        
        return new Protocol(protocolVersion, softwareVersion, dataLength, time, body);
    }
}
