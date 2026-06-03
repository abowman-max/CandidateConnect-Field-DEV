# C4.5 FIELD APP MOBILE WORKFLOW RESTORE BASELINE
# Prepared from app(177).py
# Next stage: Streets -> Houses -> Voter Card workflow

# Candidate Connect FIELD APP — separate mobile/offline shell
# C4 Split Architecture v1
#
# Separate from the main Candidate Connect web app.
# Reads:
#   app_state/security_store.json
#   app_state/mobile_assignments/<campaign_id>/<username>.json
# Writes:
#   app_state/mobile_results/<campaign_id>.json
#
# This version stages field results only. It does not update voter records.

import json
import os
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st


st.set_page_config(
    page_title="Candidate Connect Field",
    page_icon="📱",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {display: none !important;}
    .block-container {max-width: 760px; padding-top: 1.1rem;}
    h1, h2, h3 {color: #071f45;}
    .stButton > button {
        background: #a80f18 !important;
        color: white !important;
        border: 1px solid #7c0b12 !important;
        border-radius: 9px !important;
        font-weight: 700 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _secret(name: str, default: str | None = None) -> str | None:
    try:
        value = st.secrets.get(name)  # type: ignore[attr-defined]
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name, default)


def public_r2_base() -> str:
    return (
        _secret("CANDIDATE_CONNECT_R2_PUBLIC_URL")
        or _secret("R2_PUBLIC_URL")
        or _secret("R2_BASE_URL")
        or "https://pub-376c4497d59b4a7988a8af29700531e0.r2.dev"
    ).rstrip("/")


def _r2_bucket() -> str:
    env_name = (_secret("CANDIDATE_CONNECT_ENV") or _secret("APP_ENV") or "DEV").upper()
    if env_name == "LIVE":
        return _secret("R2_LIVE_BUCKET_NAME") or _secret("CANDIDATE_CONNECT_LIVE_BUCKET") or "candidate-connect-data"
    return _secret("R2_DEV_BUCKET_NAME") or _secret("CANDIDATE_CONNECT_DEV_BUCKET") or _secret("R2_BUCKET_NAME") or "candidate-connect-data-dev"


def r2_url(key: str) -> str:
    return f"{public_r2_base()}/{str(key).lstrip('/')}"


def read_json_public(key: str, default: Any) -> Any:
    try:
        r = requests.get(r2_url(key), timeout=12)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return default


def put_json_r2(key: str, payload: dict) -> tuple[bool, str]:
    try:
        import boto3  # type: ignore
    except Exception as exc:
        return False, f"boto3 unavailable: {exc}"

    endpoint_url = _secret("R2_ENDPOINT_URL") or _secret("CLOUDFLARE_R2_ENDPOINT_URL")
    account_id = _secret("R2_ACCOUNT_ID") or _secret("CLOUDFLARE_ACCOUNT_ID")
    if not endpoint_url and account_id:
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    access_key = _secret("R2_ACCESS_KEY_ID") or _secret("AWS_ACCESS_KEY_ID")
    secret_key = _secret("R2_SECRET_ACCESS_KEY") or _secret("AWS_SECRET_ACCESS_KEY")
    bucket = _r2_bucket()

    if not endpoint_url or not access_key or not secret_key or not bucket:
        return False, "R2 write credentials are not configured for the field app."

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        body = json.dumps(payload or {}, ensure_ascii=False, indent=2).encode("utf-8")
        client.put_object(
            Bucket=bucket,
            Key=str(key).lstrip("/"),
            Body=body,
            ContentType="application/json",
        )
        return True, f"Synced {key}"
    except Exception as exc:
        return False, str(exc)


def password_hash(username: str, password: str) -> str:
    salt = f"candidate-connect-v1::{str(username).strip().lower()}::"
    return hashlib.sha256((salt + str(password or "")).encode("utf-8")).hexdigest()


def campaign_slug(value: str) -> str:
    s = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "default"


def clean_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:
            return ""
    except Exception:
        pass
    return str(value).strip()


def _first_value(row: dict, keys: list[str]) -> str:
    for key in keys:
        val = clean_value(row.get(key))
        if val:
            return val
    return ""


def _assignment_payload(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    pkg = item.get("package") if isinstance(item.get("package"), dict) else {}
    return pkg or item


def _assignment_households(item: dict) -> list[dict]:
    if not isinstance(item, dict):
        return []
    pkg = _assignment_payload(item)
    households = item.get("households") if isinstance(item.get("households"), list) else None
    if households is None:
        households = pkg.get("households") if isinstance(pkg.get("households"), list) else []
    return [x for x in households if isinstance(x, dict)]


def _assignment_voters(item: dict) -> list[dict]:
    if not isinstance(item, dict):
        return []
    pkg = _assignment_payload(item)
    voters = item.get("voters") if isinstance(item.get("voters"), list) else None
    if voters is None:
        voters = pkg.get("voters") if isinstance(pkg.get("voters"), list) else []
    return [x for x in voters if isinstance(x, dict)]


def _household_key(row: dict) -> str:
    return _first_value(row, ["Household Key", "household_key", "household_id", "HouseholdID", "HH_KEY", "hh_key"])


def _voter_household_key(row: dict) -> str:
    return _first_value(row, ["Household Key", "household_key", "household_id", "HouseholdID", "HH_KEY", "hh_key"])


def _voter_id(row: dict) -> str:
    return _first_value(row, ["voter_id", "Voter ID", "VoterID", "PA Voter ID", "SURE_ID", "ID"])


def _voter_name(row: dict) -> str:
    name = _first_value(row, ["FullName", "Full Name", "name", "Name", "Voter Name"])
    if name:
        return name
    parts = [_first_value(row, ["First Name", "FirstName", "first_name"]), _first_value(row, ["Last Name", "LastName", "last_name"])]
    return " ".join([x for x in parts if x]).strip()


def _household_label(row: dict, idx: int, voters_for_hh: list[dict]) -> str:
    knock = _first_value(row, ["Knock Order", "knock_order", "order"])
    address = _first_value(row, ["Address", "address", "Residence Address", "Street Address"]) or "Unknown address"
    city = _first_value(row, ["City", "city", "Municipality", "municipality"])
    count = clean_value(row.get("Voters") or row.get("voter_count") or len(voters_for_hh))
    prefix = f"#{knock} — " if knock else f"#{idx + 1} — "
    suffix = f" — {count} voter(s)" if count else ""
    if city:
        return f"{prefix}{address}, {city}{suffix}"
    return f"{prefix}{address}{suffix}"


def _voter_label(row: dict, idx: int) -> str:
    name = _voter_name(row) or f"Voter {idx + 1}"
    party = _first_value(row, ["Party", "party", "CalculatedParty"])
    age = _first_value(row, ["Age", "age"])
    bits = [x for x in [party, f"Age {age}" if age else ""] if x]
    return f"{name} ({', '.join(bits)})" if bits else name


def _assignment_result_options(item: dict) -> list[str]:
    pkg = _assignment_payload(item)
    schema = pkg.get("mobile_schema") if isinstance(pkg.get("mobile_schema"), dict) else {}
    opts = schema.get("result_options") or pkg.get("result_options") or ["Favorable", "Undecided", "Against", "Not Home", "Yard Sign", "Needs Follow-up"]
    opts = [clean_value(x) for x in opts if clean_value(x)]
    return opts or ["Favorable", "Undecided", "Against", "Not Home", "Yard Sign", "Needs Follow-up"]


def load_security_store() -> dict:
    for key in ("app_state/security_store.json", "app_state/security_users.json"):
        raw = read_json_public(key, {})
        if isinstance(raw, dict) and raw.get("users"):
            raw.setdefault("campaigns", {})
            return raw
    return {"users": {}, "campaigns": {}}


def current_user() -> dict:
    return st.session_state.get("field_user") or {}


def current_campaign_id() -> str:
    u = current_user()
    cid = u.get("campaign_id") or u.get("campaign") or u.get("campaign_name") or ""
    return campaign_slug(cid)


def login_screen() -> None:
    st.title("Candidate Connect Field")
    st.caption("Download assignments on Wi‑Fi, record field results, then sync when back online.")

    with st.form("field_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log In")

    if not submitted:
        st.stop()

    uname = str(username or "").strip().lower()
    store = load_security_store()
    user = (store.get("users") or {}).get(uname)
    if not user or user.get("disabled"):
        st.error("Invalid login.")
        st.stop()

    if user.get("password_hash") != password_hash(uname, password):
        st.error("Invalid login.")
        st.stop()

    role = str(user.get("role") or "")
    if role not in {"Field User", "Campaign Admin", "Manager", "Super Admin"}:
        st.error("This account does not have field-app access.")
        st.stop()

    campaign_name = user.get("campaign") or user.get("campaign_name") or ""
    cid = user.get("campaign_id") or campaign_slug(campaign_name)
    user = dict(user)
    user["username"] = uname
    user["campaign_id"] = cid
    st.session_state["field_user"] = user
    st.rerun()


LOCAL_DIR = Path.home() / ".candidate_connect_field"
LOCAL_DIR.mkdir(parents=True, exist_ok=True)


def local_results_path(campaign_id: str) -> Path:
    return LOCAL_DIR / f"mobile_results_{campaign_id}.json"


def empty_results(campaign_id: str) -> dict:
    return {
        "campaign_id": campaign_id,
        "queued": [],
        "synced": [],
        "failed": [],
        "last_sync": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def load_local_results(campaign_id: str) -> dict:
    path = local_results_path(campaign_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("campaign_id", campaign_id)
                raw.setdefault("queued", [])
                raw.setdefault("synced", [])
                raw.setdefault("failed", [])
                raw.setdefault("last_sync", "")
                return raw
        except Exception:
            pass
    return empty_results(campaign_id)


def save_local_results(campaign_id: str, payload: dict) -> None:
    payload["campaign_id"] = campaign_id
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    local_results_path(campaign_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_server_results(campaign_id: str) -> dict:
    raw = read_json_public(f"app_state/mobile_results/{campaign_id}.json", {})
    if isinstance(raw, dict) and raw:
        raw.setdefault("campaign_id", campaign_id)
        raw.setdefault("queued", [])
        raw.setdefault("synced", [])
        raw.setdefault("failed", [])
        raw.setdefault("last_sync", "")
        return raw
    return empty_results(campaign_id)


def load_assignments(campaign_id: str, username: str | None = None) -> list[dict]:
    """Load only the assignments published for this Field App user.

    C4.3 path:
      app_state/mobile_assignments/<campaign_id>/<username>.json

    Legacy fallback is kept for early testing only.
    """
    uname = campaign_slug(username or (current_user().get("username") if current_user() else ""))
    candidate_keys = []
    if uname:
        candidate_keys.append(f"app_state/mobile_assignments/{campaign_id}/{uname}.json")
    candidate_keys.append(f"app_state/mobile_assignments/{campaign_id}.json")  # legacy fallback

    for key in candidate_keys:
        raw = read_json_public(key, {})
        if isinstance(raw, dict):
            items = raw.get("assignments") or raw.get("work_items") or raw.get("items") or []
            if isinstance(items, list) and items:
                st.session_state["last_assignment_source_key"] = key
                return items
        if isinstance(raw, list) and raw:
            st.session_state["last_assignment_source_key"] = key
            return raw
    return []


def merge_results_for_sync(local_payload: dict, server_payload: dict) -> dict:
    merged = empty_results(local_payload.get("campaign_id") or server_payload.get("campaign_id") or "default")
    server_synced = server_payload.get("synced") or []
    server_failed = server_payload.get("failed") or []
    local_queued = local_payload.get("queued") or []
    merged["synced"] = list(server_synced) + [
        {**item, "synced_at": datetime.now(timezone.utc).isoformat()}
        for item in local_queued
        if isinstance(item, dict)
    ]
    merged["failed"] = list(server_failed)
    merged["queued"] = []
    merged["last_sync"] = datetime.now().isoformat(timespec="seconds")
    return merged



# -----------------------------------------------------------------------------
# C4.5.2 Mobile workflow refinement
# My Lists -> Street rows -> House rows -> Household voter card -> Save -> Houses
# -----------------------------------------------------------------------------

CC_LOGO_B64 = """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"""

st.markdown(
    """
    <style>
    .stApp { background: #eee8d8; }
    .block-container {max-width: 780px; padding-top: .35rem; padding-left: .75rem; padding-right: .75rem;}
    h1, h2, h3 {color: #071f45; letter-spacing: -0.02em;}
    .cc-topbar {display:flex; align-items:center; gap:.65rem; margin:.15rem 0 .65rem 0;}
    .cc-logo {height:34px; max-width:128px; object-fit:contain;}
    .cc-mini {font-weight:900; color:#a80f18; font-size:1.0rem; letter-spacing:-.04em;}
    .cc-title {font-size:1.15rem; font-weight:900; color:#071f45; line-height:1.1;}
    .cc-subtitle {color:#69707d; font-size:.86rem; margin-top:-.25rem; margin-bottom:.75rem;}
    .cc-card {background:#fffdf7; border:1px solid #d8d2c2; border-radius:10px; padding:.72rem; margin:.45rem 0; box-shadow:0 1px 4px rgba(0,0,0,.03);}
    .cc-muted {color:#6f7581;}
    .cc-kpi {background:#f8f5ed; border:1px solid #ddd6c8; border-radius:8px; padding:.4rem .55rem; text-align:center;}
    .cc-table-head {border-bottom:1px solid #cfc8b8; padding:.35rem 0 .45rem 0; font-weight:900; color:#252938;}
    .cc-row {border-bottom:1px solid #ded8cc; padding:.14rem 0; min-height: 35px; display:flex; align-items:center;}
    .cc-row:hover {background:#f8f5ee;}
    .cc-link button {text-align:left !important; color:#064aa8 !important; background:transparent !important; border:0 !important; box-shadow:none !important; padding:.16rem 0 !important; font-weight:800 !important; min-height:1.55rem !important; line-height:1.05 !important; white-space:normal !important;}
    .cc-link button:hover {text-decoration:underline !important; background:transparent !important; color:#003c82 !important;}
    .stButton > button {border-radius:8px; min-height:2.1rem; font-weight:800;}
    div[data-testid="stMetric"] {background:transparent;}
    div[data-testid="stMetricValue"] {font-size:2.0rem; color:#252938;}
    .cc-back button, .cc-save button {background:#a80f18 !important; color:white !important; border:1px solid #7c0b12 !important; width:100%;}
    .cc-back button:hover, .cc-save button:hover {background:#8f0d15 !important; color:white !important;}
    .cc-secondary button {background:#fffdf7 !important; color:#071f45 !important; border:1px solid #c9c1af !important; width:100%;}
    .cc-center {text-align:center;}
    .cc-status-dot {font-size:.85rem;}
    .cc-legend {font-size:.84rem; line-height:1.45;}
    .cc-voter-card {background:#fffdf7; border:1px solid #d4cec0; border-radius:10px; padding:.75rem; margin:.55rem 0;}
    .cc-voter-name {font-weight:900; color:#252938; font-size:1.05rem;}
    .cc-voter-meta {color:#687080; font-size:.88rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _logo_html(title: str, subtitle: str = "") -> None:
    logo = f'<img class="cc-logo" src="data:image/png;base64,{CC_LOGO_B64}">' if CC_LOGO_B64 else '<div class="cc-mini">CC</div>'
    st.markdown(
        f"""
        <div class="cc-topbar">
          {logo}
          <div>
            <div class="cc-title">{title}</div>
            {f'<div class="cc-subtitle">{subtitle}</div>' if subtitle else ''}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _street_from_address(address: str) -> str:
    s = clean_value(address).upper()
    s = re.sub(r"^[0-9]+[A-Z]?\s+", "", s).strip()
    return s or "UNKNOWN STREET"


def _house_address(hh: dict) -> str:
    addr = _first_value(hh, ["Address", "address", "Residence Address", "Street Address"]).upper()
    city = _first_value(hh, ["City", "city", "Municipality", "municipality"]).upper()
    if addr and city and city not in addr:
        return f"{addr}, {city}"
    return addr or "UNKNOWN ADDRESS"


def _house_street(hh: dict) -> str:
    return _street_from_address(_first_value(hh, ["Address", "address", "Residence Address", "Street Address"]))


def _voter_party(v: dict) -> str:
    return _first_value(v, ["Party", "party", "CalculatedParty"]) or "—"


def _voter_age(v: dict) -> str:
    return _first_value(v, ["Age", "age"]) or "—"


def _mail_perm(v: dict) -> bool:
    val = _first_value(v, ["MB Perm", "MB_Perm", "mb_perm", "Mail Ballot Perm", "mail_ballot_perm", "PermMB"])
    return str(val).strip().upper() in {"Y", "YES", "TRUE", "1", "PERM"}


def _result_voter_key(item: dict) -> str:
    return clean_value(item.get("voter_id") or item.get("target_voter_id"))


def _result_house_key(item: dict) -> str:
    return clean_value(item.get("household_key") or item.get("target_household_key"))


def _queued_for_campaign() -> list[dict]:
    local = load_local_results(current_campaign_id())
    return [x for x in (local.get("queued") or []) + (local.get("synced") or []) if isinstance(x, dict)]


def _house_status(hh: dict, hh_voters: list[dict]) -> tuple[str, str]:
    results = _queued_for_campaign()
    hk = _household_key(hh)
    voter_ids = {_voter_id(v) for v in hh_voters if _voter_id(v)}
    done_voters = {_result_voter_key(r) for r in results if _result_voter_key(r) in voter_ids}
    hh_done = any(_result_house_key(r) == hk for r in results if hk)
    if hh_voters:
        if len(done_voters) >= len(voter_ids) and voter_ids:
            return "🟢", "Complete"
        if done_voters or hh_done:
            return "🟡", "In Progress"
        return "⚪", "Not Started"
    if hh_done:
        return "🟢", "Complete"
    return "⚪", "Not Started"


def _assignment_name(item: dict, fallback: str = "Assignment") -> str:
    pkg = _assignment_payload(item)
    assignment_meta = pkg.get("assignment") if isinstance(pkg.get("assignment"), dict) else {}
    return (
        clean_value(item.get("label"))
        or clean_value(item.get("assignment_name"))
        or clean_value(assignment_meta.get("name"))
        or clean_value(item.get("precinct"))
        or fallback
    )


def _assignment_struct(item: dict) -> dict:
    households = _assignment_households(item)
    voters = _assignment_voters(item)
    voter_map: dict[str, list[dict]] = {}
    addr_map: dict[str, list[dict]] = {}
    for v in voters:
        hk = _voter_household_key(v)
        if hk:
            voter_map.setdefault(hk, []).append(v)
        addr = _first_value(v, ["Address", "address", "Residence Address", "Street Address"]).upper()
        if addr:
            addr_map.setdefault(addr, []).append(v)
    street_map: dict[str, list[dict]] = {}
    for hh in households:
        street_map.setdefault(_house_street(hh), []).append(hh)
    return {"households": households, "voters": voters, "voter_map": voter_map, "addr_map": addr_map, "street_map": street_map}


def _hh_voters(hh: dict, data: dict) -> list[dict]:
    hk = _household_key(hh)
    out = data.get("voter_map", {}).get(hk, []) if hk else []
    if out:
        return out
    addr = _first_value(hh, ["Address", "address", "Residence Address", "Street Address"]).upper()
    return data.get("addr_map", {}).get(addr, []) if addr else []


def _go(screen: str, **kwargs) -> None:
    st.session_state["field_screen"] = screen
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


def _render_top_sync(user: dict, campaign_id: str) -> None:
    local = load_local_results(campaign_id)
    _logo_html("My Lists", f"Logged in as {user.get('username')} · Campaign: {campaign_id}")
    q, s, f = st.columns(3)
    q.metric("Queued", len(local.get("queued") or []))
    s.metric("Synced", len(local.get("synced") or []))
    f.metric("Failed", len(local.get("failed") or []))
    st.caption(f"Last Sync: {local.get('last_sync') or 'Never'}")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="cc-back">', unsafe_allow_html=True)
        if st.button("Refresh / Download Assignments", key="refresh_assignments"):
            assignments = load_assignments(campaign_id, user.get("username"))
            st.session_state["assignments"] = assignments
            st.success(f"Downloaded {len(assignments)} assignment item(s).")
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="cc-back">', unsafe_allow_html=True)
        if st.button("Sync Now", key="sync_now"):
            server = load_server_results(campaign_id)
            merged = merge_results_for_sync(load_local_results(campaign_id), server)
            ok, msg = put_json_r2(f"app_state/mobile_results/{campaign_id}.json", merged)
            if ok:
                save_local_results(campaign_id, merged)
                st.success("Synced field results.")
                st.rerun()
            else:
                local = load_local_results(campaign_id)
                local.setdefault("failed", []).append({"failed_at": datetime.now(timezone.utc).isoformat(), "reason": msg})
                save_local_results(campaign_id, local)
                st.error(f"Sync failed: {msg}")
        st.markdown('</div>', unsafe_allow_html=True)
    st.divider()


def _render_lists(assignments: list[dict]) -> None:
    st.subheader("My Lists / Assignments")
    if not assignments:
        st.info("No assignment package found yet. Build/assign work in the web app, then refresh here on Wi‑Fi.")
        return
    st.markdown('<div class="cc-table-head">', unsafe_allow_html=True)
    h = st.columns([4.5, 1.1, 1.1, 1.1, 1.2])
    h[0].markdown("List / Assignment")
    h[1].markdown('<div class="cc-center">Streets</div>', unsafe_allow_html=True)
    h[2].markdown('<div class="cc-center">Houses</div>', unsafe_allow_html=True)
    h[3].markdown('<div class="cc-center">Voters</div>', unsafe_allow_html=True)
    h[4].markdown('<div class="cc-center">Status</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    for i, item in enumerate(assignments):
        data = _assignment_struct(item)
        name = _assignment_name(item, f"Assignment {i+1}")
        cols = st.columns([4.5, 1.1, 1.1, 1.1, 1.2])
        with cols[0]:
            st.markdown('<div class="cc-link">', unsafe_allow_html=True)
            if st.button(name, key=f"open_list_{i}"):
                _go("streets", selected_assignment_idx=i)
            st.markdown('</div>', unsafe_allow_html=True)
        cols[1].markdown(f'<div class="cc-center">{len(data["street_map"]):,}</div>', unsafe_allow_html=True)
        cols[2].markdown(f'<div class="cc-center">{len(data["households"]):,}</div>', unsafe_allow_html=True)
        cols[3].markdown(f'<div class="cc-center">{len(data["voters"]):,}</div>', unsafe_allow_html=True)
        cols[4].markdown('<div class="cc-center" style="color:#16821e;font-weight:800;">Active ›</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="cc-card cc-legend"><b>Legend</b><br>
    <b>Status:</b> Active = ready to work<br>
    <b>Counts:</b> total in assignment package<br>
    Tap a list name to view streets.</div>
    """, unsafe_allow_html=True)


def _render_streets(assignments: list[dict], idx: int) -> None:
    item = assignments[idx]
    data = _assignment_struct(item)
    name = _assignment_name(item)
    _logo_html(f"Streets - {name}", f"{len(data['street_map']):,} streets · {len(data['households']):,} houses · {len(data['voters']):,} voters")
    h = st.columns([4.6, 1.15, 1.15, 1.3])
    h[0].markdown("**Street Name**")
    h[1].markdown('<div class="cc-center"><b>Houses</b></div>', unsafe_allow_html=True)
    h[2].markdown('<div class="cc-center"><b>Voters</b></div>', unsafe_allow_html=True)
    h[3].markdown('<div class="cc-center"><b>Complete</b></div>', unsafe_allow_html=True)
    st.markdown('<hr style="margin:.2rem 0 .35rem 0;border:0;border-top:1px solid #cfc8b8;">', unsafe_allow_html=True)
    for street in sorted(data["street_map"].keys()):
        hhs = data["street_map"][street]
        voter_count = sum(len(_hh_voters(hh, data)) for hh in hhs)
        complete = sum(1 for hh in hhs if _house_status(hh, _hh_voters(hh, data))[1] == "Complete")
        cols = st.columns([4.6, 1.15, 1.15, 1.3])
        with cols[0]:
            st.markdown('<div class="cc-link">', unsafe_allow_html=True)
            if st.button(street, key=f"street_{idx}_{street}"):
                _go("houses", selected_assignment_idx=idx, selected_street=street)
            st.markdown('</div>', unsafe_allow_html=True)
        cols[1].markdown(f'<div class="cc-center">{len(hhs):,}</div>', unsafe_allow_html=True)
        cols[2].markdown(f'<div class="cc-center">{voter_count:,}</div>', unsafe_allow_html=True)
        cols[3].markdown(f'<div class="cc-center">{complete:,} / {len(hhs):,} ›</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="cc-card cc-legend"><b>Legend</b><br>
    <b>Houses:</b> total houses on street<br><b>Voters:</b> total voters on street<br>
    <b>Complete:</b> houses completed / total houses</div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="cc-back">', unsafe_allow_html=True)
    if st.button("← Back to My Lists", key="back_lists"):
        _go("lists")
    st.markdown('</div>', unsafe_allow_html=True)


def _render_houses(assignments: list[dict], idx: int, street: str) -> None:
    item = assignments[idx]
    data = _assignment_struct(item)
    name = _assignment_name(item)
    hhs = sorted(data["street_map"].get(street, []), key=lambda hh: _house_address(hh))
    voter_count = sum(len(_hh_voters(hh, data)) for hh in hhs)
    _logo_html(f"Houses - {street}", f"{name} · {len(hhs):,} houses · {voter_count:,} voters")
    h = st.columns([5.2, 1.3, 2.0])
    h[0].markdown("**Address**")
    h[1].markdown('<div class="cc-center"><b>Voters</b></div>', unsafe_allow_html=True)
    h[2].markdown('<div class="cc-center"><b>Status</b></div>', unsafe_allow_html=True)
    st.markdown('<hr style="margin:.2rem 0 .35rem 0;border:0;border-top:1px solid #cfc8b8;">', unsafe_allow_html=True)
    for j, hh in enumerate(hhs):
        hv = _hh_voters(hh, data)
        dot, status = _house_status(hh, hv)
        address = _house_address(hh)
        cols = st.columns([5.2, 1.3, 2.0])
        with cols[0]:
            st.markdown('<div class="cc-link">', unsafe_allow_html=True)
            if st.button(address, key=f"house_{idx}_{street}_{j}"):
                _go("voter_card", selected_assignment_idx=idx, selected_street=street, selected_house_idx=j)
            st.markdown('</div>', unsafe_allow_html=True)
        cols[1].markdown(f'<div class="cc-center">{len(hv):,}</div>', unsafe_allow_html=True)
        cols[2].markdown(f'<div class="cc-center">{dot} {status} ›</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="cc-card cc-legend"><b>Legend - Status</b><br>
    ⚪ Not Started = no result saved<br>🟡 In Progress = at least one voter/household result saved<br>🟢 Complete = all voters completed</div>
    <div class="cc-card cc-legend"><b>Column / Icon Legend</b><br>
    <b>F</b> = Favorable &nbsp;&nbsp; <b>U</b> = Undecided &nbsp;&nbsp; <b>A</b> = Against &nbsp;&nbsp; <b>NH</b> = Not Home<br>
    <b>YS</b> = Yard Sign &nbsp;&nbsp; <b>FU</b> = Follow Up Needed &nbsp;&nbsp; <b>✉</b> = Mail Ballot Interest / MB &nbsp;&nbsp; <b>V</b> = Volunteer Interest</div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="cc-back">', unsafe_allow_html=True)
    if st.button("← Back to Streets", key="back_streets"):
        _go("streets", selected_assignment_idx=idx)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_voter_card(assignments: list[dict], idx: int, street: str, house_idx: int) -> None:
    item = assignments[idx]
    data = _assignment_struct(item)
    name = _assignment_name(item)
    hhs = sorted(data["street_map"].get(street, []), key=lambda hh: _house_address(hh))
    if house_idx >= len(hhs):
        _go("houses", selected_assignment_idx=idx, selected_street=street)
    hh = hhs[house_idx]
    voters = _hh_voters(hh, data)
    address = _house_address(hh)
    _logo_html(f"Voters - {address}", f"{name} · {len(voters):,} voter(s) at this address")

    if voters:
        for n, v in enumerate(voters, start=1):
            mb = " ✉" if _mail_perm(v) else ""
            st.markdown(
                f"""
                <div class="cc-voter-card">
                  <div class="cc-voter-name">{n}. {_voter_name(v) or f'Voter {n}'}{mb}</div>
                  <div class="cc-voter-meta">Party: {_voter_party(v)} · Age: {_voter_age(v)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.warning("No voter detail is available for this household. You can save a household-level result.")

    st.markdown("#### Record Results for This Household")
    with st.form("save_household_results"):
        selected_ids: list[str] = []
        if voters:
            st.caption("Select one or more voters to record the same result.")
            for n, v in enumerate(voters):
                vid = _voter_id(v) or f"idx-{n}"
                label = f"{_voter_name(v) or f'Voter {n+1}'} — {_voter_party(v)} · Age {_voter_age(v)}"
                if st.checkbox(label, value=True, key=f"sel_v_{idx}_{street}_{house_idx}_{n}"):
                    selected_ids.append(vid)
        else:
            st.caption("This will save a household-level result.")
        result = st.radio("Result", ["Favorable", "Undecided", "Against", "Not Home"], horizontal=True)
        c1, c2 = st.columns(2)
        yard_sign = c1.checkbox("Yard Sign")
        follow_up = c2.checkbox("Follow Up Needed")
        mb_interest = c1.checkbox("✉ Mail Ballot Interest")
        volunteer_interest = c2.checkbox("Volunteer Interest")
        notes = st.text_area("Notes", height=72)
        st.markdown('<div class="cc-save">', unsafe_allow_html=True)
        save = st.form_submit_button("Save Results for Selected Voters")
        st.markdown('</div>', unsafe_allow_html=True)

    if save:
        if voters and not selected_ids:
            st.error("Select at least one voter, or go back and choose another house.")
            st.stop()
        local = load_local_results(campaign_id)
        local.setdefault("queued", [])
        assignment_payload = _assignment_payload(item)
        assignment_meta = assignment_payload.get("assignment") if isinstance(assignment_payload.get("assignment"), dict) else {}
        targets = []
        if voters:
            by_id = {(_voter_id(v) or f"idx-{n}"): v for n, v in enumerate(voters)}
            targets = [by_id[x] for x in selected_ids if x in by_id]
        else:
            targets = [None]
        for v in targets:
            vid = _voter_id(v or {}) if v else ""
            result_item = {
                "result_id": hashlib.sha1(f"{campaign_id}|{_household_key(hh)}|{vid}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
                "campaign_id": campaign_id,
                "username": user.get("username"),
                "assignment_id": clean_value(item.get("assignment_id") or assignment_meta.get("mobile_assignment_id") or assignment_meta.get("source_work_item_id")),
                "assignment_name": name,
                "street": street,
                "household_key": _household_key(hh),
                "household_address": address,
                "target_type": "voter" if v else "household",
                "voter_id": vid,
                "voter_name": _voter_name(v or {}),
                "result": result,
                "yard_sign": bool(yard_sign),
                "follow_up": bool(follow_up),
                "mail_ballot_interest": bool(mb_interest),
                "volunteer_interest": bool(volunteer_interest),
                "notes": notes,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "field_app",
                "sync_status": "queued",
            }
            local["queued"].append(result_item)
        save_local_results(campaign_id, local)
        st.success("Saved locally. Returning to houses.")
        _go("houses", selected_assignment_idx=idx, selected_street=street)

    st.markdown("""
    <div class="cc-card cc-legend"><b>Column / Icon Legend</b><br>
    <b>F</b> = Favorable &nbsp;&nbsp; <b>U</b> = Undecided &nbsp;&nbsp; <b>A</b> = Against &nbsp;&nbsp; <b>NH</b> = Not Home<br>
    <b>YS</b> = Yard Sign &nbsp;&nbsp; <b>FU</b> = Follow Up Needed &nbsp;&nbsp; <b>✉</b> = Mail Ballot Interest / MB &nbsp;&nbsp; <b>V</b> = Volunteer Interest</div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="cc-back">', unsafe_allow_html=True)
    if st.button("← Back to Houses", key="back_houses"):
        _go("houses", selected_assignment_idx=idx, selected_street=street)
    st.markdown('</div>', unsafe_allow_html=True)


if "field_user" not in st.session_state:
    login_screen()

user = current_user()
campaign_id = current_campaign_id()
assignments = st.session_state.get("assignments")
if assignments is None:
    assignments = load_assignments(campaign_id, user.get("username"))
    st.session_state["assignments"] = assignments
assignments = [a for a in assignments if isinstance(a, dict)]

screen = st.session_state.get("field_screen", "lists")

if screen == "lists":
    _render_top_sync(user, campaign_id)
    _render_lists(assignments)
elif screen == "streets":
    idx = int(st.session_state.get("selected_assignment_idx", 0))
    if idx >= len(assignments):
        _go("lists")
    _render_streets(assignments, idx)
elif screen == "houses":
    idx = int(st.session_state.get("selected_assignment_idx", 0))
    street = clean_value(st.session_state.get("selected_street"))
    if idx >= len(assignments) or not street:
        _go("streets", selected_assignment_idx=idx if idx < len(assignments) else 0)
    _render_houses(assignments, idx, street)
elif screen == "voter_card":
    idx = int(st.session_state.get("selected_assignment_idx", 0))
    street = clean_value(st.session_state.get("selected_street"))
    house_idx = int(st.session_state.get("selected_house_idx", 0))
    if idx >= len(assignments) or not street:
        _go("lists")
    _render_voter_card(assignments, idx, street, house_idx)
else:
    _go("lists")

with st.expander("Local queue detail"):
    st.json(load_local_results(campaign_id))
