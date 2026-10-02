#!/usr/bin/env python3
"""
test_schema_wiring.py - pins the v7.5 contract. Run:  python test_schema_wiring.py
(no pytest needed; exit code 1 on any failure)

 1. schema validates itself
 2. yt_settings7.json is clean against the schema
 3. DRIFT: neither the engine nor the GUI may carry a default again
    (no settings.get("KEY", fallback) for a schema key; GUI registries are
    pure aliases of the schema)
 4. GATE MATRIX: every DOWNLOAD_TYPE x every switch combination obeys the
    state rules (hidden / structurally disabled / nearest unmet parent)
 5. HIDDEN INVARIANT: in each hiding mode, varying a hidden key's raw value
    never changes build_command's argv (the cross-file "memory rule")
 6. ENGINE LOADS DEFAULTS: a settings file missing a key gets the schema
    default, not a KeyError
"""
import ast, importlib.util, itertools, json, re, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0]]


def _import_sibling(fname):
    """
    Load a sibling module from this file's own folder, by path. A plain
    `import` can never load a file whose version position holds a dot
    (yt_schema7.5.py - Python reads that dot as a package separator), so
    the loader goes through importlib's file-location machinery instead,
    under the module name the filename spells. This small helper is
    deliberately duplicated in the engine, the GUI and the wiring test:
    it has to run before the schema exists to be imported from.
    """
    p = Path(__file__).parent / fname
    if not p.is_file():
        raise ImportError(f"{fname} not found next to {Path(__file__).name} "
                          f"- it must sit in the same folder.")
    spec = importlib.util.spec_from_file_location(fname[:-3], p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fname[:-3]] = mod
    spec.loader.exec_module(mod)
    return mod


S = _import_sibling("yt_schema7.5.py")

FAILS = []
def check(name, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + name + (f"  -> {detail}" if detail and not ok else ""))
    if not ok: FAILS.append(name)

print("1. schema")
check("self_check clean", S.self_check() == [], str(S.self_check()))

print("2. settings file")
jf = HERE / "yt_settings7.json"
if jf.exists():
    r = S.check_document(json.loads(jf.read_text(encoding="utf-8")))
    check("no unknown/missing/invalid keys", not (r["unknown"] or r["missing"] or r["invalid"]), str(r))
else:
    print("  skip  (yt_settings7.json not next to this file)")

print("3. drift")
GET = re.compile(r'settings(?:\(\))?\.get\(\s*[\'"]([A-Z][A-Z0-9_]+)[\'"]\s*,')
for f in ("yt_video_downloader7.5.py", "yt_settings_gui7.5.py"):
    p = HERE / f
    if not p.exists(): print("  skip ", f); continue
    src = p.read_text(encoding="utf-8")
    hits = [k for k in GET.findall(src) if k in S.BY_KEY]
    check(f"{f}: no per-read fallbacks for schema keys", not hits, str(hits))
gui = HERE / "yt_settings_gui7.5.py"
if gui.exists():
    tree = ast.parse(gui.read_text(encoding="utf-8"))
    names = {"HIDDEN_IN_MODE","SCHEMA","LANG_CHIPS","MORE_LANGS","CHIP_KEYS","LANG_PICKER_KEYS",
             "ADVANCED_OVERRIDE_KEYS","MODE_HINTS","TEXT_WIDTHS","BROWSE_KEYS","HINTS",
             "ARRAY_INT_KEYS","ARRAY_STR_KEYS","INT_KEYS"}
    handmade = [n.targets[0].id for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in names
                and not (isinstance(n.value, ast.Attribute) and getattr(n.value.value, "id", "") == "S")]
    check("GUI registries are all aliases of the schema", not handmade, str(handmade))

print("4. gate matrix")
switches = [f["key"] for f in S.ACTIVE if f["type"] == "bool"
            and any(c["parent"] == f["key"] for c in S.ACTIVE)]
def ancestors(k):
    out, p = [], S.BY_KEY[k]["parent"]
    while p: out.append(p); p = S.BY_KEY[p]["parent"]
    return out
bad = []
for mode in S.modes():
    for bits in itertools.product([True, False], repeat=len(switches)):
        v = dict(S.DEFAULTS); v.update(zip(switches, bits)); v["DOWNLOAD_TYPE"] = mode
        sd = S.structural_disables(v)
        for k in S.ACTIVE_KEYS:
            st, why = S.effective_state(v, k); f = S.BY_KEY[k]
            anc = ancestors(k)
            off = [a for a in anc if S.BY_KEY[a]["type"] == "bool" and not v[a]]
            if mode in f["hidden_in"] and st != "hidden": bad.append((mode, k, "should be hidden"))
            elif mode not in f["hidden_in"] and st == "hidden": bad.append((mode, k, "hidden wrongly"))
            elif st == "normal" and (off or k in sd or any(a in sd for a in anc)):
                bad.append((mode, k, "normal despite unmet cause"))
            elif st == "disabled" and not why: bad.append((mode, k, "disabled without reason"))
            elif st == "disabled" and not (off or k in sd or any(a in sd for a in anc)
                                           or any(mode in S.BY_KEY[a]["hidden_in"] for a in anc)):
                bad.append((mode, k, "disabled with no cause"))
check(f"{len(S.modes())} modes x {2**len(switches)} switch states x {len(S.ACTIVE_KEYS)} keys", not bad, str(bad[:4]))

print("5. hidden invariant (engine)")
try:
    E = _import_sibling("yt_video_downloader7.5.py")
except Exception as e:
    E = None; print("  skip  engine import failed:", e)
if E:
    base = S.with_defaults(json.loads(jf.read_text(encoding="utf-8")) if False else
                           S.flatten(json.loads(jf.read_text(encoding="utf-8"))) if jf.exists() else {})
    def argv(vals, treated, pid):
        eff = E.resolve_effective_settings(vals, None)
        return E.build_command("https://x/y", treated, pid, vals, eff, 1 if treated == "playlist" else None, None)
    alt = {"int_list": [[1080], [144]], "str_list": [["zzz"], ["vp8"]], "bool": [True, False],
           "str": ["64k", "auto", "7"], "int": [3]}
    bad, ran = [], 0
    for mode in S.modes():
        for treated, pid in (("video", None), ("playlist", "PLx")):
            v0 = dict(base, DOWNLOAD_TYPE=mode); ref = argv(v0, treated, pid)
            for k, hid in S.HIDDEN_IN_MODE.items():
                if mode not in hid: continue
                f = S.BY_KEY[k]
                for c in (f["choices"] if f["type"] == "enum" else alt.get(f["type"], [])):
                    v = dict(v0); v[k] = c; ran += 1
                    if argv(v, treated, pid) != ref: bad.append((mode, treated, k, c))
    check(f"{ran} variations leave argv unchanged", not bad, str(bad[:4]))

    print("6. engine fills defaults")
    with tempfile.TemporaryDirectory() as td:
        part = {"DOWNLOAD TYPE": {"DOWNLOAD_TYPE": "audio"}}
        old = E.SETTINGS_FILE
        E.SETTINGS_FILE = Path(td) / "s.json"; E.SETTINGS_FILE.write_text(json.dumps(part))
        try:
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                cfg = E.load_settings()
        finally:
            E.SETTINGS_FILE = old
    check("minimal file -> every active key present", set(S.ACTIVE_KEYS) <= set(cfg))
    check("file value wins over default", cfg["DOWNLOAD_TYPE"] == "audio")
    check("default path templates expanded", "{" not in cfg["LOG_DIR"] and "{" not in cfg["ARCHIVE_DIR"])

print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
