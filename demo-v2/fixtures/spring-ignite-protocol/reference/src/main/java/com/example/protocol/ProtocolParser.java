package com.example.protocol;

/** Wire format: protocolVersion (1 byte), softwareVersion (1 byte), dataLength (3 bytes, big-endian), time (4 bytes, big-endian), then the raw body. */
public class ProtocolParser {
    public static final int HEADER_LENGTH = 9;
    private static final int MAX_DATA_LENGTH = 0xFFFFFF;

    public byte[] encode(Protocol p) {
        if (p.getProtocolVersion() < 0 || p.getProtocolVersion() > 255) {
            throw new IllegalArgumentException("protocolVersion must be between 0 and 255");
        }
        if (p.getSoftwareVersion() < 0 || p.getSoftwareVersion() > 255) {
            throw new IllegalArgumentException("softwareVersion must be between 0 and 255");
        }
        byte[] body = p.getBody();
        if (body.length > MAX_DATA_LENGTH) {
            throw new IllegalArgumentException("body is longer than 16,777,215 bytes");
        }
        byte[] out = new byte[HEADER_LENGTH + body.length];
        out[0] = (byte) p.getProtocolVersion();
        out[1] = (byte) p.getSoftwareVersion();
        out[2] = (byte) (body.length >> 16);
        out[3] = (byte) (body.length >> 8);
        out[4] = (byte) body.length;
        long time = p.getTime();
        out[5] = (byte) (time >> 24);
        out[6] = (byte) (time >> 16);
        out[7] = (byte) (time >> 8);
        out[8] = (byte) time;
        System.arraycopy(body, 0, out, HEADER_LENGTH, body.length);
        return out;
    }

    public Protocol decode(byte[] data) {
        if (data == null || data.length < HEADER_LENGTH) {
            throw new IllegalArgumentException("data must be at least " + HEADER_LENGTH + " bytes");
        }
        int dataLength = ((data[2] & 0xFF) << 16) | ((data[3] & 0xFF) << 8) | (data[4] & 0xFF);
        if (data.length != HEADER_LENGTH + dataLength) {
            throw new IllegalArgumentException(
                    "length mismatch: header says " + dataLength + " body bytes, data has " + (data.length - HEADER_LENGTH));
        }
        long time = ((long) (data[5] & 0xFF) << 24) | ((data[6] & 0xFF) << 16) | ((data[7] & 0xFF) << 8) | (data[8] & 0xFF);
        byte[] body = new byte[dataLength];
        System.arraycopy(data, HEADER_LENGTH, body, 0, dataLength);
        return new Protocol(data[0] & 0xFF, data[1] & 0xFF, time, body);
    }
}
