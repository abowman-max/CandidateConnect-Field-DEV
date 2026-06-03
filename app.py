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


# -----------------------------
# C4.5 Mobile Workflow Restore
# My Lists -> Streets -> Houses -> Voter Card
# -----------------------------

def _street_from_household(row: dict) -> str:
    street = _first_value(row, ["Street", "street", "Street Name", "street_name"])
    if street:
        return street.upper()
    address = _first_value(row, ["Address", "address", "Residence Address", "Street Address"])
    address = re.sub(r"\s+", " ", address).strip()
    # Strip common leading house number patterns: 123, 123A, 123-125, 1/2
    street = re.sub(r"^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?(?:\s+1/2)?\s+", "", address).strip()
    return (street or address or "UNKNOWN STREET").upper()


def _household_address(row: dict) -> str:
    return _first_value(row, ["Address", "address", "Residence Address", "Street Address"]) or "Unknown address"


def _household_city(row: dict) -> str:
    return _first_value(row, ["City", "city", "Municipality", "municipality"])


def _household_display(row: dict) -> str:
    addr = _household_address(row)
    city = _household_city(row)
    return f"{addr}, {city}" if city else addr


def _assignment_label(item: dict, idx: int) -> str:
    return (
        clean_value(item.get("label"))
        or clean_value(item.get("street"))
        or clean_value(item.get("precinct"))
        or clean_value(item.get("assignment_name"))
        or clean_value((_assignment_payload(item).get("assignment") or {}).get("name") if isinstance((_assignment_payload(item).get("assignment") or {}), dict) else "")
        or f"Assignment {idx + 1}"
    )


