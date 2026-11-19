public class Protocol {
    private final int protocolVersion;
    private final int softwareVersion;
    private final int dataLength;
    private final long time;
    private final byte[] body;

    public Protocol(int protocolVersion, int softwareVersion, int dataLength, long time, byte[] body) {
        this.protocolVersion = protocolVersion;
        this.softwareVersion = softwareVersion;
        this.dataLength = dataLength;
        this.time = time;
        this.body = body != null ? body.clone() : null; // Defensive copy
    }

    public int getProtocolVersion() {
        return protocolVersion;
    }

    public int getSoftwareVersion() {
        return softwareVersion;
    }

    public int getDataLength() {
        return dataLength;
    }

    public long getTime() {
        return time;
    }

    public byte[] getBody() {
        return body != null ? body.clone() : null; // Defensive copy
    }
}
