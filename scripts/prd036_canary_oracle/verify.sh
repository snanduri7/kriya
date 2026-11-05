#!/bin/sh
# PRD-036 canary REQ-3 oracle (operator verification authority, kriya.verification_authority/1).
#
# REQ-3: "Do not change the method's signature, its `@Transactional`/`@Override` annotations, or any other method in this class."
# (and the goal's "Fix only this method"). Deterministic and strict. For DefaultDriverService.java the candidate must equal the BASE except
# the BODY of delete(Long driverId) (braces included) and import lines:
#   HEADER  = the annotation lines directly above delete(Long driverId) plus its signature line  -> must be byte-identical;
#   REST    = every other line (all other methods, fields, constants, the class declaration, comments), import lines excluded,
#             with a position marker where delete was                                             -> must be byte-identical.
# Exit 0 = PASS, 1 = the candidate contradicts REQ-3 (FAIL), 2 = the oracle could not decide (INDETERMINATE).
# Limit (disclosed): braces inside string literals or comments of the delete body are counted; the body of the fixture's delete has none.
set -eu
HERE=".kriya/authority/oracle"
BASE="$HERE/base/DefaultDriverService.java"
CAND="src/main/java/com/myapp/service/driver/DefaultDriverService.java"
[ -f "$BASE" ] || { echo "oracle asset missing: $BASE" >&2; exit 2; }
[ -f "$CAND" ] || { echo "REQ-3 contradicted: $CAND no longer exists" >&2; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

split() {  # split FILE PREFIX  -> PREFIX.header, PREFIX.rest ; awk exit 3 = delete(Long driverId) not found exactly once
  awk -v hdr="$2.header" -v rest="$2.rest" '
    { L[NR] = $0 }
    END {
      n = NR; s = 0; found = 0
      for (i = 1; i <= n; i++)
        if (L[i] ~ /^[ \t]*public[ \t]+void[ \t]+delete[ \t]*\([ \t]*Long[ \t]+driverId[ \t]*\)/) { s = i; found++ }
      if (found != 1) exit 3
      h = s
      while (h > 1 && L[h - 1] ~ /^[ \t]*@/) h--
      depth = 0; started = 0; e = 0
      for (i = s; i <= n && !e; i++) {
        line = L[i]
        o = gsub(/\{/, "{", line); c = gsub(/\}/, "}", line)
        if (o > 0) started = 1
        depth += o - c
        if (started && depth == 0) e = i
      }
      if (!e) exit 3
      b = s + 1; while (b < e && L[b] !~ /\{/) b++      # first line carrying the opening brace (Allman or K&R)
      if (L[s] ~ /\{/) b = s
      for (i = h; i <= s; i++) print L[i] > hdr
      for (i = 1; i <= n; i++) {
        if (i >= h && i <= s) continue
        if (i == s + 1) print "<<DELETE-POSITION>>" > rest
        if (i > s && i <= e) continue
        if (L[i] ~ /^import[ \t]/) continue
        print L[i] > rest
      }
      if (s == n) print "<<DELETE-POSITION>>" > rest
      close(hdr); close(rest)
    }' "$1"
}

split "$BASE" "$WORK/base" || { echo "oracle base asset is not the expected class (delete not found exactly once)" >&2; exit 2; }
if ! split "$CAND" "$WORK/cand"; then echo "REQ-3 contradicted: delete(Long driverId) is not present exactly once with its signature" >&2; exit 1; fi
cmp -s "$WORK/base.header" "$WORK/cand.header" || { echo "REQ-3 contradicted: the signature or annotations of delete(Long driverId) changed" >&2; exit 1; }
cmp -s "$WORK/base.rest" "$WORK/cand.rest" || { echo "REQ-3 contradicted: code outside the body of delete(Long driverId) changed" >&2; diff "$WORK/base.rest" "$WORK/cand.rest" | head -5 >&2 || true; exit 1; }
echo "REQ-3 PASS: only the body of delete(Long driverId) (and imports) differ from the base"
exit 0
