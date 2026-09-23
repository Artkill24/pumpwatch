import base64, hashlib, json, os, sys, urllib.request, zlib
sys.path.insert(0, "/opt/pumpwatch")
from pda import b58decode, b58encode, find_program_address

RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")

def idl_address(program_id):
    base, _ = find_program_address([], program_id)
    h = hashlib.sha256(b58decode(base) + b"anchor:idl"
                       + b58decode(program_id)).digest()
    return b58encode(h)

def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    if "error" in d:
        sys.exit(f"{method}: {d['error']}")
    return d.get("result")

def fetch_idl(program_id):
    addr = idl_address(program_id)
    print(f"IDL account: {addr}")
    val = (rpc("getAccountInfo", [addr, {"encoding": "base64"}]) or {}).get("value")
    if not val:
        return None
    raw = base64.b64decode(val["data"][0])
    if len(raw) < 44:
        return None
    n = int.from_bytes(raw[40:44], "little")
    try:
        return json.loads(zlib.decompress(raw[44:44 + n]))
    except Exception as e:
        print(f"could not decompress: {e}")
        return None

def summarize(idl):
    print(f"\nprogram: {idl.get('name')}  version {idl.get('version')}")
    accounts = idl.get("accounts") or []
    types = {t["name"]: t for t in (idl.get("types") or [])}
    print(f"\n{len(accounts)} account types:")
    for a in accounts:
        name = a.get("name")
        ty = a.get("type") or types.get(name, {}).get("type") or {}
        fields = ty.get("fields") or []
        print(f"\n  {name}  ({len(fields)} fields)")
        for f in fields:
            t = f.get("type")
            t = t if isinstance(t, str) else json.dumps(t)
            print(f"     {f.get('name'):30} {t}")

if __name__ == "__main__":
    idl = fetch_idl(sys.argv[1])
    if idl is None:
        sys.exit("no IDL published on chain for this program")
    if "--dump" in sys.argv:
        path = sys.argv[sys.argv.index("--dump") + 1]
        json.dump(idl, open(path, "w"), indent=1)
        print(f"full IDL written to {path}")
    summarize(idl)
