package com.example.protocol;

import java.util.Arrays;

/** A protocol message: a small header plus a raw body. Stored in the Ignite cache as a value. */
public class Protocol {
    private int protocolVersion;
    private int softwareVersion;
    private int dataLength;
    private long time;
    private byte[] body = new byte[0];

    public Protocol() {
    }

    public Protocol(int protocolVersion, int softwareVersion, long time, byte[] body) {
        this.protocolVersion = protocolVersion;
        this.softwareVersion = softwareVersion;
        this.time = time;
        setBody(body);
    }

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
        return body.clone();
    }

    public void setBody(byte[] body) {
        this.body = body == null ? new byte[0] : body.clone();
        this.dataLength = this.body.length;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (o == null || getClass() != o.getClass()) {
            return false;
        }
        Protocol other = (Protocol) o;
        return protocolVersion == other.protocolVersion
                && softwareVersion == other.softwareVersion
                && dataLength == other.dataLength
                && time == other.time
                && Arrays.equals(body, other.body);
    }

    @Override
    public int hashCode() {
        int result = Integer.hashCode(protocolVersion);
        result = 31 * result + softwareVersion;
        result = 31 * result + dataLength;
        result = 31 * result + Long.hashCode(time);
        result = 31 * result + Arrays.hashCode(body);
        return result;
    }

    @Override
    public String toString() {
        return "Protocol{protocolVersion=" + protocolVersion + ", softwareVersion=" + softwareVersion
                + ", dataLength=" + dataLength + ", time=" + time + ", bodyLength=" + body.length + '}';
    }
}
