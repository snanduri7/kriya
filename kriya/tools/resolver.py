import logging
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

def resolve_maven_class(
    query_term: str, query_type: str = "fc", *, allow_external_lookup: bool = True,
) -> Optional[Dict[str, str]]:
    """
    Query Maven Central's SOLR search API to find coordinates for a missing class or package.
    query_type can be:
      - 'fc': fully qualified class name (e.g. org.apache.qpid.jms.JmsConnectionFactory)
      - 'c': simple class name (e.g. AMQPProtocolManagerFactory)
      - 'g': general text search
    """
    if not allow_external_lookup:
        return None

    if query_type == "fc":
        q = f'fc:"{query_term}"'
    elif query_type == "c":
        q = f'c:"{query_term}"'
    else:
        q = f'"{query_term}"'

    url = f"https://search.maven.org/solrsearch/select?q={q}&rows=1&wt=json"
    try:
        # Use synchronous Client since validator runs synchronously
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                docs = resp.json().get("response", {}).get("docs", [])
                if docs:
                    doc = docs[0]
                    return {
                        "groupId": doc.get("g"),
                        "artifactId": doc.get("a"),
                        "version": doc.get("latestVersion")
                    }
    except Exception as e:
        logger.debug(f"Failed to query Maven Central for {query_term}: {e}")
    return None

def enrich_java_compiler_errors(
    output: str, *, allow_external_lookup: bool = True,
) -> str:
    """
    Parses Java compiler output, identifies missing packages/classes,
    queries Maven Central, and appends suggestions to the output log.
    """
    if not output:
        return output
    # Enforce the capability at this public boundary too, before parsing
    # potentially proprietary compiler text or invoking even a mocked/custom
    # resolver implementation. The lower-level resolver repeats the guard as
    # defense in depth for direct callers.
    if not allow_external_lookup:
        return output

    suggestions = []

    # 1. Match ClassNotFoundException
    class_not_found = re.findall(r"ClassNotFoundException:\s+([a-zA-Z0-9_\.]+)", output)
    for cls in class_not_found:
        res = resolve_maven_class(cls, "fc", allow_external_lookup=allow_external_lookup)
        if res:
            suggestions.append((cls, res))

    # 2. Match cannot find class in bean definitions
    bean_class_not_found = re.findall(r"Cannot find class \[([a-zA-Z0-9_\.]+)\]", output)
    for cls in bean_class_not_found:
        res = resolve_maven_class(cls, "fc", allow_external_lookup=allow_external_lookup)
        if res:
            suggestions.append((cls, res))

    # 3. "cannot find symbol: class X" (a bare, unqualified class name) is
    # deliberately NOT queried against Maven Central. A bare simple name has
    # no qualifying context at all - millions of unrelated classes across
    # Maven Central share common names - and Solr's c:"..." field search
    # returns its single top hit with no relevance/consensus filtering.
    # Confirmed live, twice, as actively harmful rather than just unhelpful:
    # a real "cannot find symbol: class IgniteCache" (whose real cause was a
    # wrong import path, not a missing dependency) matched an unrelated tiny
    # library (cc.mashroom:mashroom-plugin) that then got auto-accrued into
    # a permanent, git-committed skill polluting a later, unrelated goal;
    # separately, 5/5 bare-class-name matches in the same run
    # (SystemLauncher, ConnectionFactory, MessageProducer, MessageConsumer,
    # TextMessage) were unrelated garbage (dataspacetck, ldk-sql-api,
    # tracee-examples-jms-api, dapeng-message-api, dingtalk) in the SAME
    # compiler output where the package-based search below correctly and
    # usefully resolved javax.jms -> javax.jms:jms:1.1. Package/FQCN context
    # (patterns 1, 2, 4) is meaningfully more specific and hasn't shown this
    # failure mode - only the fully-unqualified bare-name case is removed.

    # 4. Match package X does not exist
    missing_packages = re.findall(r"package\s+([a-zA-Z0-9_\.]+)\s+does not exist", output, re.IGNORECASE)
    for pkg in missing_packages:
        res = resolve_maven_class(pkg, "g", allow_external_lookup=allow_external_lookup)
        if res:
            suggestions.append((pkg, res))

    if suggestions:
        enriched = [output, "\n=== KRIYA PLATFORM DEPENDENCY SUGGESTIONS ==="]
        # De-duplicate suggestions
        seen = set()
        for term, coords in suggestions:
            key = f"{coords['groupId']}:{coords['artifactId']}"
            if key in seen:
                continue
            seen.add(key)
            enriched.append(
                f"[KRIYA SUGGESTION] Missing item '{term}' was matched to Maven dependency:\n"
                f"<dependency>\n"
                f"    <groupId>{coords['groupId']}</groupId>\n"
                f"    <artifactId>{coords['artifactId']}</artifactId>\n"
                f"    <version>{coords['version']}</version>\n"
                f"</dependency>"
            )
        enriched.append("=============================================\n")
        return "\n".join(enriched)

    return output
