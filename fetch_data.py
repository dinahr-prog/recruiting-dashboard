"""
fetch_data.py — fetches PeopleForce data and injects into template.html → index.html
Run: python fetch_data.py
Env var: PEOPLEFORCE_API_KEY
"""
import os, json, time
import urllib.request, urllib.error

API_KEY  = os.environ.get("PEOPLEFORCE_API_KEY", "")
BASE_URL = "https://app.peopleforce.io/api/public/v3"
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


def fetch_all_pages(path, per_page=100, max_pages=50):
    results = []
    for page in range(1, max_pages + 1):
        data = get(path, f"per_page={per_page}&page={page}")
        if not data or not data.get("data"):
            break
        results.extend(data["data"])
        pagination = data.get("metadata", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        time.sleep(0.15)
    return results


# ── 1. Vacancy IDs ──────────────────────────────────────────────────
print("Fetching vacancies list...")
vac_list = get("recruitment/vacancies", "per_page=100")
vac_ids  = [v["id"] for v in (vac_list.get("data") or [])]
print(f"  Found {len(vac_ids)} vacancies")

# ── 2. Full vacancy details (includes pipeline stages) ──────────────
print("Fetching vacancy details...")
vacs_js = []
for vid in vac_ids:
    d = get(f"recruitment/vacancies/{vid}")
    if not d or not d.get("data"):
        continue
    v = d["data"]
    pipeline = v.get("recruitment_pipeline") or {}
    stages   = [{"id": s["id"], "name": s["name"], "pos": s["position"]}
                for s in pipeline.get("stages", [])]
    vacs_js.append({
        "id":       v["id"],
        "title":    v["title"],
        "state":    v["state"],
        "dept":     (v.get("department") or {}).get("name", ""),
        "pipeName": pipeline.get("name", ""),
        "stages":   stages,
        "appCount": v.get("applications_count", 0),
        "openedAt": (v.get("opened_at") or "")[:10],
    })
    time.sleep(0.1)
print(f"  Loaded {len(vacs_js)} vacancy details")

# ── 3. Applications per vacancy ────────────────────────────────────
print("Fetching applications...")
apps_raw = []
for vid in vac_ids:
    page = 1
    while True:
        data = get(f"recruitment/vacancies/{vid}/applications",
                   f"per_page=200&page={page}")
        if not data or not data.get("data"):
            break
        for a in data["data"]:
            apps_raw.append({
                "app_id":       a["id"],
                "applicant_id": a["applicant"]["id"],
                "vac_id":       vid,
                "stage_name":   (a.get("pipeline_state") or {}).get("name", ""),
                "created_at":   (a.get("created_at") or "")[:10],
                "updated_at":   (a.get("updated_at") or "")[:10],
            })
        pagination = data.get("metadata", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        page += 1
        time.sleep(0.1)
print(f"  Loaded {len(apps_raw)} applications")

# ── 4. Candidates → full enriched map (with custom fields + tags) ───
print("Fetching candidates (source, recruiter, custom fields, tags)...")
cand_map = {}   # candidate_id → enriched dict
candidates_list = fetch_all_pages("recruitment/candidates", per_page=100, max_pages=40)

# Fetch full detail for each to get custom_fields and tags
print(f"  Fetching full details for {len(candidates_list)} candidates...")
for c in candidates_list:
    cid = str(c["id"])
    detail = get(f"recruitment/candidates/{cid}")
    d = (detail or {}).get("data") or c

    # source
    source = (d.get("source") or "").strip()

    # recruiter (created_by)
    cb = d.get("created_by") or {}
    recruiter = ""
    if cb:
        first = cb.get("first_name") or ""
        last  = cb.get("last_name") or ""
        recruiter = f"{first} {last}".strip() or cb.get("email", "")

    # custom fields: Sourcer + Source type
    sourcer     = ""
    source_type = ""
    for cf in (d.get("custom_fields") or []):
        if cf.get("internal_name") == "sourcer":
            sourcer = cf.get("value") or ""
        elif cf.get("internal_name") == "source_type":
            source_type = cf.get("value") or ""

    # tags → extract rejection type and reason
    tags = [t.get("name", "") for t in (d.get("tags") or [])]
    reject_who    = ""
    reject_reason = ""
    for tag in tags:
        tl = tag.lower()
        if "ми відмовили" in tl:
            reject_who = "ми відмовили"
        elif "кандидат відмовився" in tl:
            reject_who = "кандидат відмовився"
        if "причина:" in tl:
            reject_reason = tag.split("причина:")[-1].strip()

    cand_map[cid] = {
        "src":  source,
        "rec":  recruiter,
        "scr":  sourcer,       # sourcer (researcher)
        "stype":source_type,   # Inbound / Outbound
        "rw":   reject_who,    # хто відмовив
        "rr":   reject_reason, # причина відмови
    }
    time.sleep(0.07)

print(f"  Mapped {len(cand_map)} candidates with full details")

# ── 5. Build APPS list with all enriched fields ─────────────────────
apps_js = []
for a in apps_raw:
    cid = str(a["applicant_id"])
    cm  = cand_map.get(cid, {})
    apps_js.append({
        "id":    a["app_id"],
        "vid":   a["vac_id"],
        "sn":    a["stage_name"],
        "src":   cm.get("src", ""),
        "rec":   cm.get("rec", ""),
        "scr":   cm.get("scr", ""),
        "stype": cm.get("stype", ""),
        "rw":    cm.get("rw", ""),
        "rr":    cm.get("rr", ""),
        "ca":    a["created_at"],          # created (application added)
        "ua":    a.get("updated_at", ""),  # updated (last stage move)
    })

# ── 6. Inject into template ────────────────────────────────────────
print("Building index.html...")
with open("template.html", encoding="utf-8") as f:
    template = f.read()

data_block = (
    f"const VACS={json.dumps(vacs_js, ensure_ascii=False)};\n"
    f"const APPS={json.dumps(apps_js, ensure_ascii=False)};"
)

if "__RECRUITING_DATA__" not in template:
    raise ValueError("Placeholder __RECRUITING_DATA__ not found in template.html")

html = template.replace("__RECRUITING_DATA__", data_block)

from datetime import datetime, timezone, timedelta
kyiv_time = datetime.now(timezone.utc) + timedelta(hours=3)
build_time = kyiv_time.strftime("%d.%m.%Y %H:%M (Київ)")
html = html.replace("__BUILD_TIME__", build_time)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

size_kb = round(len(html.encode()) / 1024, 1)
print(f"Done! index.html written ({size_kb} KB)")
print(f"  Vacancies: {len(vacs_js)}, Applications: {len(apps_js)}, Candidates enriched: {len(cand_map)}")
