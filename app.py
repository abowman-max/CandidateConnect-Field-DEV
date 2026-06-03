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


if "field_user" not in st.session_state:
    login_screen()

user = current_user()
campaign_id = current_campaign_id()

top_left, top_right = st.columns([2, 1])
with top_left:
    st.title("Field Work")
    st.caption(f"Logged in as {user.get('username')} · Campaign: {campaign_id}")
with top_right:
    if st.button("Log Out"):
        st.session_state.clear()
        st.rerun()

local = load_local_results(campaign_id)
assignments = load_assignments(campaign_id, user.get("username"))

q, s, f = st.columns(3)
q.metric("Queued", len(local.get("queued") or []))
s.metric("Synced", len(local.get("synced") or []))
f.metric("Failed", len(local.get("failed") or []))
st.caption(f"Last Sync: {local.get('last_sync') or 'Never'}")

c1, c2 = st.columns(2)
with c1:
    if st.button("Refresh / Download Assignments"):
        st.session_state["assignments"] = assignments
        st.success(f"Downloaded {len(assignments)} assignment item(s).")
        if st.session_state.get("last_assignment_source_key"):
            st.caption(f"Source: {st.session_state.get('last_assignment_source_key')}")
with c2:
    if st.button("Sync Now"):
        server = load_server_results(campaign_id)
        merged = merge_results_for_sync(local, server)
        ok, msg = put_json_r2(f"app_state/mobile_results/{campaign_id}.json", merged)
        if ok:
            save_local_results(campaign_id, merged)
            st.success("Synced field results.")
            st.caption(msg)
            st.rerun()
        else:
            local.setdefault("failed", [])
            local["failed"].append({
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "reason": msg,
                "queued_count": len(local.get("queued") or []),
            })
            save_local_results(campaign_id, local)
            st.error(f"Sync failed: {msg}")

st.divider()
st.subheader("Assignments")
assignments = st.session_state.get("assignments", assignments)
selected_assignment = None
selected_household = None
selected_voter = None
households: list[dict] = []
voters: list[dict] = []

if not assignments:
    st.info("No assignment package found yet. Build/assign work in the web app, then refresh here on Wi‑Fi.")
else:
    valid_items = [item for item in assignments if isinstance(item, dict)]
    labels = []
    for i, item in enumerate(valid_items):
        labels.append(
            clean_value(item.get("label"))
            or clean_value(item.get("street"))
            or clean_value(item.get("precinct"))
            or clean_value(item.get("assignment_name"))
            or f"Assignment {i+1}"
        )

    chosen = st.selectbox("Choose assignment", labels, key="field_choose_assignment")
    chosen_idx = labels.index(chosen)
    selected_assignment = valid_items[chosen_idx]
    st.write(f"Selected: **{chosen}**")

    households = _assignment_households(selected_assignment)
    voters = _assignment_voters(selected_assignment)
    st.caption(f"Package contents: {len(households):,} household(s), {len(voters):,} voter(s)")

    if households:
        voter_map: dict[str, list[dict]] = {}
        for v in voters:
            hk = _voter_household_key(v)
            voter_map.setdefault(hk, []).append(v)

        st.markdown("#### Household")
        hh_labels = []
        for i, hh in enumerate(households):
            hk = _household_key(hh)
            hh_labels.append(_household_label(hh, i, voter_map.get(hk, [])))
        hh_choice = st.selectbox("Choose household", hh_labels, key="field_choose_household")
        hh_idx = hh_labels.index(hh_choice)
        selected_household = households[hh_idx]
        selected_hh_key = _household_key(selected_household)
        hh_voters = voter_map.get(selected_hh_key, [])
        if not hh_voters and selected_hh_key:
            # Some legacy packages may not carry the household key consistently.
            addr = _first_value(selected_household, ["Address", "address", "Residence Address", "Street Address"])
            hh_voters = [v for v in voters if _first_value(v, ["Address", "address", "Residence Address", "Street Address"]) == addr]

        addr = _first_value(selected_household, ["Address", "address", "Residence Address", "Street Address"])
        city = _first_value(selected_household, ["City", "city", "Municipality", "municipality"])
        names = _first_value(selected_household, ["Names", "names", "Voter Names"])
        st.info(f"**{addr or 'Selected household'}**" + (f", {city}" if city else ""))
        if names:
            st.caption(names)

        if hh_voters:
            st.markdown("#### Voters")
            voter_labels = ["Household result / no specific voter"] + [_voter_label(v, i) for i, v in enumerate(hh_voters)]
            v_choice = st.selectbox("Choose voter", voter_labels, key="field_choose_voter")
            if v_choice != voter_labels[0]:
                selected_voter = hh_voters[voter_labels.index(v_choice) - 1]
                vcols = st.columns(3)
                vcols[0].metric("Party", _first_value(selected_voter, ["Party", "party", "CalculatedParty"]) or "—")
                vcols[1].metric("Age", _first_value(selected_voter, ["Age", "age"]) or "—")
                vcols[2].metric("Voter ID", _voter_id(selected_voter) or "—")
        else:
            st.warning("This household has no voter detail in the package. You can still record a household-level result.")
    else:
        st.warning("This assignment package does not include household detail yet. You can still manually enter a Voter ID or Household ID.")

st.subheader("Record Field Result")
result_options = _assignment_result_options(selected_assignment or {})
default_record_id = ""
if selected_voter:
    default_record_id = _voter_id(selected_voter)
elif selected_household:
    default_record_id = _household_key(selected_household)

with st.form("record_result"):
    voter_id = st.text_input("Voter ID / Household ID", value=default_record_id)
    result = st.selectbox("Result", result_options)
    notes = st.text_area("Notes", height=80)
    save_clicked = st.form_submit_button("Save Offline / Queue")

if save_clicked:
    assignment_payload = _assignment_payload(selected_assignment or {})
    assignment_meta = assignment_payload.get("assignment") if isinstance(assignment_payload.get("assignment"), dict) else {}
    item = {
        "result_id": hashlib.sha1(f"{campaign_id}|{voter_id}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
        "campaign_id": campaign_id,
        "username": user.get("username"),
        "assignment_id": clean_value((selected_assignment or {}).get("assignment_id") or assignment_meta.get("mobile_assignment_id") or assignment_meta.get("source_work_item_id")),
        "assignment_name": clean_value((selected_assignment or {}).get("assignment_name") or assignment_meta.get("name")),
        "household_key": _household_key(selected_household or {}),
        "household_address": _first_value(selected_household or {}, ["Address", "address", "Residence Address", "Street Address"]),
        "voter_id": str(voter_id or "").strip(),
        "voter_name": _voter_name(selected_voter or {}),
        "result": result,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "field_app",
        "sync_status": "queued",
    }
    local = load_local_results(campaign_id)
    local.setdefault("queued", []).append(item)
    save_local_results(campaign_id, local)
    st.success("Saved locally. Sync when back on Wi‑Fi.")
    st.rerun()

with st.expander("Local queue detail"):
    st.json(load_local_results(campaign_id))
