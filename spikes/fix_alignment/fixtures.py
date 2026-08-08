"""Fixtures for the fix-alignment spike (see README.md).

Both fixtures are reconstructed from real, live-captured Kriya runs this
session - not invented examples. Each pairs a real buggy file, the real
error text it produced, and a mechanical check for whether a proposed fix
actually addresses the root cause (not just "compiles" or "looks plausible").

BUFFER_FIXTURE's buggy_content is the byte-faithful original file pulled
directly from spikes/eval_harness/runs/20260808-001428/logs/
ignite_qpid_protocol.stdout.log (the [Implementing file: ...ProtocolParser.java]
block) - confirmed live line 23 (`buffer.put(protocol.getBody())`) is exactly
where java.nio.BufferOverflowException was thrown, matching the real captured
stack trace used here verbatim.

TYPES_FIXTURE's buggy_content reconstructs the real pattern found live,
2026-08-07 (ignite_qpid_person, run 20260807-193054): `var cache =
ignite.cache(CACHE_NAME)` (raw/erased generics) followed by `cache.get(1)`
assigned directly to a `Person` variable - the exact shape that produced
`incompatible types: java.lang.Object cannot be converted to
com.example.Person`, and the exact shape the incompatible-types scaffold
(kriya/agents/agent.py) was built in direct response to.
"""
import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Fixture:
    id: str
    filepath: str
    buggy_content: str
    error_context: str
    task_description: str
    design_context: str
    diagnosis_check: Callable[[str], bool]
    success_check: Callable[[str], bool]


def _buffer_diagnosis_check(analysis: str) -> bool:
    a = (analysis or "").lower()
    mentions_field = "datalength" in a or "data length" in a or ("time" in a and "long" in a)
    mentions_width_language = any(
        kw in a for kw in ("byte", "width", "putint", "putlong", "overflow", "3-byte", "3 byte")
    )
    return mentions_field and mentions_width_language


def _buffer_success_check(content: str) -> bool:
    bad_datalength = re.search(r"putInt\s*\([^)]*[Dd]ata[Ll]ength", content) is not None
    bad_time = re.search(r"putLong\s*\([^)]*[Tt]ime", content) is not None
    return not bad_datalength and not bad_time


def _types_diagnosis_check(analysis: str) -> bool:
    a = (analysis or "").lower()
    names_the_shape = "cast" in a or "generic" in a or "raw" in a or "var" in a
    names_the_symptom = "object" in a or "person" in a
    return names_the_shape and names_the_symptom


def _types_success_check(content: str) -> bool:
    has_cast = re.search(r"\(\s*Person\s*\)\s*cache\.get", content) is not None
    has_generics = re.search(r"IgniteCache\s*<[^>]*Person[^>]*>\s+cache", content) is not None
    return has_cast or has_generics


BUFFER_FIXTURE = Fixture(
    id="buffer_capacity",
    filepath="src/main/java/com/example/ProtocolParser.java",
    buggy_content=(
        "package com.example;\n"
        "\n"
        "import java.nio.ByteBuffer;\n"
        "import java.nio.ByteOrder;\n"
        "\n"
        "public class ProtocolParser {\n"
        "    \n"
        "    public static byte[] encode(Protocol protocol) {\n"
        "        // Create 9-byte header + body bytes\n"
        "        int totalLength = 9 + protocol.getBody().length;\n"
        "        byte[] encoded = new byte[totalLength];\n"
        "        \n"
        "        ByteBuffer buffer = ByteBuffer.wrap(encoded);\n"
        "        buffer.order(ByteOrder.BIG_ENDIAN);\n"
        "        \n"
        "        // Write header fields\n"
        "        buffer.put((byte) protocol.getProtocolVersion());\n"
        "        buffer.put((byte) protocol.getSoftwareVersion());\n"
        "        buffer.putInt(protocol.getDataLength());  // 3 bytes (but int is 4, we'll use only first 3)\n"
        "        buffer.putLong(protocol.getTime());\n"
        "        \n"
        "        // Write body\n"
        "        buffer.put(protocol.getBody());\n"
        "        \n"
        "        return encoded;\n"
        "    }\n"
        "    \n"
        "    public static Protocol decode(byte[] data) {\n"
        "        if (data == null || data.length < 9) {\n"
        "            throw new IllegalArgumentException(\"Invalid data length for protocol header\");\n"
        "        }\n"
        "        \n"
        "        ByteBuffer buffer = ByteBuffer.wrap(data);\n"
        "        buffer.order(ByteOrder.BIG_ENDIAN);\n"
        "        \n"
        "        // Read header fields\n"
        "        int protocolVersion = buffer.get() & 0xFF;\n"
        "        int softwareVersion = buffer.get() & 0xFF;\n"
        "        int dataLength = (buffer.getInt() >>> 8);  // Extract first 3 bytes from 4-byte int\n"
        "        long time = buffer.getLong();\n"
        "        \n"
        "        // Read body\n"
        "        byte[] body = new byte[data.length - 9];\n"
        "        buffer.get(body);\n"
        "        \n"
        "        return new Protocol(protocolVersion, softwareVersion, dataLength, time, body);\n"
        "    }\n"
        "}\n"
    ),
    error_context=(
        'Exception in thread "main" java.nio.BufferOverflowException\n'
        "\tat java.base/java.nio.HeapByteBuffer.put(HeapByteBuffer.java:231)\n"
        "\tat java.base/java.nio.ByteBuffer.put(ByteBuffer.java:1210)\n"
        "\tat com.example.ProtocolParser.encode(ProtocolParser.java:23)\n"
        "\tat com.example.ProtocolApp.main(ProtocolApp.java:40)\n"
    ),
    task_description=(
        "Fix the ProtocolParser.java error described above. The wire format is: a 9-byte header - "
        "protocolVersion (1 byte), softwareVersion (1 byte), dataLength (3 bytes, big-endian), time "
        "(4 bytes, big-endian) - followed by the raw body bytes."
    ),
    design_context="Protocol/ProtocolParser implement a fixed binary wire format for a Person-carrying message.",
    diagnosis_check=_buffer_diagnosis_check,
    success_check=_buffer_success_check,
)

TYPES_FIXTURE = Fixture(
    id="incompatible_types",
    filepath="src/main/java/com/example/PersonApp.java",
    buggy_content=(
        "package com.example;\n"
        "\n"
        "import org.apache.ignite.Ignite;\n"
        "import org.apache.ignite.Ignition;\n"
        "\n"
        "public class PersonApp {\n"
        "    private static final String CACHE_NAME = \"person-cache\";\n"
        "\n"
        "    private static void readFromIgniteCacheAndPrint() throws Exception {\n"
        "        try (Ignite ignite = Ignition.start(\"ignite-config.xml\")) {\n"
        "            // Get cache\n"
        "            var cache = ignite.cache(CACHE_NAME);\n"
        "\n"
        "            // Read person from cache\n"
        "            Person person = cache.get(1);\n"
        "\n"
        "            if (person != null) {\n"
        "                System.out.println(\"[RESULT] Retrieved Person from cache: \" + person);\n"
        "            } else {\n"
        "                System.out.println(\"No Person found in cache\");\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    error_context=(
        "PersonApp.java:[15,38] incompatible types: java.lang.Object cannot be converted to com.example.Person"
    ),
    task_description="Fix the PersonApp.java compile error described above.",
    design_context="PersonApp reads a Person object back from an Ignite cache and prints it.",
    diagnosis_check=_types_diagnosis_check,
    success_check=_types_success_check,
)

FIXTURES = [BUFFER_FIXTURE, TYPES_FIXTURE]