def _voters_by_household(voters: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for v in voters:
        hk = _voter_household_key(v)
        if hk:
            out.setdefault(hk, []).append(v)
    return out


def _household_voters(household: dict, voters: list[dict], voter_map: dict[str, list[dict]]) -> list[dict]:
    hk = _household_key(household)
    hh_voters = voter_map.get(hk, [])
    if hh_voters:
        return hh_voters
    # Fallback for legacy packages with inconsistent household keys.
    addr = _household_address(household)
    return [v for v in voters if _first_value(v, ["Address", "address", "Residence Address", "Street Address"]) == addr]


def _street_groups(households: list[dict], voters: list[dict]) -> list[dict]:
    voter_map = _voters_by_household(voters)
    groups: dict[str, dict] = {}
    for hh in households:
        street = _street_from_household(hh)
        g = groups.setdefault(street, {"street": street, "households": [], "voter_count": 0})
        g["households"].append(hh)
        g["voter_count"] += len(_household_voters(hh, voters, voter_map))
    return sorted(groups.values(), key=lambda x: x["street"])


def _result_lookup(local_payload: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for bucket in ("queued", "synced"):
        for item in local_payload.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            hk = clean_value(item.get("household_key"))
            if hk:
                out.setdefault(hk, []).append(item)
    return out


def _house_status(household: dict, hh_voters: list[dict], local_payload: dict) -> str:
    hk = _household_key(household)
    results = _result_lookup(local_payload).get(hk, [])
    if not results:
        return "Not Started"
    if any(not clean_value(r.get("voter_id")) or clean_value(r.get("record_level")) == "household" for r in results):
        return "Complete"
    voter_ids = {_voter_id(v) for v in hh_voters if _voter_id(v)}
    completed = {clean_value(r.get("voter_id")) for r in results if clean_value(r.get("voter_id"))}
    if voter_ids and voter_ids.issubset(completed):
        return "Complete"
    return "In Progress"


def _status_badge(status: str) -> str:
    if status == "Complete":
        return "✅ Complete"
    if status == "In Progress":
        return "🟡 In Progress"
    return "⚪ Not Started"


def _go(screen: str, **kwargs) -> None:
    st.session_state["field_screen"] = screen
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


def _active_assignment(valid_items: list[dict]) -> tuple[int, dict | None]:
    idx = int(st.session_state.get("active_assignment_idx", 0) or 0)
    if not valid_items:
        return 0, None
    idx = max(0, min(idx, len(valid_items) - 1))
    return idx, valid_items[idx]


def _render_list_table(valid_items: list[dict]) -> None:
    st.subheader("My Lists")
    st.caption("Refresh on Wi‑Fi, then open a list to walk streets, houses, and voters.")

    h = st.columns([4, 1, 1, 1, 1])
    h[0].markdown("**List**")
    h[1].markdown("**Streets**")
    h[2].markdown("**Houses**")
    h[3].markdown("**Voters**")
    h[4].markdown("**Open**")

    for i, item in enumerate(valid_items):
        households = _assignment_households(item)
        voters = _assignment_voters(item)
        streets = _street_groups(households, voters)
        label = _assignment_label(item, i)
        row = st.columns([4, 1, 1, 1, 1])
        row[0].markdown(f"**{label}**")
        row[1].write(len(streets))
        row[2].write(len(households))
        row[3].write(len(voters))
        if row[4].button("Open", key=f"open_assignment_{i}", use_container_width=True):
            _go("streets", active_assignment_idx=i, active_street="")


def _render_street_table(item: dict, label: str, local_payload: dict) -> None:
    households = _assignment_households(item)
    voters = _assignment_voters(item)
    groups = _street_groups(households, voters)
    voter_map = _voters_by_household(voters)

    top = st.columns([1, 3])
    if top[0].button("← Lists", use_container_width=True):
        _go("lists")
    top[1].subheader(label)
    st.caption(f"{len(groups):,} street(s) · {len(households):,} house(s) · {len(voters):,} voter(s)")

    h = st.columns([4, 1, 1, 2, 1])
    h[0].markdown("**Street**")
    h[1].markdown("**Houses**")
    h[2].markdown("**Voters**")
    h[3].markdown("**Progress**")
    h[4].markdown("**Open**")

    for g in groups:
        complete = 0
        for hh in g["households"]:
            if _house_status(hh, _household_voters(hh, voters, voter_map), local_payload) == "Complete":
                complete += 1
        row = st.columns([4, 1, 1, 2, 1])
        row[0].markdown(f"**{g['street']}**")
        row[1].write(len(g["households"]))
        row[2].write(g["voter_count"])
        row[3].write(f"{complete}/{len(g['households'])}")
        if row[4].button("Open", key=f"open_street_{campaign_id}_{g['street']}", use_container_width=True):
            _go("houses", active_street=g["street"])


def _render_house_table(item: dict, label: str, local_payload: dict) -> None:
    households = _assignment_households(item)
    voters = _assignment_voters(item)
    voter_map = _voters_by_household(voters)
    street = st.session_state.get("active_street") or ""
    street_houses = [hh for hh in households if _street_from_household(hh) == street]

    top = st.columns([1, 3])
    if top[0].button("← Streets", use_container_width=True):
        _go("streets")
    top[1].subheader(street or label)
    st.caption(f"{len(street_houses):,} house(s) on this street")

    h = st.columns([4, 1, 2, 1])
    h[0].markdown("**Address**")
    h[1].markdown("**Voters**")
    h[2].markdown("**Status**")
    h[3].markdown("**Open**")

    for i, hh in enumerate(street_houses):
        hh_voters = _household_voters(hh, voters, voter_map)
        status = _house_status(hh, hh_voters, local_payload)
        hk = _household_key(hh) or str(i)
        row = st.columns([4, 1, 2, 1])
        row[0].markdown(f"**{_household_display(hh)}**")
        row[1].write(len(hh_voters))
        row[2].write(_status_badge(status))
        if row[3].button("Open", key=f"open_house_{campaign_id}_{hk}_{i}", use_container_width=True):
            _go("house", active_household_key=hk, active_household_index=i)


def _render_house_card(item: dict, label: str, local_payload: dict) -> None:
    households = _assignment_households(item)
    voters = _assignment_voters(item)
    voter_map = _voters_by_household(voters)
    street = st.session_state.get("active_street") or ""
    street_houses = [hh for hh in households if _street_from_household(hh) == street]
    idx = int(st.session_state.get("active_household_index", 0) or 0)
    idx = max(0, min(idx, len(street_houses) - 1)) if street_houses else 0
    hh = street_houses[idx] if street_houses else {}
    hh_voters = _household_voters(hh, voters, voter_map)
    hk = _household_key(hh)
    result_options = _assignment_result_options(item)

    top = st.columns([1, 3])
    if top[0].button("← Houses", use_container_width=True):
        _go("houses")
    top[1].subheader(_household_display(hh))
    st.caption(f"{street} · {len(hh_voters)} voter(s) · {_status_badge(_house_status(hh, hh_voters, local_payload))}")

    st.markdown("### Voter Card")
    if not hh_voters:
        st.info("No individual voter detail is attached to this house. Save a household-level result below.")
    else:
        for vi, voter in enumerate(hh_voters):
            name = _voter_name(voter) or f"Voter {vi + 1}"
            party = _first_value(voter, ["Party", "party", "CalculatedParty"]) or "—"
            age = _first_value(voter, ["Age", "age"]) or "—"
            vid = _voter_id(voter)
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.caption(f"Party: {party} · Age: {age}" + (f" · ID: {vid}" if vid else ""))
                with st.form(f"save_voter_{vi}_{vid or vi}"):
                    result = st.radio("Result", result_options, horizontal=False, key=f"result_voter_{vi}_{vid or vi}")
                    c1, c2 = st.columns(2)
                    yard = c1.checkbox("Yard sign", key=f"yard_voter_{vi}_{vid or vi}")
                    volunteer = c2.checkbox("Volunteer interest", key=f"vol_voter_{vi}_{vid or vi}")
                    c3, c4 = st.columns(2)
                    mb = c3.checkbox("Mail ballot interest", key=f"mb_voter_{vi}_{vid or vi}")
                    follow = c4.checkbox("Needs follow-up", key=f"follow_voter_{vi}_{vid or vi}")
                    notes = st.text_area("Notes", height=70, key=f"notes_voter_{vi}_{vid or vi}")
                    save = st.form_submit_button("Save Voter Result")
                if save:
                    _save_field_result(
                        campaign_id=campaign_id,
                        user=user,
                        selected_assignment=item,
                        selected_household=hh,
                        selected_voter=voter,
                        voter_id=vid,
                        result=result,
                        notes=notes,
                        flags={
                            "yard_sign": yard,
                            "volunteer_interest": volunteer,
                            "mail_ballot_interest": mb,
                            "needs_follow_up": follow,
                        },
                        record_level="voter",
                    )
                    st.success("Saved. Returning to houses.")
                    _go("houses")

    st.markdown("### Household Result")
    with st.form("save_household_result"):
        household_result = st.radio("Household result", result_options, horizontal=False, key="household_result_choice")
        household_notes = st.text_area("Household notes", height=80)
        save_hh = st.form_submit_button("Save Household Result")
    if save_hh:
        _save_field_result(
            campaign_id=campaign_id,
            user=user,
            selected_assignment=item,
            selected_household=hh,
            selected_voter=None,
            voter_id=hk,
            result=household_result,
            notes=household_notes,
            flags={},
            record_level="household",
        )
        st.success("Household saved. Returning to houses.")
        _go("houses")


def _save_field_result(
    campaign_id: str,
    user: dict,
    selected_assignment: dict,
    selected_household: dict,
    selected_voter: dict | None,
    voter_id: str,
    result: str,
    notes: str,
    flags: dict,
    record_level: str,
) -> None:
    assignment_payload = _assignment_payload(selected_assignment or {})
    assignment_meta = assignment_payload.get("assignment") if isinstance(assignment_payload.get("assignment"), dict) else {}
    item = {
        "result_id": hashlib.sha1(f"{campaign_id}|{voter_id}|{record_level}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
        "campaign_id": campaign_id,
        "username": user.get("username"),
        "assignment_id": clean_value((selected_assignment or {}).get("assignment_id") or assignment_meta.get("mobile_assignment_id") or assignment_meta.get("source_work_item_id")),
        "assignment_name": clean_value((selected_assignment or {}).get("assignment_name") or assignment_meta.get("name") or _assignment_label(selected_assignment or {}, 0)),
        "record_level": record_level,
        "household_key": _household_key(selected_household or {}),
        "household_address": _household_address(selected_household or {}),
        "household_city": _household_city(selected_household or {}),
        "street": _street_from_household(selected_household or {}),
        "voter_id": str(voter_id or "").strip(),
        "voter_name": _voter_name(selected_voter or {}),
        "result": result,
        "notes": notes,
        "flags": flags or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "field_app_c4_5",
        "sync_status": "queued",
    }
    local_payload = load_local_results(campaign_id)
    local_payload.setdefault("queued", []).append(item)
    save_local_results(campaign_id, local_payload)


st.divider()
st.caption("C4.5 Field App Mobile Workflow Restore")

assignments = st.session_state.get("assignments", assignments)
valid_items = [item for item in assignments if isinstance(item, dict)]

if not valid_items:
    st.subheader("My Lists")
    st.info("No assignment package found yet. Build/assign work in the web app, then refresh here on Wi‑Fi.")
else:
    st.session_state.setdefault("field_screen", "lists")
    screen = st.session_state.get("field_screen", "lists")
    idx, active_item = _active_assignment(valid_items)
    active_label = _assignment_label(active_item or {}, idx) if active_item else ""

    if screen == "lists":
        _render_list_table(valid_items)
    elif screen == "streets" and active_item:
        _render_street_table(active_item, active_label, local)
    elif screen == "houses" and active_item:
        _render_house_table(active_item, active_label, load_local_results(campaign_id))
    elif screen == "house" and active_item:
        _render_house_card(active_item, active_label, load_local_results(campaign_id))
    else:
        _go("lists")

with st.expander("Local queue detail"):
    st.json(load_local_results(campaign_id))
