# Candidate Connect FIELD APP — separate mobile/offline shell
# C4 Split Architecture v1
#
# Separate from the main Candidate Connect web app.
# Reads:
#   app_state/security_store.json
#   app_state/mobile_assignments/<campaign_id>.json
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


def load_assignments(campaign_id: str) -> list[dict]:
    raw = read_json_public(f"app_state/mobile_assignments/{campaign_id}.json", {})
    if isinstance(raw, dict):
        items = raw.get("assignments") or raw.get("work_items") or raw.get("items") or []
        return items if isinstance(items, list) else []
    if isinstance(raw, list):
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
assignments = load_assignments(campaign_id)

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
if not assignments:
    st.info("No assignment package found yet. Build/assign work in the web app, then refresh here on Wi‑Fi.")
else:
    labels = []
    for i, item in enumerate(assignments):
        if not isinstance(item, dict):
            continue
        labels.append(
            item.get("label")
            or item.get("street")
            or item.get("precinct")
            or item.get("assignment_name")
            or f"Assignment {i+1}"
        )
    chosen = st.selectbox("Choose assignment", labels)
    st.write(f"Selected: **{chosen}**")

st.subheader("Record Field Result")
with st.form("record_result"):
    voter_id = st.text_input("Voter ID / Household ID")
    result = st.selectbox("Result", ["Favorable", "Undecided", "Against", "Not Home", "Yard Sign", "Needs Follow-up"])
    notes = st.text_area("Notes", height=80)
    save_clicked = st.form_submit_button("Save Offline / Queue")

if save_clicked:
    item = {
        "result_id": hashlib.sha1(f"{campaign_id}|{voter_id}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
        "campaign_id": campaign_id,
        "username": user.get("username"),
        "voter_id": str(voter_id or "").strip(),
        "result": result,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "field_app",
    }
    local = load_local_results(campaign_id)
    local.setdefault("queued", []).append(item)
    save_local_results(campaign_id, local)
    st.success("Saved locally. Sync when back on Wi‑Fi.")
    st.rerun()

with st.expander("Local queue detail"):
    st.json(load_local_results(campaign_id))
