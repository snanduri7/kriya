import logging
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

def resolve_maven_class(query_term: str, query_type: str = "fc") -> Optional[Dict[str, str]]:
    """
    Query Maven Central's SOLR search API to find coordinates for a missing class or package.
    query_type can be:
      - 'fc': fully qualified class name (e.g. org.apache.qpid.jms.JmsConnectionFactory)
      - 'c': simple class name (e.g. AMQPProtocolManagerFactory)
      - 'g': general text search
    """
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

def enrich_java_compiler_errors(output: str) -> str:
    """
    Parses Java compiler output, identifies missing packages/classes,
    queries Maven Central, and appends suggestions to the output log.
    """
    if not output:
        return output

    suggestions = []

    # 1. Match ClassNotFoundException
    class_not_found = re.findall(r"ClassNotFoundException:\s+([a-zA-Z0-9_\.]+)", output)
    for cls in class_not_found:
        res = resolve_maven_class(cls, "fc")
        if res:
            suggestions.append((cls, res))

    # 2. Match cannot find class in bean definitions
    bean_class_not_found = re.findall(r"Cannot find class \[([a-zA-Z0-9_\.]+)\]", output)
    for cls in bean_class_not_found:
        res = resolve_maven_class(cls, "fc")
        if res:
            suggestions.append((cls, res))

    # 3. Match cannot find symbol: class X
    missing_symbols = re.findall(r"cannot find symbol\s+symbol:\s+class\s+([a-zA-Z0-9_]+)", output, re.IGNORECASE)
    for sym in missing_symbols:
        # Exclude common built-in java types to avoid redundant queries
        if sym in ("String", "Integer", "List", "Map", "Set", "Exception"):
            continue
        res = resolve_maven_class(sym, "c")
        if res:
            suggestions.append((sym, res))

    # 4. Match package X does not exist
    missing_packages = re.findall(r"package\s+([a-zA-Z0-9_\.]+)\s+does not exist", output, re.IGNORECASE)
    for pkg in missing_packages:
        res = resolve_maven_class(pkg, "g")
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
