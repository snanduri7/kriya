# Supplementary measurements (transcribed from the commands run in the qualification session, 2026-10-16; each is reproducible with the command shown)

## S1. Ignite 2.18.0 listeners on the host (default configuration), Java 17, reference candidate
Method: run the reference with 'mvn -o -q compile exec:exec', sample 'lsof -nP -a -p <java pid> -i' every 50 ms; unique sockets seen (count = samples):
      24 IPv6 TCP *:47100 (LISTEN)
      13 IPv4 TCP <LAN-IP>:50659-><LAN-IP>:7 (SYN_SENT)
      13 IPv4 TCP <LAN-IP>:50658-><LAN-IP>:7 (SYN_SENT)
      13 IPv4 TCP <LAN-IP>:50655-><LAN-IP>:7 (SYN_SENT)
      11 IPv6 TCP *:47500 (LISTEN)
      11 IPv6 TCP *:* (CLOSED)
      10 IPv6 TCP *:11211 (LISTEN)
      10 IPv6 TCP *:10800 (LISTEN)
       9 IPv6 UDP *:47400 
       3 IPv6 UDP *:59466 
       3 IPv6 UDP *:58255 

## S2. Obsolete / optional flags (Java 17 and Java 21, with only the two required --add-opens)
    --illegal-access=permit : 'OpenJDK 64-Bit Server VM warning: Ignoring option --illegal-access=permit; support was removed in 17.0' then [VERIFICATION] PASS (JDK 17 and 21)
    -Djava.security.manager=allow : accepted, [VERIFICATION] PASS (JDK 17 and 21); not needed
    JDK 26 (Homebrew; the JVM Maven itself runs on in the desktop app's environment): the two flags are enough, [VERIFICATION] PASS

## S3. Skill 'Method A' (plain IgniteConfiguration in the XML + Ignition.start("ignite-config.xml") in try-with-resources), Protocol as the cache value
    mvn -o -q compile exec:exec: [RESULT] ... equals=true, [VERIFICATION] PASS, 'Ignite node stopped OK' (the skill's other recommended mechanism works too)

## S4. Offline resolvability of common JUnit 5 choices (reference pom, surefire version UNSPECIFIED = Maven 3.9.16's default binding surefire 3.5.4, 'mvn -o test', desktop app PATH)
    junit-jupiter 5.7.0  : BUILD FAILURE (junit-platform-launcher:1.7.0 not in ~/.m2)
    junit-jupiter 5.8.2  : 3/3 pass
    junit-jupiter 5.9.2  : 3/3 pass
    junit-jupiter 5.10.0 : 3/3 pass
    junit-jupiter 5.11.4 : BUILD FAILURE (junit-platform-launcher:1.11.4 not in ~/.m2)
    junit-jupiter 5.14.3 : 3/3 pass
    (with surefire 2.22.2 pinned and junit-jupiter 5.8.2: 3/3 pass - the qualified reference)

## S5. Protocol is registered with Ignite's binary marshaller (cache value)
    after a run: ignite/work/db/marshaller/-165319811.classname0 contains the text 'com.example.protocol.Protocol'; -165319811 = Java String.hashCode of the LOWER-CASED class name 'com.example.protocol.protocol'

## S6. Toolchain under the desktop app's environment (env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin HOME=$HOME)
    java -> /usr/bin/java -> Temurin 21.0.12.1; mvn -> /opt/homebrew/bin/mvn 3.9.16 running on Homebrew OpenJDK 26.0.1; no JAVA_HOME. Interactive shell: JAVA_HOME=temurin-17.
