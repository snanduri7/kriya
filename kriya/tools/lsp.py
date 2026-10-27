"""Language Server Protocol (LSP) grounding for jdtls (Eclipse JDT Language Server) - the Developer retry loop's check against the project's REAL resolved type graph.

S8-ORPHAN-HOST-CODE-001: jdtls imports the project's own Maven/Gradle build and has NO contained mode, so it is never started while containment is mandatory - and containment is
mandatory for every subject (kriya/tools/containment.py::_containment_is_mandatory is unconditional). `JdtlsClient.start()` therefore always raises the typed refusal and no jdtls
process can exist. The process machinery that used to follow it (the stdio JSON-RPC framing, the reader loop, requests, the private candidate mirror and the tree-kill shutdown) was
unreachable and is deleted; the caller (kriya/workflow/lsp_integration.py) degrades to no LSP grounding. A contained jdtls would be a new feature, not a revival of that code.
What remains is the PATH lookup the doctor uses and the prompt formatter for diagnostics."""
import logging
import shutil
from typing import Any, Dict, List, Optional

from kriya.tools.containment import decide_external_execution

logger = logging.getLogger(__name__)

# jdtls itself needs a modern JVM to RUN - separate from whatever JDK the analyzed project targets (kriya doctor reports it).
JDTLS_MIN_JAVA_MAJOR_VERSION = 21

# LSP diagnostic severity 1 == Error (2=Warning, 3=Info, 4=Hint) - only errors are worth interrupting a retry prompt for.
LSP_SEVERITY_ERROR = 1


def find_jdtls() -> Optional[str]:
    """Locates the `jdtls` launcher on PATH (`kriya doctor` reports it; never auto-downloaded). Returns None (never raises) if not found."""
    return shutil.which("jdtls")


class JdtlsClient:
    """The jdtls client of one generation run. It can never be started: `start()` is the typed refusal (see the module docstring), so a caller never holds a started client."""

    def __init__(self, project_root: str, jdtls_path: str, autonomy: Any = None):
        self.project_root = project_root
        self.jdtls_path = jdtls_path
        # the configuration the canonical containment decision is made from (kriya/tools/containment.py)
        self._autonomy = autonomy

    async def start(self) -> None:
        """Refused, typed, before anything is created: jdtls imports the project's build on the host and has no contained mode."""
        decide_external_execution(self._autonomy, subject="JdtlsClient").refuse_unsupported(
            "jdtls imports the project's build on the host and has no contained mode; it is not started while containment is mandatory")


def format_diagnostics_for_prompt(filepath: str, diagnostics: List[Dict[str, Any]]) -> str:
    """Formats jdtls diagnostics (errors only) into a deliberately forceful,
    unambiguous prompt block - this is the project's own, already-resolved
    ground truth confirming a specific mistake, not a suggestion the model is
    free to weigh against its own assumption. Empty string if there are no
    error-severity diagnostics."""
    errors = [d for d in diagnostics if d.get("severity") == LSP_SEVERITY_ERROR]
    if not errors:
        return ""
    lines = []
    for d in errors:
        line_no = d.get("range", {}).get("start", {}).get("line", 0) + 1
        message = d.get("message", "").strip()
        lines.append(f"  [line {line_no}] {message}")
    diagnostics_text = "\n".join(lines)
    return (
        f"\n=== LANGUAGE SERVER GROUND TRUTH for '{filepath}' - READ THIS CAREFULLY ===\n"
        "This is not a guess, and it is not something to weigh against your own assumption. "
        "A real language server just checked this file against the ACTUAL resolved project "
        "classpath on disk - the real dependencies, the real package structure - and confirms "
        "the following is definitely, unambiguously wrong:\n"
        f"{diagnostics_text}\n"
        "If you regenerate this file without genuinely fixing every one of these, the exact "
        "same error WILL happen again - there is no version of this code that compiles while "
        "this remains true. Do not rationalize around it or assume you already know better. "
        "Fix precisely what is stated above.\n"
    )
