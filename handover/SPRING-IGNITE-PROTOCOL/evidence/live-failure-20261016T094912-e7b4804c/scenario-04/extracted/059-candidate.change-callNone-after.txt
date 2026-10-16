package com.example.protocol;

/**
 * Represents a protocol message with versioning, timestamp, and payload.
 */
public class Protocol {
    private int protocolVersion;
    private int softwareVersion;
    private int dataLength;
    private long time;
    private byte[] body;

    // Default constructor required for serialization
    public Protocol() {
    }

    public Protocol(int protocolVersion, int softwareVersion, int dataLength, long time, byte[] body) {
        this.protocolVersion = protocolVersion;
        this.softwareVersion = softwareVersion;
        this.dataLength = dataLength;
        this.time = time;
        this.body = body;
    }

    // Getters and setters
    public int getProtocolVersion() {
        return protocolVersion;
    }

    public void setProtocolVersion(int protocolVersion) {
        this.protocolVersion = protocolVersion;
    }

    public int getSoftwareVersion() {
        return softwareVersion;
    }

    public void setSoftwareVersion(int softwareVersion) {
        this.softwareVersion = softwareVersion;
    }

    public int getDataLength() {
        return dataLength;
    }

    public void setDataLength(int dataLength) {
        this.dataLength = dataLength;
    }

    public long getTime() {
        return time;
    }

    public void setTime(long time) {
        this.time = time;
    }

    public byte[] getBody() {
        return body;
    }

    public void setBody(byte[] body) {
        this.body = body;
    }

    @Override
    public boolean equals(Object obj) {
        if (this == obj) {
            return true;
        }
        if (obj == null || getClass() != obj.getClass()) {
            return false;
        }

        Protocol protocol = (Protocol) obj;

        if (protocolVersion != protocol.protocolVersion) {
            return false;
        }
        if (softwareVersion != protocol.softwareVersion) {
            return false;
        }
        if (dataLength != protocol.dataLength) {
            return false;
        }
        if (time != protocol.time) {
            return false;
        }
        // Body comparison
        if (body == null && protocol.body == null) {
            return true;
        }
        if (body == null || protocol.body == null) {
            return false;
        }
        if (body.length != protocol.body.length) {
            return false;
        }

        for (int i = 0; i < body.length; i++) {
            if (body[i] != protocol.body[i]) {
                return false;
            }
        }
        return true;
    }

    @Override
    public int hashCode() {
        int result = protocolVersion;
        result = 31 * result + softwareVersion;
        result = 31 * result + dataLength;
        result = 31 * result + (int) (time ^ (time >>> 32));
        result = 31 * result + (body != null ? java.util.Arrays.hashCode(body) : 0);
        return result;
    }

    @Override
    public String toString() {
        return "Protocol{" +
                "protocolVersion=" + protocolVersion +
                ", softwareVersion=" + softwareVersion +
                ", dataLength=" + dataLength +
                ", time=" + time +
                ", body=" + new String(body) +
                '}';
    }
}
