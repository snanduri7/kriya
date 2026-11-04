import json,sys,hashlib,urllib.request,re
S=sys.argv[1]; M=sys.argv[2]
man={f["path"]:f for f in json.load(open(M))["files"]}
paths=[l.strip() for l in open(S+"/refused.txt") if l.strip()]
C="https://repo.maven.apache.org/maven2/"
def get(p): return urllib.request.urlopen(C+p,timeout=60).read()
rows=[];bad=[]
for sp in paths:
    ap=sp[:-5]
    side=get(sp); art=get(ap)
    art_ok=hashlib.sha256(art).hexdigest()==man[ap]["sha256"]       # Central artifact == frozen artifact?
    txt=side.decode("utf-8","replace")
    m=re.match(r"\s*([0-9a-fA-F]{40})(\s+\*?(\S.*?))?\s*$",txt)
    val=m.group(1).lower() if m else None
    sha1=hashlib.sha1(art).hexdigest()
    frozen=man[sp]["sha256"]
    cand={"bare":sha1.encode(),"bare+nl":(sha1+"\n").encode()}
    which=[k for k,v in cand.items() if hashlib.sha256(v).hexdigest()==frozen]
    # representation class
    rep=("bare" if re.fullmatch(r"\s*[0-9a-fA-F]{40}\s*",txt) else "hash *name" if "*" in txt else "hash staging-path" if "/" in txt.split(None,1)[-1] else "hash name")+(" +nl" if txt.endswith("\n") else " no-nl")
    ok=art_ok and val==sha1 and which==["bare"]
    if not ok: bad.append(sp)
    rows.append((sp,rep,val==sha1,art_ok,which,repr(side)[:90]))
for r in rows: print(r)
print("TOTAL",len(rows),"ALL_OK",not bad,"BAD",bad)
from collections import Counter; print(Counter(r[1] for r in rows))
