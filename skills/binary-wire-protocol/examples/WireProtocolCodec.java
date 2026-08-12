package com.example.protocol;

/**
 * Reference pattern for a fixed-width binary wire header with a mix of
 * field widths, some narrower than their natural Java primitive type.
 *
 * Header layout (8 bytes total):
 *   version   (1 byte,  int 0-255)
 *   length    (3 bytes, big-endian, int 0-16,777,215)
 *   timestamp (4 bytes, big-endian, int - a genuine 4-byte field, so
 *              ByteBuffer/manual packing are equally fine here; shown
 *              manually below for a consistent, dependency-free pattern)
 */
public final class WireProtocolCodec {

    public static byte[] encodeHeader(int version, int length, int timestamp) {
        byte[] header = new byte[8];

        // 1-byte field: no shifting needed, just mask to a single byte.
        header[0] = (byte) (version & 0xFF);

        // 3-byte, big-endian field: narrower than the natural 4-byte int
        // width - manual byte-by-byte packing, NOT buffer.putInt(length),
        // which would write 4 bytes and overflow this 8-byte header.
        header[1] = (byte) ((length >> 16) & 0xFF);
        header[2] = (byte) ((length >> 8) & 0xFF);
        header[3] = (byte) (length & 0xFF);

        // 4-byte, big-endian field: exactly matches int's native width.
        header[4] = (byte) ((timestamp >> 24) & 0xFF);
        header[5] = (byte) ((timestamp >> 16) & 0xFF);
        header[6] = (byte) ((timestamp >> 8) & 0xFF);
        header[7] = (byte) (timestamp & 0xFF);

        return header;
    }

    public static int[] decodeHeader(byte[] header) {
        int version = header[0] & 0xFF;

        // Mask each byte with & 0xFF FIRST - a raw byte with its high bit
        // set otherwise sign-extends into a negative int when shifted.
        int length = ((header[1] & 0xFF) << 16) | ((header[2] & 0xFF) << 8) | (header[3] & 0xFF);

        int timestamp = ((header[4] & 0xFF) << 24) | ((header[5] & 0xFF) << 16)
                | ((header[6] & 0xFF) << 8) | (header[7] & 0xFF);

        return new int[] { version, length, timestamp };
    }
}
