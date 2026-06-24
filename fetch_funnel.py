"""
fetch_funnel.py — fetches PeopleForce data and injects into funnel_template.html → funnel.html
Run: python fetch_funnel.py
Env var: PEOPLEFORCE_API_KEY
"""
import os, json, time
import urllib.request, urllib.error

API_KEY  = os.environ.get("PEOPLEFORCE_API_KEY", "")
BASE_URL = "https://punch.peopleforce.io/api/public/v3"
HEADERS  = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def get(path, params=""):
    url = f"{BASE_URL}/{path}{'?' + params if params else ''}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {path}")
        return None
    except Exception as e:
        print(f"  Error {path}: {e}")
        return None


def fetch_all_pages(path, per_page=100, extra_params="", max_pages=200):
    results = []
    for page in range(1, max_pages + 1):
        params = f"per_page={per_page}&page={page}"
        if extra_params:
            params += f"&{extra_params}"
        data = get(path, params)
        if not data or not data.get("data"):
            break
        results.extend(data["data"])
        pagination = data.get("metadata", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        time.sleep(0.12)
    return results


def is_reject_stage(name):
    nl = (name or "").lower()
    return any(kw in nl for kw in ["reject", "відмов", "відмова", "disqualif"])


# ── 1. All vacancies ────────────────────────────────────────────────
print("Fetching vacancies...")
vac_raw = fetch_all_pages("recruitment/vacancies", per_page=100)
print(f"  Found {len(vac_raw)} vacancies")

# ── 2. Full vacancy details with pipeline stages ─────────────────────
print("Fetching vacancy details + pipeline stages...")
vacancies = []
pipeline_cache = {}  # pipeline_id → stages list

for v in vac_raw:
    d = get(f"recruitment/vacancies/{v['id']}")
    if not d or not d.get("data"):
        continue
    v = d["data"]
    pipeline = v.get("recruitment_pipeline") or {}
    pid = pipeline.get("id")

    stages = []
    for s in pipeline.get("stages", []):
        is_rej = is_reject_stage(s["name"])
        stages.append({
            "id":   s["id"],
            "name": s["name"],
            "pos":  s["position"],
            "rej":  is_rej,
        })

    if pid:
        pipeline_cache[pid] = stages

    # recruiters (hiring_lead + collaborators)
    recruiters = []
    hl = v.get("hiring_lead") or {}
    if hl:
        name = f"{hl.get('first_name','')} {hl.get('last_name','')}".strip()
        if name:
            recruiters.append(name)
    for collab in (v.get("collaborators") or []):
        name = f"{collab.get('first_name','')} {collab.get('last_name','')}".strip()
        if name and name not in recruiters:
            recruiters.append(name)

    vacancies.append({
        "id":    v["id"],
        "title": v["title"],
        "state": v["state"],
        "pid":   pid,
        "pname": pipeline.get("name", ""),
        "stages": stages,
        "recs":  recruiters,
        "appCnt": v.get("applications_count", 0),
    })
    time.sleep(0.1)

print(f"  Loaded {len(vacancies)} vacancy details")

vac_by_id = {v["id"]: v for v in vacancies}

# ── 3. Applications per vacancy ─────────────────────────────────────
print("Fetching applications per vacancy...")
apps = []

for vac in vacancies:
    vid = vac["id"]
    stages = vac["stages"]
    stage_by_id = {s["id"]: s for s in stages}

    page = 1
    while True:
        data = get(f"recruitment/vacancies/{vid}/applications",
                   f"per_page=200&page={page}")
        if not data or not data.get("data"):
            break
        for a in data["data"]:
            ps = a.get("pipeline_state") or {}
            sid = ps.get("id")
            sn  = ps.get("name", "")
            sp  = stage_by_id.get(sid, {}).get("pos", 0) if sid else 0
            sr  = stage_by_id.get(sid, {}).get("rej", False) if sid else False

            dis_at = (a.get("disqualified_at") or "")[:10] or None
            apps.append({
                "id":  a["id"],
                "cid": a["applicant"]["id"],    # candidate id
                "vid": vid,
                "sid": sid,
                "sn":  sn,
                "sp":  sp,
                "sr":  sr,  # is reject stage
                "ca":  (a.get("created_at") or "")[:10],
                "ua":  (a.get("updated_at") or "")[:10],
                "da":  dis_at,
            })
        pagination = data.get("metadata", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        page += 1
        time.sleep(0.1)

print(f"  Loaded {len(apps)} applications")

# ── 4. Candidate details → source, recruiter ─────────────────────────
print("Fetching candidate list (source + recruiter)...")
cand_list = fetch_all_pages("recruitment/candidates", per_page=100, max_pages=200)
cand_map = {}
for c in cand_list:
    cid = c["id"]
    cb  = c.get("created_by") or {}
    rec = ""
    if cb:
        rec = f"{cb.get('first_name','')} {cb.get('last_name','')}".strip() or cb.get("email","")
    src = (c.get("source") or "").strip()

    # custom fields for sourcer
    sourcer = ""
    for cf in (c.get("custom_fields") or []):
        if cf.get("internal_name") == "sourcer":
            sourcer = (cf.get("value") or "").strip()
            break

    cand_map[cid] = {"src": src, "rec": rec, "scr": sourcer}

print(f"  Mapped {len(cand_map)} candidates")

# ── 5. Enrich apps with candidate data ──────────────────────────────
for a in apps:
    cm = cand_map.get(a["cid"], {})
    a["src"] = cm.get("src", "")
    a["rec"] = cm.get("rec", "")
    a["scr"] = cm.get("scr", "")

# ── 6. Build compact output ─────────────────────────────────────────
# Only keep fields needed by UI
apps_out = []
for a in apps:
    apps_out.append({
        "id":  a["id"],
        "vid": a["vid"],
        "sid": a["sid"],
        "sn":  a["sn"],
        "sp":  a["sp"],
        "sr":  1 if a["sr"] else 0,
        "ca":  a["ca"],
        "ua":  a["ua"],
        "da":  a["da"],
        "src": a["src"],
        "rec": a["rec"],
        "scr": a["scr"],
    })

vacs_out = []
for v in vacancies:
    vacs_out.append({
        "id":    v["id"],
        "title": v["title"],
        "state": v["state"],
        "pid":   v["pid"],
        "pname": v["pname"],
        "stages": v["stages"],
        "recs":  v["recs"],
    })

# ── 7. Inject into template ─────────────────────────────────────────
print("Building funnel.html...")
with open("funnel_template.html", encoding="utf-8") as f:
    template = f.read()

data_block = (
    f"const VACS={json.dumps(vacs_out, ensure_ascii=False)};\n"
    f"const APPS={json.dumps(apps_out, ensure_ascii=False)};"
)

if "__FUNNEL_DATA__" not in template:
    raise ValueError("Placeholder __FUNNEL_DATA__ not found in funnel_template.html")

html = template.replace("__FUNNEL_DATA__", data_block)

from datetime import datetime, timezone, timedelta
kyiv_time = datetime.now(timezone.utc) + timedelta(hours=3)
build_time = kyiv_time.strftime("%d.%m.%Y %H:%M (Київ)")
html = html.replace("__BUILD_TIME__", build_time)

with open("funnel.html", "w", encoding="utf-8") as f:
    f.write(html)

size_kb = round(len(html.encode()) / 1024, 1)
print(f"Done! funnel.html written ({size_kb} KB)")
print(f"  Vacancies: {len(vacs_out)}, Apps: {len(apps_out)}, Candidates mapped: {len(cand_map)}")
