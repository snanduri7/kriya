public class ProtocolParser {
    
    public byte[] encode(Protocol protocol) {
        // Create a buffer with size 9 + body length
        int bodyLength = protocol.getBody() != null ? protocol.getBody().length : 0;
        byte[] buffer = new byte[9 + bodyLength];
        
        // Write header in big-endian format
        int index = 0;
        buffer[index++] = (byte) protocol.getProtocolVersion();
        buffer[index++] = (byte) protocol.getSoftwareVersion();
        buffer[index++] = (byte) (bodyLength >> 16 & 0xFF);
        buffer[index++] = (byte) (bodyLength >> 8 & 0xFF);
        buffer[index++] = (byte) (bodyLength & 0xFF);
        buffer[index++] = (byte) (protocol.getTime() >> 24 & 0xFF);
        buffer[index++] = (byte) (protocol.getTime() >> 16 & 0xFF);
        buffer[index++] = (byte) (protocol.getTime() >> 8 & 0xFF);
        buffer[index++] = (byte) (protocol.getTime() & 0xFF);
        
        // Copy body if not null
        if (protocol.getBody() != null && bodyLength > 0) {
            System.arraycopy(protocol.getBody(), 0, buffer, index, bodyLength);
        }
        
        return buffer;
    }
    
    public Protocol decode(byte[] bytes) {
        // Check minimum length
        if (bytes == null || bytes.length < 9) {
            throw new IllegalArgumentException("Input byte array is too short");
        }
        
        // Read header in big-endian format
        int protocolVersion = bytes[0] & 0xFF;
        int softwareVersion = bytes[1] & 0xFF;
        int dataLength = ((bytes[2] & 0xFF) << 16) | ((bytes[3] & 0xFF) << 8) | (bytes[4] & 0xFF);
        long time = ((long)(bytes[5] & 0xFF) << 24) |
                    ((long)(bytes[6] & 0xFF) << 16) |
                    ((long)(bytes[7] & 0xFF) << 8) |
                    ((long)(bytes[8] & 0xFF));
        
        // Extract body
        byte[] body = null;
        if (dataLength > 0) {
            body = new byte[dataLength];
            System.arraycopy(bytes, 9, body, 0, dataLength);
        }
        
        return new Protocol(protocolVersion, softwareVersion, dataLength, time, body);
    }
}
