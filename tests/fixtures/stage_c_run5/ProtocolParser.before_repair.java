package com.example;

public class ProtocolParser {
    
    public byte[] encode(Protocol protocol) {
        if (protocol == null) {
            return null;
        }
        
        // Create header bytes (9 bytes total)
        byte[] header = new byte[9];
        header[0] = (byte) protocol.getProtocolVersion();           // 1 byte
        header[1] = (byte) protocol.getSoftwareVersion();           // 1 byte
        int dataLength = protocol.getDataLength();
        header[2] = (byte) (dataLength >> 16 & 0xFF);                // 3 bytes for dataLength
        header[3] = (byte) (dataLength >> 8 & 0xFF);
        header[4] = (byte) (dataLength & 0xFF);
        long time = protocol.getTime();
        header[5] = (byte) (time >> 24 & 0xFF);                     // 4 bytes for time
        header[6] = (byte) (time >> 16 & 0xFF);
        header[7] = (byte) (time >> 8 & 0xFF);
        header[8] = (byte) (time & 0xFF);
        
        // Get body bytes
        byte[] body = protocol.getBody();
        int bodyLength = body != null ? body.length : 0;
        
        // Create result array
        byte[] result = new byte[9 + bodyLength];
        
        // Copy header
        System.arraycopy(header, 0, result, 0, 9);
        
        // Copy body if not null
        if (body != null) {
            System.arraycopy(body, 0, result, 9, bodyLength);
        }
        
        return result;
    }
    
    public Protocol decode(byte[] bytes) {
        if (bytes == null || bytes.length < 9) {
            return null;
        }
        
        // Read header
        int protocolVersion = bytes[0] & 0xFF;
        int softwareVersion = bytes[1] & 0xFF;
        int dataLength = ((bytes[2] & 0xFF) << 16) | 
                         ((bytes[3] & 0xFF) << 8) | 
                         (bytes[4] & 0xFF);
        long time = ((long)(bytes[5] & 0xFF) << 24) |
                    ((long)(bytes[6] & 0xFF) << 16) |
                    ((long)(bytes[7] & 0xFF) << 8) |
                    (long)(bytes[8] & 0xFF);
        
        // Extract body
        byte[] body = new byte[dataLength];
        if (dataLength > 0) {
            System.arraycopy(bytes, 9, body, 0, dataLength);
        }
        
        return new Protocol(protocolVersion, softwareVersion, dataLength, time, body);
    }
}
