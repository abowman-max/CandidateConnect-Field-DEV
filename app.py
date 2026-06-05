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
from urllib.parse import quote, unquote


# C4.6.27 Mobile — final progress/back-route helpers
def cc27_norm(value):
    try:
        return "" if value is None else str(value).strip().upper()
    except Exception:
        return ""


def cc27_keys_from_household(hh):
    if not isinstance(hh, dict):
        return set()
    keys = set()
    for k in [
        "Household Key", "household_key", "household_id", "HouseholdID",
        "HH_ID", "hh_id", "Address", "address", "FullAddress", "full_address"
    ]:
        v = cc27_norm(hh.get(k))
        if v:
            keys.add(v)
    precinct = cc27_norm(hh.get("Precinct") or hh.get("precinct"))
    addr = cc27_norm(hh.get("Address") or hh.get("address") or hh.get("FullAddress") or hh.get("full_address"))
    if precinct and addr:
        keys.add(f"{precinct}|{addr}")
    return keys


def cc27_keys_from_result(rec):
    if not isinstance(rec, dict):
        return set()
    keys = set()
    for k in [
        "household_key", "Household Key", "household_id", "HouseholdID",
        "hh_key", "HH_ID", "selected_household_key", "current_household_key",
        "address", "Address", "full_address", "FullAddress"
    ]:
        v = cc27_norm(rec.get(k))
        if v:
            keys.add(v)
    precinct = cc27_norm(rec.get("precinct") or rec.get("Precinct"))
    addr = cc27_norm(rec.get("address") or rec.get("Address") or rec.get("full_address") or rec.get("FullAddress"))
    if precinct and addr:
        keys.add(f"{precinct}|{addr}")
    return keys


def cc27_result_has_real_contact(rec):
    if not isinstance(rec, dict):
        return False
    for k in ["result", "Result", "contact_result", "Contact Result", "outcome", "disposition", "voter_result"]:
        v = cc27_norm(rec.get(k))
        if v and v not in {"NONE", "NAN", "NULL", "PENDING", "QUEUED", "SYNCED", "FAILED", "ACTIVE"}:
            return True
    # many rows are saved as status + notes
    for k in ["notes", "Notes", "tags_added", "contacted_at", "created_at"]:
        if rec.get(k):
            return True
    return False


def cc27_walk_all_objects(obj, max_items=50000):
    """Walk nested dict/list structures in session_state to find result-like records."""
    seen = set()
    stack = [obj]
    count = 0
    while stack and count < max_items:
        cur = stack.pop()
        count += 1
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            yield cur
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append(v)


def cc27_completed_household_keys():
    """
    Find completed households from every local store the mobile app might be using.
    This reads queued + synced + nested result records instead of assuming one key name.
    """
    completed = set()

    # Scan all session state structures.
    for ss_key, ss_val in list(st.session_state.items()):
        lk = str(ss_key).lower()
        # skip enormous assignment payloads unless the key looks result/progress related
        likely = any(tok in lk for tok in [
            "result", "queue", "queued", "sync", "synced", "contact",
            "complete", "progress", "done", "field"
        ])
        if not likely:
            continue
        for rec in cc27_walk_all_objects(ss_val):
            if cc27_result_has_real_contact(rec):
                completed.update(cc27_keys_from_result(rec))

    # Scan any dedicated progress indices too.
    for ss_key, ss_val in list(st.session_state.items()):
        lk = str(ss_key).lower()
        if any(tok in lk for tok in ["completed_households", "complete_households", "progress_index"]):
            if isinstance(ss_val, (set, list, tuple)):
                completed.update({cc27_norm(x) for x in ss_val if cc27_norm(x)})
            elif isinstance(ss_val, dict):
                for k, v in ss_val.items():
                    if v:
                        completed.add(cc27_norm(k))

    return completed


def cc27_households_from_streets(streets):
    households = []
    for s in streets or []:
        if isinstance(s, dict):
            for hh in s.get("households") or []:
                if isinstance(hh, dict):
                    households.append(hh)
    return households


def cc27_progress_for_streets(streets):
    households = cc27_households_from_streets(streets)
    total = len(households)
    if total <= 0:
        return 0, 0
    completed = cc27_completed_household_keys()
    done = 0
    for hh in households:
        keys = cc27_keys_from_household(hh)
        if keys and completed.intersection(keys):
            done += 1
        else:
            # Some lower screens may mutate the household dict directly.
            for k in ["complete", "completed", "done", "contacted", "has_result", "recorded"]:
                if bool(hh.get(k)):
                    done += 1
                    break
            else:
                for k in ["result", "Result", "contact_result", "status", "Status", "outcome"]:
                    v = cc27_norm(hh.get(k))
                    if v and v not in {"", "NOT STARTED", "ACTIVE", "READY", "PENDING", "NONE", "NAN"}:
                        done += 1
                        break
    return done, total


def cc27_progress_label_for_streets(streets):
    done, total = cc27_progress_for_streets(streets)
    return f"{done:,} / {total:,} ›"




# C4.6.25 Mobile — durable local progress index
def cc25_progress_index_key():
    user = clean_value(st.session_state.get("user_email") or st.session_state.get("email") or "")
    campaign = clean_value(st.session_state.get("campaign_id") or st.session_state.get("campaign") or "")
    return f"cc25_completed_households::{campaign}::{user}"


def cc25_norm_key(value):
    try:
        return "" if value is None else str(value).strip().upper()
    except Exception:
        return ""


def cc25_household_keys_from_any(obj):
    if not isinstance(obj, dict):
        return set()
    keys = set()
    for k in [
        "Household Key", "household_key", "household_id", "HouseholdID",
        "HH_ID", "hh_id", "selected_household_key", "current_household_key",
        "Address", "address", "FullAddress", "full_address"
    ]:
        v = cc25_norm_key(obj.get(k))
        if v:
            keys.add(v)
    precinct = cc25_norm_key(obj.get("Precinct") or obj.get("precinct"))
    addr = cc25_norm_key(obj.get("Address") or obj.get("address") or obj.get("FullAddress") or obj.get("full_address"))
    if precinct and addr:
        keys.add(f"{precinct}|{addr}")
    return keys


def cc25_get_completed_keys():
    k = cc25_progress_index_key()
    val = st.session_state.get(k, set())
    if isinstance(val, list):
        val = set(val)
    if not isinstance(val, set):
        val = set()
    return val


def cc25_mark_household_complete(obj):
    keys = cc25_household_keys_from_any(obj)
    if not keys:
        # Try selected household in session state.
        for sk in ["selected_household", "current_household", "household", "selected_household_obj"]:
            if isinstance(st.session_state.get(sk), dict):
                keys.update(cc25_household_keys_from_any(st.session_state.get(sk)))
    if keys:
        idx_key = cc25_progress_index_key()
        existing = cc25_get_completed_keys()
        existing.update(keys)
        st.session_state[idx_key] = existing
    return keys


def cc25_completion_for_households(households):
    households = [h for h in (households or []) if isinstance(h, dict)]
    total = len(households)
    if total <= 0:
        return 0, 0
    completed = cc25_get_completed_keys()
    # Also merge old discovered keys if helper exists
    try:
        completed = set(completed) | set(cc24_completed_household_keyset())
    except Exception:
        pass
    done = 0
    for hh in households:
        hk = cc25_household_keys_from_any(hh)
        if hk and completed.intersection(hk):
            done += 1
    return done, total


def cc25_completion_for_streets(streets):
    households = []
    for s in streets or []:
        if isinstance(s, dict):
            households.extend([hh for hh in (s.get("households") or []) if isinstance(hh, dict)])
    return cc25_completion_for_households(households)


def cc25_progress_label_for_streets(streets):
    return cc27_progress_label_for_streets(streets)



# C4.6.20 Mobile — force precinct-first flow for assignment packages with hierarchy


# C4.6.26 Mobile — roll up progress from the same household/street data used by lower screens
def cc26_is_household_done(hh):
    if not isinstance(hh, dict):
        return False
    # Lower screens may write one of these flags/statuses onto household rows.
    for k in ["complete", "completed", "is_complete", "done", "is_done", "contacted", "has_result", "recorded"]:
        if bool(hh.get(k)):
            return True
    for k in ["status", "Status", "progress", "Progress", "result", "Result", "contact_result", "outcome"]:
        v = clean_value(hh.get(k)).strip().lower()
        if v and v not in {"not started", "0", "0/1", "active", "ready", "none", "nan", "pending"}:
            return True

    # Match any local result by household key/address.
    try:
        hh_keys = cc25_household_keys_from_any(hh) if "cc25_household_keys_from_any" in globals() else set()
        completed = cc25_get_completed_keys() if "cc25_get_completed_keys" in globals() else set()
        if hh_keys and completed and hh_keys.intersection(completed):
            return True
    except Exception:
        pass

    # Lower screens often store progress by household key in session_state dictionaries.
    try:
        keys = []
        for k in ["Household Key", "household_key", "household_id", "Address", "address"]:
            val = clean_value(hh.get(k))
            if val:
                keys.append(val)
        for ss_key, ss_val in list(st.session_state.items()):
            if not isinstance(ss_val, dict):
                continue
            lk = str(ss_key).lower()
            if not any(tok in lk for tok in ["complete", "progress", "result", "household", "contact"]):
                continue
            for key in keys:
                if key in ss_val and bool(ss_val.get(key)):
                    return True
    except Exception:
        pass
    return False


def cc26_households_from_streets(streets):
    households = []
    for s in streets or []:
        if isinstance(s, dict):
            households.extend([hh for hh in (s.get("households") or []) if isinstance(hh, dict)])
    return households


def cc26_progress_for_households(households):
    households = [h for h in (households or []) if isinstance(h, dict)]
    total = len(households)
    done = sum(1 for h in households if cc26_is_household_done(h))
    return done, total


def cc26_progress_for_streets(streets):
    return cc26_progress_for_households(cc26_households_from_streets(streets))


def cc26_progress_label(streets):
    return cc27_progress_label_for_streets(streets)



def cc26_assignment_all_streets(item):
    streets = []
    try:
        for p in cc21_get_hierarchy(item):
            if isinstance(p, dict):
                streets.extend(p.get("streets") or [])
    except Exception:
        pass
    if streets:
        return streets
    try:
        households, voters, voter_map = assignment_maps(item)
        # create fake one-street wrapper so the same logic can count household flags
        return [{"street": "All", "households": households or []}]
    except Exception:
        return []


def cc26_precinct_counts_row(p):
    streets = p.get("streets") if isinstance(p, dict) and isinstance(p.get("streets"), list) else []
    houses = len(cc26_households_from_streets(streets))
    voters = 0
    for s in streets:
        if isinstance(s, dict):
            if s.get("voter_count") is not None:
                voters += int(s.get("voter_count") or 0)
            else:
                for hh in s.get("households") or []:
                    if isinstance(hh, dict):
                        voters += int(hh.get("Voters") or len(hh.get("voters") or []) or 0)
    return houses, voters, cc26_progress_label(streets)


def cc_m20_text(value):
    try:
        return "" if value is None else str(value).strip()
    except Exception:
        return ""


def cc_m20_assignment_title(a):
    if not isinstance(a, dict):
        return "Assignment"
    assignment = a.get("assignment") if isinstance(a.get("assignment"), dict) else {}
    program = a.get("program") if isinstance(a.get("program"), dict) else {}
    return (
        cc_m20_text(a.get("name"))
        or cc_m20_text(assignment.get("name"))
        or cc_m20_text(program.get("name"))
        or "Assignment"
    )


def cc_m20_get_hierarchy(a):
    """Return precinct hierarchy from all supported package shapes."""
    if not isinstance(a, dict):
        return []
    package = a.get("package") if isinstance(a.get("package"), dict) else {}
    assignment = a.get("assignment") if isinstance(a.get("assignment"), dict) else {}
    for source in [a, assignment, package]:
        for key in ["hierarchy", "precincts"]:
            val = source.get(key) if isinstance(source, dict) else None
            if isinstance(val, list) and val:
                return val
    return []


def cc_m20_should_precinct_first(a):
    h = cc_m20_get_hierarchy(a)
    if len(h) > 1:
        return True
    if isinstance(a, dict):
        assignment = a.get("assignment") if isinstance(a.get("assignment"), dict) else {}
        mode = cc_m20_text(a.get("mobile_open_mode") or assignment.get("mobile_open_mode")).lower()
        group = cc_m20_text(a.get("mobile_group_by") or assignment.get("mobile_group_by")).lower()
        scope = cc_m20_text(assignment.get("scope") or a.get("scope") or assignment.get("street_area") or a.get("street_area")).lower()
        if mode == "precinct_first" or group == "precinct" or "whole universe" in scope:
            return True
    return False


def cc_m20_assignment_counts(a):
    h = cc_m20_get_hierarchy(a)
    if h:
        streets = sum(len(p.get("streets") or []) for p in h if isinstance(p, dict))
        houses = sum(int(p.get("household_count") or 0) for p in h if isinstance(p, dict))
        voters = sum(int(p.get("voter_count") or 0) for p in h if isinstance(p, dict))
        return len(h), streets, houses, voters
    if isinstance(a, dict):
        assignment = a.get("assignment") if isinstance(a.get("assignment"), dict) else {}
        return (
            int(a.get("precinct_count") or assignment.get("precinct_count") or 0),
            int(a.get("street_count") or assignment.get("street_count") or 0),
            int(a.get("household_count") or assignment.get("household_count") or 0),
            int(a.get("voter_count") or assignment.get("voter_count") or 0),
        )
    return 0, 0, 0, 0


def cc_m20_render_precinct_list(a):
    h = cc_m20_get_hierarchy(a)
    if not h:
        return False
    st.markdown("### Precincts")
    st.caption("Choose a precinct, then choose a street.")
    rows = []
    for p in h:
        if not isinstance(p, dict):
            continue
        precinct = cc_m20_text(p.get("precinct") or p.get("name") or "Unassigned Precinct")
        streets = p.get("streets") if isinstance(p.get("streets"), list) else []
        rows.append((precinct, streets, int(p.get("household_count") or 0), int(p.get("voter_count") or 0)))
    for i, (precinct, streets, houses, voters) in enumerate(rows):
        label = f"{precinct} — {len(streets):,} streets · {houses:,} houses · {voters:,} voters"
        if st.button(label, key=f"m20_precinct_{i}_{abs(hash(precinct))}"):
            st.session_state["m20_selected_precinct"] = {
                "precinct": precinct,
                "streets": streets,
                "household_count": houses,
                "voter_count": voters,
            }
            st.session_state["m20_view"] = "streets"
            st.rerun()
    return True


def cc_m20_selected_streets_from_state(a=None):
    """Return streets based on selected precinct when present, otherwise legacy assignment streets."""
    p = st.session_state.get("m20_selected_precinct")
    if isinstance(p, dict) and isinstance(p.get("streets"), list):
        return p.get("streets") or []
    h = cc_m20_get_hierarchy(a or st.session_state.get("m20_selected_assignment") or {})
    if h:
        streets = []
        for p in h:
            if isinstance(p, dict):
                streets.extend(p.get("streets") or [])
        return streets
    return []




# C4.6.17 Mobile — normalize web-exported assignments and remove stale local assignments
def cc_mobile_clean_value(value):
    try:
        if value is None:
            return ""
        return str(value).strip()
    except Exception:
        return ""


def cc_mobile_assignment_id(rec):
    """Stable assignment/work item id resolver for local cleanup."""
    if not isinstance(rec, dict):
        return ""
    for key in [
        "assignment_id", "work_item_id", "package_id", "list_id",
        "id", "mobile_assignment_id"
    ]:
        val = cc_mobile_clean_value(rec.get(key))
        if val:
            return val
    parts = [
        rec.get("program_id"),
        rec.get("program_name"),
        rec.get("assigned_to"),
        rec.get("assignee"),
        rec.get("street_area"),
        rec.get("area"),
        rec.get("name"),
    ]
    return "_".join([cc_mobile_clean_value(x).lower().replace(" ", "-") for x in parts if cc_mobile_clean_value(x)])


def cc_mobile_is_deleted_assignment(rec):
    if not isinstance(rec, dict):
        return False
    status = cc_mobile_clean_value(rec.get("status")).lower()
    if status in {"deleted", "removed", "archived", "archive", "inactive", "cancelled", "canceled"}:
        return True
    for key in ["deleted", "is_deleted", "_deleted", "archived", "is_archived"]:
        if bool(rec.get(key)):
            return True
    return False


def cc_mobile_precincts_from_flat_voters(voters):
    """Fallback grouping when older payload has only flat voters."""
    precinct_map = {}
    for rec in voters or []:
        if not isinstance(rec, dict):
            continue
        precinct = (
            cc_mobile_clean_value(rec.get("Precinct"))
            or cc_mobile_clean_value(rec.get("PRECINCT"))
            or cc_mobile_clean_value(rec.get("precinct"))
            or "Unassigned Precinct"
        )
        street = (
            cc_mobile_clean_value(rec.get("StreetName"))
            or cc_mobile_clean_value(rec.get("street_name"))
            or cc_mobile_clean_value(rec.get("Street"))
            or cc_mobile_clean_value(rec.get("street"))
            or cc_mobile_clean_value(rec.get("Address"))
            or "Unknown Street"
        )
        address = cc_mobile_clean_value(rec.get("Address") or rec.get("address") or rec.get("FullAddress") or rec.get("full_address"))
        hh = cc_mobile_clean_value(rec.get("HouseholdID") or rec.get("household_id") or address or rec.get("PA_ID") or rec.get("voter_id"))
        p = precinct_map.setdefault(precinct, {"precinct": precinct, "streets": {}})
        s = p["streets"].setdefault(street, {"street": street, "households": {}})
        h = s["households"].setdefault(hh, {"household_id": hh, "address": address, "voters": []})
        h["voters"].append(rec)
    out = []
    for p_name in sorted(precinct_map.keys()):
        p = precinct_map[p_name]
        streets = []
        for s_name in sorted(p["streets"].keys()):
            s = p["streets"][s_name]
            households = list(s["households"].values())
            households.sort(key=lambda x: cc_mobile_clean_value(x.get("address")))
            streets.append({
                "street": s["street"],
                "households": households,
                "household_count": len(households),
                "voter_count": sum(len(h.get("voters") or []) for h in households),
            })
        out.append({
            "precinct": p["precinct"],
            "streets": streets,
            "street_count": len(streets),
            "household_count": sum(int(s.get("household_count") or 0) for s in streets),
            "voter_count": sum(int(s.get("voter_count") or 0) for s in streets),
        })
    return out


def cc_mobile_normalize_assignment(rec):
    """Normalize one assignment so mobile can show assignment -> precinct -> street -> household."""
    if not isinstance(rec, dict):
        return rec
    rec = dict(rec)
    package = rec.get("package") if isinstance(rec.get("package"), dict) else {}
    voters = rec.get("voters")
    if voters is None and isinstance(package, dict):
        voters = package.get("voters")
    if voters is None:
        voters = []

    hierarchy = (
        rec.get("precincts")
        or rec.get("hierarchy")
        or package.get("precincts")
        or package.get("hierarchy")
    )
    if not isinstance(hierarchy, list) or not hierarchy:
        hierarchy = cc_mobile_precincts_from_flat_voters(voters)

    rec["precincts"] = hierarchy
    rec["hierarchy"] = hierarchy
    rec["mobile_hierarchy_version"] = rec.get("mobile_hierarchy_version") or package.get("mobile_hierarchy_version") or "precinct_street_household_v1"
    rec["precinct_count"] = len(hierarchy)
    rec["street_count"] = sum(int(p.get("street_count") or len(p.get("streets") or [])) for p in hierarchy if isinstance(p, dict))
    rec["household_count"] = sum(int(p.get("household_count") or 0) for p in hierarchy if isinstance(p, dict))
    rec["voter_count"] = sum(int(p.get("voter_count") or 0) for p in hierarchy if isinstance(p, dict)) or len(voters or [])
    return rec


def cc_mobile_normalize_downloaded_assignments(payload):
    """
    Use server package as source of truth:
    - deleted/archived assignments are removed
    - active assignments are normalized
    - old local assignments not present in this payload should be replaced, not merged forever
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("assignments"), list):
            raw = payload.get("assignments") or []
        elif isinstance(payload.get("work_items"), list):
            raw = payload.get("work_items") or []
        elif isinstance(payload.get("packages"), list):
            raw = payload.get("packages") or []
        else:
            raw = [payload] if (payload.get("precincts") or payload.get("hierarchy") or payload.get("voters") or payload.get("package") or payload.get("assignment")) else []
    elif isinstance(payload, list):
        raw = payload
    else:
        raw = []

    cleaned = []
    seen = set()
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        if cc_mobile_is_deleted_assignment(rec):
            continue
        norm = cc_mobile_normalize_assignment(rec)
        aid = cc_mobile_assignment_id(norm)
        if aid and aid in seen:
            continue
        if aid:
            seen.add(aid)
        cleaned.append(norm)
    return cleaned


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
    .stApp { background: #efe8d8; }
    .block-container {max-width: 820px; padding: 0.55rem 0.9rem 1.2rem 0.9rem;}
    h1, h2, h3 {color: #071f45; letter-spacing: -0.02em;}
    h1 {font-size: 2.0rem !important; margin-bottom: 0.25rem !important;}
    h2 {font-size: 1.35rem !important; margin-top: 0.55rem !important;}
    h3 {font-size: 1.05rem !important; margin-top: 0.45rem !important;}
    .cc-topbar {display:flex; align-items:center; gap:0.6rem; margin:0.15rem 0 0.4rem 0;}
    .cc-logo {height:34px; width:auto; object-fit:contain;}
    .cc-title {font-weight:800; font-size:1.15rem; color:#071f45; line-height:1.1;}
    .cc-subtitle {font-size:0.78rem; color:#687084; margin-top:0.1rem;}
    .cc-card {background:#fffaf0; border:1px solid #d7cdbc; border-radius:10px; padding:0.75rem; margin:0.55rem 0;}
    .cc-legend {font-size:0.82rem; color:#071f45; background:#fffaf0; border:1px solid #d7cdbc; border-radius:9px; padding:0.65rem; margin-top:0.65rem;}
    .cc-muted {color:#687084; font-size:0.9rem;}
    .cc-pill {display:inline-block; padding:0.12rem 0.45rem; border-radius:999px; background:#f4f0e7; border:1px solid #d7cdbc; font-size:0.78rem; margin-right:0.25rem;}
    .stButton > button {
        background: #a80f18 !important;
        color: white !important;
        border: 1px solid #7c0b12 !important;
        border-radius: 8px !important;
        font-weight: 700 !important;
        padding: 0.38rem 0.7rem !important;
        min-height: 2.15rem !important;
    }
    div[data-testid="stDataFrame"] {border:1px solid #d7cdbc; border-radius:10px; overflow:hidden; background:#fffaf0;}
    div[data-testid="stDataFrame"] * {font-size: 0.92rem;}
    section.main p {margin-bottom:0.3rem;}
    hr {margin:0.7rem 0;}
    
/* C4.5.13 table alignment refinement */
.cc-table,
.cc-compact-table,
.cc-list-table,
.cc-street-table,
.cc-house-table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
}

.cc-table th,
.cc-table td,
.cc-compact-table th,
.cc-compact-table td,
.cc-list-table th,
.cc-list-table td,
.cc-street-table th,
.cc-street-table td,
.cc-house-table th,
.cc-house-table td {
    padding: 8px 10px !important;
    line-height: 1.15 !important;
    border-bottom: 1px solid rgba(10, 34, 64, 0.15);
    vertical-align: middle;
}

.cc-table th:first-child,
.cc-table td:first-child,
.cc-compact-table th:first-child,
.cc-compact-table td:first-child,
.cc-list-table th:first-child,
.cc-list-table td:first-child,
.cc-street-table th:first-child,
.cc-street-table td:first-child,
.cc-house-table th:first-child,
.cc-house-table td:first-child {
    text-align: left !important;
}

.cc-table th:not(:first-child),
.cc-table td:not(:first-child),
.cc-compact-table th:not(:first-child),
.cc-compact-table td:not(:first-child),
.cc-list-table th:not(:first-child),
.cc-list-table td:not(:first-child),
.cc-street-table th:not(:first-child),
.cc-street-table td:not(:first-child),
.cc-house-table th:not(:first-child),
.cc-house-table td:not(:first-child) {
    text-align: center !important;
}

/* Streamlit dataframe/table fallback */
[data-testid="stTable"] table {
    width: 100%;
    border-collapse: collapse;
}
[data-testid="stTable"] th,
[data-testid="stTable"] td {
    padding: 8px 10px !important;
    line-height: 1.15 !important;
}
[data-testid="stTable"] th:first-child,
[data-testid="stTable"] td:first-child {
    text-align: left !important;
}
[data-testid="stTable"] th:not(:first-child),
[data-testid="stTable"] td:not(:first-child) {
    text-align: center !important;
}


    /* C4.5.13 true compact mobile UI */
    #MainMenu {visibility:hidden !important;}
    footer {visibility:hidden !important;}
    header[data-testid="stHeader"] {visibility:hidden !important; height:0 !important;}
    [data-testid="stToolbar"] {display:none !important;}
    [data-testid="stDecoration"] {display:none !important;}
    .block-container {max-width: 820px; padding: 0.25rem 0.75rem 1rem 0.75rem !important;}
    .stApp { background: #efe8d8 !important; }
    .cc-table-wrap {background:#fffaf0; border:1px solid #d7cdbc; border-radius:10px; overflow:hidden; margin:0.45rem 0 0.75rem 0;}
    table.cc-mobile-table {width:100%; border-collapse:collapse; table-layout:fixed;}
    table.cc-mobile-table th, table.cc-mobile-table td {
        padding: 8px 10px !important;
        line-height: 1.15 !important;
        border-bottom: 1px solid rgba(7,31,69,.14);
        vertical-align: middle;
        color:#071f45;
        font-size:.92rem;
    }
    table.cc-mobile-table th {font-weight:800; background:rgba(255,255,255,.42);}
    table.cc-mobile-table tr:last-child td {border-bottom:0;}
    table.cc-mobile-table th:first-child, table.cc-mobile-table td:first-child {text-align:left !important;}
    table.cc-mobile-table th:not(:first-child), table.cc-mobile-table td:not(:first-child) {text-align:center !important;}
    table.cc-mobile-table a {color:#0050a4 !important; text-decoration:none !important; font-weight:800;}
    table.cc-mobile-table tr:nth-child(even) {background:rgba(255,255,255,.32);}
    .cc-chevron {color:#071f45; font-weight:900;}
    .cc-back-bottom button {width:100%;}
    .cc-status-dot {font-size:.95rem;}
    .cc-debug-hidden {display:none !important;}


    /* C4.5.13 selectable dataframe polish: no row buttons, compact rows, correct alignment */
    #MainMenu {visibility:hidden !important;}
    footer {visibility:hidden !important;}
    header[data-testid="stHeader"] {visibility:hidden !important; height:0 !important;}
    [data-testid="stToolbar"] {display:none !important;}
    [data-testid="stDecoration"] {display:none !important;}
    .block-container {max-width: 820px; padding: 0.25rem 0.75rem 1rem 0.75rem !important;}
    .stApp { background: #efe8d8 !important; }

    div[data-testid="stDataFrame"] {
        border: 1px solid #d7cdbc !important;
        border-radius: 10px !important;
        overflow: hidden !important;
        background: #fffaf0 !important;
    }

    div[data-testid="stDataFrame"] * {
        font-size: 0.92rem !important;
    }

    div[data-testid="stDataFrame"] div[role="row"] {
        min-height: 30px !important;
        max-height: 34px !important;
    }

    div[data-testid="stDataFrame"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="columnheader"] {
        padding-top: 3px !important;
        padding-bottom: 3px !important;
        line-height: 1.1 !important;
        border-bottom: 1px solid rgba(7,31,69,.12) !important;
    }

    /* first visible data column left aligned */
    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="1"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="1"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }

    /* if row-selector checkbox is counted as col 1, align col 2 left too */
    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="2"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="2"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }

    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="3"],
    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="4"],
    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="5"],
    div[data-testid="stDataFrame"] div[role="gridcell"][aria-colindex="6"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="3"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="4"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="5"],
    div[data-testid="stDataFrame"] div[role="columnheader"][aria-colindex="6"] {
        justify-content: center !important;
        text-align: center !important;
    }


    /* C4.5.13 tiny nav links */
    .cc-mini-nav {
        display:flex;
        justify-content:flex-end;
        gap:0.85rem;
        font-size:0.82rem;
        line-height:1.1;
        margin:0.05rem 0 0.15rem 0;
    }
    .cc-mini-nav a {
        color:#0050a4 !important;
        text-decoration:none !important;
        font-weight:700;
    }


    /* C4.5.13 same-session tiny nav buttons */
    div[data-testid="stHorizontalBlock"] button[kind="tertiary"],
    button[kind="tertiary"] {
        background: transparent !important;
        color: #0050a4 !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        min-height: 1.1rem !important;
        height: auto !important;
        font-size: 0.82rem !important;
        font-weight: 700 !important;
    }
    div[data-testid="stHorizontalBlock"] button[kind="tertiary"]:hover,
    button[kind="tertiary"]:hover {
        color: #003b78 !important;
        text-decoration: underline !important;
    }


    /* C4.5.13 mobile Safari light-mode/login cleanup */
    html, body, .stApp, [data-testid="stAppViewContainer"] {
        background: #efe8d8 !important;
        color: #071f45 !important;
        color-scheme: light !important;
    }
    * {
        color-scheme: light !important;
    }
    label, p, span, div, h1, h2, h3, h4 {
        color: #071f45 !important;
        -webkit-text-fill-color: inherit !important;
    }
    input, textarea, select {
        background-color: #ffffff !important;
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
        border: 1px solid #9aa3b2 !important;
        border-radius: 8px !important;
        caret-color: #071f45 !important;
    }
    input::placeholder, textarea::placeholder {
        color: #6b7280 !important;
        -webkit-text-fill-color: #6b7280 !important;
        opacity: 1 !important;
    }
    input:-webkit-autofill,
    input:-webkit-autofill:hover,
    input:-webkit-autofill:focus {
        -webkit-box-shadow: 0 0 0px 1000px #ffffff inset !important;
        -webkit-text-fill-color: #071f45 !important;
        caret-color: #071f45 !important;
    }
    [data-testid="stTextInput"] input,
    [data-testid="stPasswordInput"] input {
        background: #ffffff !important;
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
    }
    [data-testid="stForm"] {
        background: #fffaf0 !important;
        border: 1px solid #d7cdbc !important;
        border-radius: 10px !important;
    }
    .cc-login-help {
        font-size: .82rem;
        color: #526070 !important;
        margin-top: .35rem;
    }


    /* C4.5.13 final mobile readability cleanup */
    html, body, .stApp, [data-testid="stAppViewContainer"] {
        background: #efe8d8 !important; color: #071f45 !important; color-scheme: light !important;
    }
    .block-container {padding-top: .35rem !important; padding-left:.75rem !important; padding-right:.75rem !important; max-width:820px !important;}
    h1,h2,h3,h4,h5,h6,p,span,div,label {color:#071f45 !important; -webkit-text-fill-color:#071f45 !important;}
    .stButton > button, div[data-testid="stFormSubmitButton"] button, button[kind="primary"], button[kind="secondary"] {
        background:#a80f18 !important; background-color:#a80f18 !important; color:#fff !important; -webkit-text-fill-color:#fff !important;
        border:1px solid #7c0b12 !important; border-radius:8px !important; font-weight:800 !important;
    }
    .stButton > button *, div[data-testid="stFormSubmitButton"] button *, button[kind="primary"] *, button[kind="secondary"] * {
        color:#fff !important; -webkit-text-fill-color:#fff !important;
    }
    button[kind="tertiary"], button[kind="tertiary"] * {
        background:transparent !important; color:#0050a4 !important; -webkit-text-fill-color:#0050a4 !important;
        border:none !important; box-shadow:none !important; padding:0 !important; min-height:1rem !important; height:auto !important; font-size:.82rem !important;
    }
    input,textarea,select,[data-testid="stTextInput"] input,[data-testid="stPasswordInput"] input {
        background:#fff !important; background-color:#fff !important; color:#071f45 !important; -webkit-text-fill-color:#071f45 !important;
        border:1px solid #9aa3b2 !important; border-radius:8px !important; caret-color:#071f45 !important;
    }
    input::placeholder, textarea::placeholder {color:#6b7280 !important; -webkit-text-fill-color:#6b7280 !important; opacity:1 !important;}
    input:-webkit-autofill,input:-webkit-autofill:hover,input:-webkit-autofill:focus {
        -webkit-box-shadow:0 0 0 1000px #fff inset !important; -webkit-text-fill-color:#071f45 !important; caret-color:#071f45 !important;
    }
    [data-testid="stForm"] {background:#fffaf0 !important; border:1px solid #d7cdbc !important; border-radius:10px !important;}
    div[data-testid="stDataFrame"], div[data-testid="stDataFrame"] * {
        background-color:#fffaf0 !important; color:#071f45 !important; -webkit-text-fill-color:#071f45 !important;
    }
    div[data-testid="stDataFrame"] div[role="row"]:nth-child(even) {background-color:#f8f4ea !important;}
    div[data-testid="stDataFrame"] div[role="gridcell"], div[data-testid="stDataFrame"] div[role="columnheader"] {
        color:#071f45 !important; -webkit-text-fill-color:#071f45 !important; border-bottom:1px solid rgba(7,31,69,.14) !important;
    }
    .cc-login-logo-wrap {text-align:center; margin:.2rem auto .65rem auto;}
    .cc-login-logo-wrap img {max-width:220px; height:auto;}


    /* C4.5.13 downloaded-list preservation + dataframe readability */
    div[data-testid="stDataFrame"] {
        background-color: #fffaf0 !important;
        border: 1px solid #d7cdbc !important;
        border-radius: 10px !important;
        overflow: hidden !important;
    }
    div[data-testid="stDataFrame"] * {
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
    }
    div[data-testid="stDataFrame"] [role="columnheader"],
    div[data-testid="stDataFrame"] [role="gridcell"] {
        background-color: #fffaf0 !important;
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
    }
    div[data-testid="stDataFrame"] [role="row"]:nth-child(even) [role="gridcell"] {
        background-color: #f8f4ea !important;
    }
    div[data-testid="stDataFrame"] svg,
    div[data-testid="stDataFrame"] path {
        color: #071f45 !important;
        fill: #071f45 !important;
    }


    /* C4.5.13 visible compact rows: avoid Streamlit dataframe invisible canvas text */
    .cc-row-head {
        border-bottom: 1px solid rgba(7,31,69,.25);
        padding: 5px 0 6px 0;
        font-weight: 900;
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
    }
    .cc-cell {
        border-bottom: 1px solid rgba(7,31,69,.14);
        padding: 4px 0 5px 0;
        min-height: 24px;
        color: #071f45 !important;
        -webkit-text-fill-color: #071f45 !important;
    }
    .cc-cell-center { text-align: center; }
    .cc-row-note {
        font-size: .8rem;
        color: #526070 !important;
        -webkit-text-fill-color: #526070 !important;
        text-align:center;
        margin:.35rem 0;
    }
    /* Tertiary row buttons should look like blue tappable text */
    button[kind="tertiary"],
    button[kind="tertiary"] *,
    div[data-testid="stButton"] button[kind="tertiary"],
    div[data-testid="stButton"] button[kind="tertiary"] * {
        background: transparent !important;
        background-color: transparent !important;
        color: #0050a4 !important;
        -webkit-text-fill-color: #0050a4 !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        margin: 0 !important;
        min-height: 1rem !important;
        height: auto !important;
        font-weight: 900 !important;
        text-align: left !important;
        justify-content: flex-start !important;
    }
    div[data-testid="stButton"] button[kind="tertiary"]:hover {
        text-decoration: underline !important;
    }

</style>
    """,
    unsafe_allow_html=True,
)




# C4.6.18 Mobile — precinct-first routing
def cc_mobile_should_open_precinct_first(assignment):
    if not isinstance(assignment, dict):
        return False
    mode = cc_mobile_clean_value(assignment.get("mobile_open_mode")).lower()
    group = cc_mobile_clean_value(assignment.get("mobile_group_by")).lower()
    if mode == "precinct_first" or group == "precinct":
        return True
    precincts = assignment.get("precincts") or assignment.get("hierarchy") or []
    if isinstance(precincts, list) and len(precincts) > 1:
        return True
    name = cc_mobile_clean_value(assignment.get("name") or assignment.get("title") or assignment.get("street_area") or assignment.get("area")).lower()
    return "whole universe" in name


def cc_mobile_assignment_precinct_rows(assignment):
    assignment = cc_mobile_normalize_assignment(assignment) if isinstance(assignment, dict) else {}
    precincts = assignment.get("precincts") or assignment.get("hierarchy") or []
    rows = []
    if isinstance(precincts, list):
        for p in precincts:
            if not isinstance(p, dict):
                continue
            rows.append({
                "precinct": cc_mobile_clean_value(p.get("precinct") or p.get("name") or "Unassigned Precinct"),
                "streets": p.get("streets") or [],
                "street_count": int(p.get("street_count") or len(p.get("streets") or [])),
                "household_count": int(p.get("household_count") or 0),
                "voter_count": int(p.get("voter_count") or 0),
            })
    return rows


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


def tiny_nav() -> None:
    """Small same-session home/logout controls. No HTML links, no new browser window."""
    try:
        spacer, home_col, logout_col = st.columns([0.76, 0.11, 0.13])
        with home_col:
            if st.button("Home", key="cc_tiny_home", type="tertiary"):
                st.session_state["field_page"] = "lists"
                for k in ["selected_street", "household_idx"]:
                    st.session_state.pop(k, None)
                st.rerun()
        with logout_col:
            if st.button("Log Out", key="cc_tiny_logout", type="tertiary"):
                for k in [
                    "field_user", "field_page", "assignments", "assignment_idx",
                    "selected_street", "household_idx"
                ]:
                    st.session_state.pop(k, None)
                try:
                    st.query_params.clear()
                except Exception:
                    pass
                st.rerun()
    except Exception:
        pass

def handle_field_nav_actions() -> None:
    """Legacy no-op. Navigation now uses same-session buttons."""
    return


def login_screen() -> None:
    try:
        _login_logo_uri = img_data_uri(LOGO_CANDIDATE_CONNECT) if "img_data_uri" in globals() and "LOGO_CANDIDATE_CONNECT" in globals() else ""
    except Exception:
        _login_logo_uri = ""
    if _login_logo_uri:
        st.markdown(f"<div class='cc-login-logo-wrap'><img src='{_login_logo_uri}' /></div>", unsafe_allow_html=True)
    else:
        st.title("Candidate Connect Field")
    st.caption("Download assignments on Wi‑Fi, record field results, then sync when back online.")

    with st.form("field_login"):
        username = st.text_input("Email", key="email", placeholder="name@example.com", help="Use your Field App login email.")
        password = st.text_input("Password", type="password", key="current-password", placeholder="Password")
        remember = st.checkbox("Keep me signed in on this device", value=True)
        submitted = st.form_submit_button("Log In", type="primary")
        st.markdown("<div class='cc-login-help'>For best phone autofill, save this login when your browser prompts after a successful sign-in.</div>", unsafe_allow_html=True)

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
    try:
        if remember:
            st.query_params["ccu"] = uname
            st.query_params["cct"] = _saved_login_token(uname, user)
    except Exception:
        pass
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
            items = raw.get("assignments") or raw.get("work_items") or raw.get("items") or ([] if not (raw.get("assignment") or raw.get("hierarchy") or raw.get("precincts")) else [raw])
            if isinstance(items, list) and items:
                st.session_state["last_assignment_source_key"] = key
                return items
        if isinstance(raw, list) and raw:
            st.session_state["last_assignment_source_key"] = key
            return raw
    return []



def merge_results_for_sync(local_payload: dict, server_payload: dict) -> dict:
    """Merge local queued results into campaign mobile_results using stable keys so users/programs see one current record."""
    campaign_id = local_payload.get("campaign_id") or server_payload.get("campaign_id") or "default"
    merged = empty_results(campaign_id)
    by_key: dict[str, dict] = {}

    for r in (server_payload.get("synced") or []):
        if not isinstance(r, dict):
            continue
        key = result_key_for_voter(r.get("campaign_id"), r.get("assignment_id"), r.get("household_key"), r.get("voter_id"))
        if key:
            by_key[key] = r

    now = datetime.now(timezone.utc).isoformat()
    for r in (local_payload.get("synced") or []):
        if not isinstance(r, dict):
            continue
        key = result_key_for_voter(r.get("campaign_id"), r.get("assignment_id"), r.get("household_key"), r.get("voter_id"))
        if key:
            by_key[key] = r

    for r in (local_payload.get("queued") or []):
        if not isinstance(r, dict):
            continue
        item = dict(r)
        item["sync_status"] = "synced"
        item["synced_at"] = now
        key = result_key_for_voter(item.get("campaign_id"), item.get("assignment_id"), item.get("household_key"), item.get("voter_id"))
        if key:
            by_key[key] = item

    merged["synced"] = list(by_key.values())
    merged["failed"] = list(server_payload.get("failed") or []) + list(local_payload.get("failed") or [])
    merged["queued"] = []
    merged["last_sync"] = datetime.now().isoformat(timespec="seconds")
    return merged


def cc_header(title: str, subtitle: str = "", show_logo: bool = True) -> None:
    logo_html = f'<img class="cc-logo" src="data:image/png;base64,{CC_LOGO_B64}" />' if (show_logo and CC_LOGO_B64) else '<span class="cc-title">CC</span>'
    st.markdown(
        f"""
        <div class="cc-topbar">
            {logo_html}
            <div>
                <div class="cc-title">{title}</div>
                <div class="cc-subtitle">{subtitle}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def parse_street(address: str) -> str:
    s = clean_value(address).upper()
    if "," in s:
        s = s.split(",", 1)[0]
    # remove unit-like suffixes lightly, then leading house number
    s = re.sub(r"^\s*\d+[A-Z]?\s+", "", s).strip()
    return s or "UNKNOWN STREET"


def hh_address(hh: dict) -> str:
    addr = _first_value(hh, ["Address", "address", "Residence Address", "Street Address"])
    city = _first_value(hh, ["City", "city", "Municipality", "municipality"])
    if addr and city and city.upper() not in addr.upper():
        return f"{addr}, {city}"
    return addr or "Unknown address"


def voter_party(v: dict) -> str:
    return _first_value(v, ["Party", "party", "CalculatedParty"]) or "—"


def voter_age(v: dict) -> str:
    return _first_value(v, ["Age", "age"]) or "—"


def voter_mb_icon(v: dict) -> str:
    val = (_first_value(v, ["MB Perm", "MB_Perm", "MBPERM", "mail_ballot_perm", "Mail Ballot Permanent", "Perm MB"]) or "").upper()
    return "✉" if val in {"Y", "YES", "TRUE", "1", "PERM"} else ""


def get_assignment_label(item: dict, idx: int = 0) -> str:
    try:
        return cc21_assignment_label(item, idx)
    except Exception:
        return (
            clean_value(item.get("label"))
            or clean_value(item.get("street"))
            or clean_value(item.get("precinct"))
            or clean_value(item.get("assignment_name"))
            or f"Assignment {idx + 1}"
        )


def assignment_maps(item: dict) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    households = _assignment_households(item)
    voters = _assignment_voters(item)
    voter_map: dict[str, list[dict]] = {}
    for v in voters:
        hk = _voter_household_key(v)
        voter_map.setdefault(hk, []).append(v)
    # Fallback map by address for records missing household key
    for hh in households:
        hk = _household_key(hh)
        if hk and voter_map.get(hk):
            continue
        addr = _first_value(hh, ["Address", "address", "Residence Address", "Street Address"])
        if addr:
            matched = [v for v in voters if _first_value(v, ["Address", "address", "Residence Address", "Street Address"]) == addr]
            if matched:
                voter_map[hk] = matched
    return households, voters, voter_map




# C4.6.21 Mobile — actual route patch: assignment click opens precincts before streets
def cc21_get_assignment_meta(item: dict) -> dict:
    if isinstance(item, dict) and isinstance(item.get("assignment"), dict):
        return item.get("assignment") or {}
    return {}


def cc21_get_program_meta(item: dict) -> dict:
    if isinstance(item, dict) and isinstance(item.get("program"), dict):
        return item.get("program") or {}
    return {}


def cc21_get_hierarchy(item: dict) -> list:
    if not isinstance(item, dict):
        return []
    for src in [item, cc21_get_assignment_meta(item), _assignment_payload(item)]:
        if isinstance(src, dict):
            for key in ["hierarchy", "precincts"]:
                val = src.get(key)
                if isinstance(val, list) and val:
                    return val
    return []


def cc21_should_open_precincts(item: dict) -> bool:
    hierarchy = cc21_get_hierarchy(item)
    if len(hierarchy) > 1:
        return True
    meta = cc21_get_assignment_meta(item)
    scope = clean_value(meta.get("scope") or meta.get("street_area") or item.get("scope") or item.get("street_area")).lower()
    return "whole universe" in scope


def cc21_assignment_label(item: dict, idx: int = 0) -> str:
    meta = cc21_get_assignment_meta(item)
    prog = cc21_get_program_meta(item)
    return (
        clean_value(item.get("label"))
        or clean_value(item.get("name"))
        or clean_value(meta.get("name"))
        or clean_value(item.get("assignment_name"))
        or clean_value(prog.get("name"))
        or f"Assignment {idx + 1}"
    )


def cc21_assignment_counts(item: dict) -> tuple[int, int, int, int]:
    hierarchy = cc21_get_hierarchy(item)
    if hierarchy:
        precincts = len(hierarchy)
        streets = 0
        houses = 0
        voters = 0
        for p in hierarchy:
            if not isinstance(p, dict):
                continue
            p_streets = p.get("streets") if isinstance(p.get("streets"), list) else []
            streets += len(p_streets)
            houses += int(p.get("household_count") or cc22_household_count_from_streets(p_streets) or 0)
            voters += int(p.get("voter_count") or cc22_voter_count_from_streets(p_streets) or 0)
        return precincts, streets, houses, voters
    households, voters, _ = assignment_maps(item)
    streets = len(sorted({parse_street(hh_address(h)) for h in households}))
    return 0, streets, len(households), len(voters)


def cc21_precinct_rows(item: dict) -> list[dict]:
    rows = []
    for p in cc21_get_hierarchy(item):
        if not isinstance(p, dict):
            continue
        precinct = clean_value(p.get("precinct") or p.get("name") or "Unassigned Precinct")
        streets = p.get("streets") if isinstance(p.get("streets"), list) else []
        houses = len(cc27_households_from_streets(streets))
        voters = 0
        for _s in streets:
            if isinstance(_s, dict):
                voters += int(_s.get("voter_count") or 0)
        progress = cc27_progress_label_for_streets(streets)
        rows.append({
            "Precinct": precinct,
            "Streets": len(streets),
            "Houses": houses,
            "Voters": voters,
            "Progress": progress,
        })
    return rows


def cc21_flatten_households_from_streets(streets: list[dict]) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    households = []
    voters = []
    voter_map = {}
    for s in streets or []:
        if not isinstance(s, dict):
            continue
        for hh in s.get("households") or []:
            if not isinstance(hh, dict):
                continue
            households.append(hh)
            hk = _household_key(hh) or clean_value(hh.get("Household Key") or hh.get("household_id") or hh.get("Address"))
            hv = hh.get("voters") if isinstance(hh.get("voters"), list) else []
            # In the package, household rows often aggregate voter info but may not include individual voter dicts.
            if not hv:
                hv = [{
                    "Name": clean_value(hh.get("Names") or hh.get("Name")),
                    "Party": clean_value(hh.get("Party")),
                    "Age": clean_value(hh.get("Ages")),
                    "Household Key": clean_value(hh.get("Household Key") or hk),
                    "Address": clean_value(hh.get("Address")),
                }]
            voter_map[hk] = [v for v in hv if isinstance(v, dict)]
            voters.extend(voter_map[hk])
    return households, voters, voter_map




# C4.6.22 Mobile — count/status helpers for hierarchy tables
def cc22_household_count_from_streets(streets):
    total = 0
    for s in streets or []:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("households"), list):
            total += len(s.get("households") or [])
        else:
            total += int(s.get("household_count") or s.get("houses") or 0)
    return total


def cc22_voter_count_from_streets(streets):
    total = 0
    for s in streets or []:
        if not isinstance(s, dict):
            continue
        if s.get("voter_count") is not None:
            total += int(s.get("voter_count") or 0)
            continue
        for hh in s.get("households") or []:
            if isinstance(hh, dict):
                total += int(hh.get("Voters") or hh.get("voters_count") or len(hh.get("voters") or []) or 0)
    return total


def cc22_household_key_from_hh(hh):
    if not isinstance(hh, dict):
        return ""
    return clean_value(
        hh.get("Household Key")
        or hh.get("household_key")
        or hh.get("household_id")
        or hh.get("Address")
        or hh.get("address")
    )


def cc22_recorded_result_household_keys():
    return cc24_completed_household_keyset()


def cc22_completion_for_streets(streets):
    done, total = cc25_completion_for_streets(streets)
    if total <= 0:
        return 0, 0, "Not Started"
    if done <= 0:
        status = "Not Started"
    elif done >= total:
        status = "Complete"
    else:
        status = "In Progress"
    return done, total, status


def cc22_status_label_for_streets(streets):
    return cc27_progress_label_for_streets(streets)



# C4.6.24 Mobile — robust local completion/progress resolver
def cc24_norm(value):
    try:
        return "" if value is None else str(value).strip().upper()
    except Exception:
        return ""


def cc24_household_keys_from_household(hh):
    if not isinstance(hh, dict):
        return set()
    vals = set()
    for k in [
        "Household Key", "household_key", "household_id", "HouseholdID",
        "HH_ID", "hh_id", "Address", "address", "FullAddress", "full_address"
    ]:
        v = cc24_norm(hh.get(k))
        if v:
            vals.add(v)
    # common package format
    addr = cc24_norm(hh.get("Address") or hh.get("address"))
    precinct = cc24_norm(hh.get("Precinct") or hh.get("precinct"))
    if addr and precinct:
        vals.add(f"{precinct}|{addr}")
    return vals


def cc24_household_keys_from_result(rec):
    if not isinstance(rec, dict):
        return set()
    vals = set()
    for k in [
        "household_key", "Household Key", "household_id", "HouseholdID",
        "hh_key", "HH_ID", "address", "Address", "full_address", "FullAddress",
        "selected_household_key", "current_household_key"
    ]:
        v = cc24_norm(rec.get(k))
        if v:
            vals.add(v)
    precinct = cc24_norm(rec.get("precinct") or rec.get("Precinct"))
    addr = cc24_norm(rec.get("address") or rec.get("Address"))
    if precinct and addr:
        vals.add(f"{precinct}|{addr}")
    return vals


def cc24_result_is_completed_interaction(rec):
    if not isinstance(rec, dict):
        return False
    # Anything that has a meaningful canvass result/contact status counts as an interaction.
    for k in ["result", "Result", "contact_result", "status", "Contact Status", "outcome", "disposition"]:
        v = cc24_norm(rec.get(k))
        if v and v not in {"", "QUEUED", "SYNCED", "FAILED", "PENDING", "NONE", "NAN"}:
            return True
    # Notes/tags can also indicate a saved contact record, but only when tied to a household.
    if cc24_household_keys_from_result(rec) and (cc24_norm(rec.get("notes")) or cc24_norm(rec.get("Notes")) or rec.get("tags_added")):
        return True
    return False


def cc24_collect_local_results():
    """
    Collect queued and synced result records from common mobile app locations.
    This intentionally scans session_state because the exact local staging key has changed
    across C4.2-C4.6 builds.
    """
    found = []
    for key, val in list(st.session_state.items()):
        lk = str(key).lower()
        if not any(tok in lk for tok in ["result", "queue", "queued", "sync", "synced", "contact", "mobile"]):
            continue
        if isinstance(val, list):
            found.extend([x for x in val if isinstance(x, dict)])
        elif isinstance(val, dict):
            for subk in ["results", "queued", "queue", "synced", "items", "records", "mobile_results", "contact_results"]:
                if isinstance(val.get(subk), list):
                    found.extend([x for x in val.get(subk) if isinstance(x, dict)])
            # Sometimes the dict itself is a single result record.
            if cc24_household_keys_from_result(val):
                found.append(val)

    # Also check likely globals if present.
    for name in ["mobile_results", "queued_results", "sync_queue", "results_queue", "local_results"]:
        try:
            val = globals().get(name)
            if isinstance(val, list):
                found.extend([x for x in val if isinstance(x, dict)])
            elif isinstance(val, dict):
                for subk in ["results", "queued", "queue", "synced", "items", "records"]:
                    if isinstance(val.get(subk), list):
                        found.extend([x for x in val.get(subk) if isinstance(x, dict)])
        except Exception:
            pass
    return found


def cc24_completed_household_keyset():
    keys = set()
    for rec in cc24_collect_local_results():
        if not cc24_result_is_completed_interaction(rec):
            continue
        keys.update(cc24_household_keys_from_result(rec))
    return keys


def cc24_completion_for_households(households):
    households = [h for h in (households or []) if isinstance(h, dict)]
    total = len(households)
    if total <= 0:
        return 0, 0
    completed_keys = cc24_completed_household_keyset()
    done = 0
    for hh in households:
        hh_keys = cc24_household_keys_from_household(hh)
        if hh_keys and completed_keys.intersection(hh_keys):
            done += 1
    return done, total


def cc24_completion_for_streets(streets):
    households = []
    for s in streets or []:
        if isinstance(s, dict):
            households.extend([h for h in (s.get("households") or []) if isinstance(h, dict)])
    return cc24_completion_for_households(households)


def cc24_progress_label(done, total):
    try:
        return f"{int(done):,} / {int(total):,} ›"
    except Exception:
        return "0 / 0 ›"



def result_key_for_voter(campaign_id: str, assignment_id: str, household_key: str, voter_id: str) -> str:
    """Stable key used locally and on sync so edits replace previous results instead of disappearing/duplicating."""
    return "|".join([
        clean_value(campaign_id),
        clean_value(assignment_id),
        clean_value(household_key),
        clean_value(voter_id),
    ])



def result_index(local_payload: dict) -> dict[str, dict]:
    """Latest local result by stable voter key. Queued overrides synced so local edits remain visible after sync."""
    idx: dict[str, dict] = {}
    for bucket in ["synced", "queued"]:
        for r in local_payload.get(bucket) or []:
            if not isinstance(r, dict):
                continue
            key = result_key_for_voter(
                clean_value(r.get("campaign_id")),
                clean_value(r.get("assignment_id")),
                clean_value(r.get("household_key")),
                clean_value(r.get("voter_id")),
            )
            if key:
                idx[key] = r
    return idx


def result_records_for_household(local_payload: dict, campaign_id: str, assignment_id: str, household_key: str) -> list[dict]:
    """Return all local records for a household, forgiving assignment-id changes but preferring the current assignment."""
    hk = clean_value(household_key)
    exact = []
    fallback = []
    for bucket in ["synced", "queued"]:
        for r in local_payload.get(bucket) or []:
            if not isinstance(r, dict):
                continue
            if clean_value(r.get("campaign_id")) != clean_value(campaign_id):
                continue
            if clean_value(r.get("household_key")) != hk:
                continue
            if clean_value(r.get("assignment_id")) == clean_value(assignment_id):
                exact.append(r)
            else:
                fallback.append(r)
    return exact or fallback


def existing_result_for_voter(local_payload: dict, campaign_id: str, assignment_id: str, household_key: str, voter_id: str) -> dict:
    """Find existing result for pre-filling the voter result form."""
    idx = result_index(local_payload)
    exact = idx.get(result_key_for_voter(campaign_id, assignment_id, household_key, voter_id))
    if exact:
        return exact
    for r in result_records_for_household(local_payload, campaign_id, assignment_id, household_key):
        if clean_value(r.get("voter_id")) == clean_value(voter_id):
            return r
    return {}


def upsert_local_result(local_payload: dict, item: dict) -> dict:
    """Remove any older local queued/synced result for the same voter and queue the updated result."""
    key = result_key_for_voter(item.get("campaign_id"), item.get("assignment_id"), item.get("household_key"), item.get("voter_id"))
    for bucket in ["queued", "synced"]:
        kept = []
        for r in local_payload.get(bucket) or []:
            if not isinstance(r, dict):
                continue
            rkey = result_key_for_voter(r.get("campaign_id"), r.get("assignment_id"), r.get("household_key"), r.get("voter_id"))
            if rkey != key:
                kept.append(r)
        local_payload[bucket] = kept
    local_payload.setdefault("queued", []).append(item)
    return local_payload



def household_status(local_payload: dict, campaign_id: str, assignment_id: str, hh: dict, hh_voters: list[dict]) -> tuple[str, int, int]:
    """Progress for one household using the same local result records used by the voter form."""
    hk = _household_key(hh)
    total = max(len(hh_voters), 1)
    done_voters = set()

    if hh_voters:
        for v in hh_voters:
            vid = _voter_id(v) or _voter_name(v)
            if existing_result_for_voter(local_payload, campaign_id, assignment_id, hk, vid):
                done_voters.add(clean_value(vid))
    else:
        if result_records_for_household(local_payload, campaign_id, assignment_id, hk):
            done_voters.add(clean_value(hk))

    done = len(done_voters)
    if done <= 0:
        return "⚪ Not Started", done, total
    if done >= total:
        return "🟢 Complete", done, total
    return "🟡 In Progress", done, total


def assignment_progress_for_households(local_payload: dict, campaign_id: str, assignment_id: str, households: list[dict], voter_map: dict[str, list[dict]]) -> tuple[int, int]:
    """Count completed households using the same household_status() logic as Street/Houses screens."""
    total_houses = len([h for h in (households or []) if isinstance(h, dict)])
    completed_houses = 0
    for h in households or []:
        if not isinstance(h, dict):
            continue
        hv = voter_map.get(_household_key(h), [])
        _status, done, total = household_status(local_payload, campaign_id, assignment_id, h, hv)
        if total > 0 and done >= total:
            completed_houses += 1
    return completed_houses, total_houses


def flatten_hierarchy_streets_to_maps(streets: list[dict]) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """Use the hierarchy street/household package shape to build households, voters, and voter_map."""
    households = []
    voters = []
    voter_map: dict[str, list[dict]] = {}
    for s in streets or []:
        if not isinstance(s, dict):
            continue
        for hh in s.get("households") or []:
            if not isinstance(hh, dict):
                continue
            households.append(hh)
            hk = _household_key(hh) or clean_value(hh.get("Household Key") or hh.get("household_id") or hh.get("Address"))
            hv = hh.get("voters") if isinstance(hh.get("voters"), list) else []
            if not hv:
                names = [x.strip() for x in clean_value(hh.get("Names") or hh.get("Name")).split(";") if x.strip()]
                if not names:
                    names = [clean_value(hh.get("Names") or hh.get("Name") or hk)]
                hv = []
                for idx, name in enumerate(names):
                    hv.append({
                        "Name": name,
                        "Party": clean_value(hh.get("Party")),
                        "Age": clean_value(hh.get("Ages")),
                        "Household Key": hk,
                        "Address": clean_value(hh.get("Address")),
                        "voter_id": f"{hk}|{name or idx}",
                    })
            voter_map[hk] = [v for v in hv if isinstance(v, dict)]
            voters.extend(voter_map[hk])
    return households, voters, voter_map


def progress_label_for_households(local_payload: dict, campaign_id: str, assignment_id: str, households: list[dict], voter_map: dict[str, list[dict]]) -> str:
    done, total = assignment_progress_for_households(local_payload, campaign_id, assignment_id, households, voter_map)
    return f"{done:,} / {total:,} ›"





def _qp_get(name: str, default: str = "") -> str:
    try:
        v = st.query_params.get(name, default)
        if isinstance(v, list):
            return str(v[0]) if v else default
        return str(v) if v is not None else default
    except Exception:
        return default

def _saved_login_token(username: str, user: dict) -> str:
    raw = f"field|{str(username or '').lower()}|{user.get('password_hash') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def try_restore_saved_login() -> None:
    if st.session_state.get("field_user"):
        return
    uname = _qp_get("ccu", "").strip().lower()
    token = _qp_get("cct", "").strip()
    if not uname or not token:
        return
    store = load_security_store()
    user = (store.get("users") or {}).get(uname)
    if not user or user.get("disabled"):
        return
    if token != _saved_login_token(uname, user):
        return
    role = str(user.get("role") or "")
    if role not in {"Field User", "Campaign Admin", "Manager", "Super Admin"}:
        return
    campaign_name = user.get("campaign") or user.get("campaign_name") or ""
    cid = user.get("campaign_id") or campaign_slug(campaign_name)
    user = dict(user)
    user["username"] = uname
    user["campaign_id"] = cid
    st.session_state["field_user"] = user

def cc_nav_href(page: str, **kwargs) -> str:
    parts = {"cc_page": page}
    try:
        ccu = _qp_get("ccu", "")
        cct = _qp_get("cct", "")
        if ccu and cct:
            parts["ccu"] = ccu
            parts["cct"] = cct
    except Exception:
        pass
    for k, v in kwargs.items():
        parts[k] = str(v)
    return "?" + "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in parts.items())

def render_compact_table(headers: list[str], rows: list[list[Any]], col_widths: list[str] | None = None) -> None:
    if col_widths and len(col_widths) == len(headers):
        cg = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in col_widths) + "</colgroup>"
    else:
        cg = ""
    html = ['<div class="cc-table-wrap"><table class="cc-mobile-table">', cg, "<thead><tr>"]
    for h in headers:
        html.append(f"<th>{h}</th>")
    html.append("</tr></thead><tbody>")
    for row in rows:
        html.append("<tr>")
        for cell in row:
            html.append(f"<td>{cell}</td>")
        html.append("</tr>")
    html.append("</tbody></table></div>")
    st.markdown("".join(html), unsafe_allow_html=True)

def handle_nav_params() -> None:
    p = _qp_get("cc_page", "")
    if not p:
        return
    st.session_state["field_page"] = p
    if _qp_get("assignment_idx", "") != "":
        try:
            st.session_state["assignment_idx"] = int(_qp_get("assignment_idx"))
        except Exception:
            pass
    if _qp_get("selected_street", "") != "":
        st.session_state["selected_street"] = unquote(_qp_get("selected_street"))
    if _qp_get("household_idx", "") != "":
        try:
            st.session_state["household_idx"] = int(_qp_get("household_idx"))
        except Exception:
            pass


def render_visible_click_rows(headers: list[str], rows: list[dict], key_prefix: str, widths: list[float]) -> int | None:
    """Visible same-session compact rows. First column is a tertiary text button."""
    selected = None
    head_cols = st.columns(widths)
    for j, h in enumerate(headers):
        with head_cols[j]:
            cls = "cc-row-head cc-cell-center" if j else "cc-row-head"
            st.markdown(f"<div class='{cls}'>{h}</div>", unsafe_allow_html=True)

    for i, row in enumerate(rows):
        cols = st.columns(widths)
        with cols[0]:
            if st.button(str(row.get(headers[0], "")), key=f"{key_prefix}_{i}", type="tertiary"):
                selected = i
        for j, h in enumerate(headers[1:], start=1):
            with cols[j]:
                st.markdown(f"<div class='cc-cell cc-cell-center'>{row.get(h, '')}</div>", unsafe_allow_html=True)
    return selected


def assignment_id_for(item: dict) -> str:
    payload = _assignment_payload(item or {})
    meta = payload.get("assignment") if isinstance(payload.get("assignment"), dict) else {}
    return clean_value((item or {}).get("assignment_id") or meta.get("mobile_assignment_id") or meta.get("source_work_item_id") or get_assignment_label(item or {}, 0))


def set_page(page: str, **kwargs) -> None:
    st.session_state["field_page"] = page
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


handle_field_nav_actions()
if "field_user" not in st.session_state:
    try_restore_saved_login()
if "field_user" not in st.session_state:
    login_screen()

user = current_user()
campaign_id = current_campaign_id()
tiny_nav()
local = load_local_results(campaign_id)
assignments_from_server = load_assignments(campaign_id, user.get("username"))
if "assignments" not in st.session_state and assignments_from_server:
    st.session_state["assignments"] = assignments_from_server
assignments = st.session_state.get("assignments")
if not assignments:
    assignments = assignments_from_server
    if assignments:
        st.session_state["assignments"] = assignments
handle_nav_params()
page = st.session_state.get("field_page", "lists")

# Page helpers
valid_items = [item for item in assignments if isinstance(item, dict)] if assignments else []
selected_assignment = valid_items[st.session_state.get("assignment_idx", 0)] if valid_items and st.session_state.get("assignment_idx", 0) < len(valid_items) else None
assignment_label = get_assignment_label(selected_assignment or {}, st.session_state.get("assignment_idx", 0)) if selected_assignment else "My Lists"
assignment_id = assignment_id_for(selected_assignment or {})
if selected_assignment and isinstance(st.session_state.get("selected_precinct_obj"), dict):
    households, voters, voter_map = cc21_flatten_households_from_streets(st.session_state.get("selected_precinct_obj", {}).get("streets") or [])
elif selected_assignment:
    households, voters, voter_map = assignment_maps(selected_assignment or {})
else:
    households, voters, voter_map = ([], [], {})

# MY LISTS screen includes login/sync block
if page == "lists":
    cc_header("Mobile", f"Logged in as {user.get('username')} · Campaign: {campaign_id}")
    q, s, f = st.columns(3)
    q.metric("Queued", len(local.get("queued") or []))
    s.metric("Synced", len(local.get("synced") or []))
    f.metric("Failed", len(local.get("failed") or []))
    st.caption(f"Last Sync: {local.get('last_sync') or 'Never'}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Refresh / Download Assignments", key="refresh_assignments"):
            fresh = load_assignments(campaign_id, user.get("username"))
            if fresh:
                st.session_state["assignments"] = fresh
                st.success(f"Downloaded {len(fresh)} assignment item(s).")
                if st.session_state.get("last_assignment_source_key"):
                    st.caption(f"Source: {st.session_state.get('last_assignment_source_key')}")
                st.rerun()
            else:
                existing = st.session_state.get("assignments") or assignments_from_server or []
                if existing:
                    st.session_state["assignments"] = existing
                    st.warning("No new assignment package found on the server. Keeping your downloaded list on this device.")
                else:
                    st.warning("No assignment package found yet. Ask the campaign admin to generate/export the mobile assignment package.")
    with c2:
        if st.button("Sync Now", key="sync_now"):
            server = load_server_results(campaign_id)
            merged = merge_results_for_sync(local, server)
            ok, msg = put_json_r2(f"app_state/mobile_results/{campaign_id}.json", merged)
            if ok:
                save_local_results(campaign_id, merged)
                st.success("Synced field results to R2.")
                st.caption("Note: web reporting needs the mobile_results reader build before these appear in the web app.")
                st.caption(msg)
                st.rerun()
            else:
                local.setdefault("failed", [])
                local["failed"].append({"failed_at": datetime.now(timezone.utc).isoformat(), "reason": msg, "queued_count": len(local.get("queued") or [])})
                save_local_results(campaign_id, local)
                st.error(f"Sync failed: {msg}")
    st.divider()
    st.subheader("My Lists / Assignments")
    if not valid_items:
        st.info("No assignment package found yet. Build/assign work in the web app, then refresh here on Wi‑Fi. Refresh will not clear an already-downloaded list.")
    else:
        rows=[]
        for i,item in enumerate(valid_items):
            hierarchy = cc21_get_hierarchy(item)
            if hierarchy:
                all_streets = []
                for p in hierarchy:
                    if isinstance(p, dict):
                        all_streets.extend(p.get("streets") or [])
                item_households, item_voters, item_voter_map = flatten_hierarchy_streets_to_maps(all_streets)
                precincts = len(hierarchy)
                streets = len(all_streets)
                houses = len(item_households)
                voter_count = len(item_voters)
                item_assignment_id = assignment_id_for(item)
                progress_label = progress_label_for_households(local, campaign_id, item_assignment_id, item_households, item_voter_map)
            else:
                item_households, item_voters, item_voter_map = assignment_maps(item)
                streets = len(sorted({parse_street(hh_address(h)) for h in item_households}))
                houses = len(item_households)
                voter_count = len(item_voters)
                item_assignment_id = assignment_id_for(item)
                progress_label = progress_label_for_households(local, campaign_id, item_assignment_id, item_households, item_voter_map)
            rows.append({"List / Assignment": get_assignment_label(item, i), "Streets": streets, "Houses": houses, "Voters": voter_count, "Progress": progress_label})
        sel_idx = render_visible_click_rows(["List / Assignment", "Streets", "Houses", "Voters", "Progress"], rows, "list_visible_row", [4, 1, 1, 1, 1.15])
        if sel_idx is not None:
            target_item = valid_items[int(sel_idx)]
            if cc21_should_open_precincts(target_item):
                set_page("precincts", assignment_idx=int(sel_idx))
            else:
                set_page("streets", assignment_idx=int(sel_idx))
        st.markdown('<div class="cc-legend"><b>Legend</b><br><b>Status:</b> Not Started = no interactions · In Progress = at least one interaction · Complete = finished<br><b>Counts:</b> totals in assignment package<br><br><center>Tap a list name to view streets</center></div>', unsafe_allow_html=True)
    st.stop()

# Header for deeper screens: compact only

if page == "precincts":
    hierarchy = cc21_get_hierarchy(selected_assignment or {})
    precinct_rows = []
    streets_total = houses_total = voters_total = 0
    for p in hierarchy:
        if not isinstance(p, dict):
            continue
        p_streets = p.get("streets") if isinstance(p.get("streets"), list) else []
        p_households, p_voters, p_voter_map = flatten_hierarchy_streets_to_maps(p_streets)
        p_progress = progress_label_for_households(local, campaign_id, assignment_id, p_households, p_voter_map)
        precinct_rows.append({
            "Precinct": clean_value(p.get("precinct") or p.get("name") or "Unassigned Precinct"),
            "Streets": len(p_streets),
            "Houses": len(p_households),
            "Voters": len(p_voters),
            "Progress": p_progress,
        })
        streets_total += len(p_streets)
        houses_total += len(p_households)
        voters_total += len(p_voters)
    cc_header(f"Precincts - {assignment_label}", f"{len(precinct_rows):,} precincts · {streets_total:,} streets · {houses_total:,} houses · {voters_total:,} voters")
    if not precinct_rows:
        st.warning("This assignment does not contain precinct groups. Opening streets instead.")
        set_page("streets", assignment_idx=st.session_state.get("assignment_idx", 0))
    sel_idx = render_visible_click_rows(["Precinct", "Streets", "Houses", "Voters", "Progress"], precinct_rows, "precinct_visible_row", [4, 1, 1, 1, 1.15])
    if sel_idx is not None:
        st.session_state["selected_precinct_obj"] = hierarchy[int(sel_idx)]
        set_page("streets", assignment_idx=st.session_state.get("assignment_idx", 0))
    st.markdown('<div class="cc-legend"><b>Legend</b><br><b>Precinct:</b> tap a precinct to view its streets<br><b>Progress:</b> houses completed / total houses<br><br><center>Tap a precinct name to view streets</center></div>', unsafe_allow_html=True)
    st.markdown('<div class="cc-back-bottom">', unsafe_allow_html=True)
    if st.button("← Back to My Lists", key="back_lists_from_precincts"):
        st.session_state.pop("selected_precinct_obj", None)
        set_page("lists")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

if page == "streets":
    cc_header(f"Streets - {assignment_label}", f"{len(set(parse_street(hh_address(h)) for h in households))} streets · {len(households)} houses · {len(voters)} voters")
    street_rows=[]
    for street in sorted(set(parse_street(hh_address(h)) for h in households)):
        street_hhs=[h for h in households if parse_street(hh_address(h))==street]
        street_voters=[]
        complete=0
        for h in street_hhs:
            hv=voter_map.get(_household_key(h), [])
            street_voters.extend(hv)
            status, done, total=household_status(local, campaign_id, assignment_id, h, hv)
            if done>=total and total>0:
                complete += 1
        street_rows.append({"Street Name": street, "Houses": len(street_hhs), "Voters": len(street_voters), "Complete": f"{complete} / {len(street_hhs)} ›"})
    sel_idx = render_visible_click_rows(["Street Name", "Houses", "Voters", "Complete"], street_rows, "street_visible_row", [4, 1, 1, 1.25])
    if sel_idx is not None:
        set_page("houses", selected_street=street_rows[int(sel_idx)]["Street Name"])
    st.markdown('<div class="cc-legend"><b>Legend</b><br><b>Houses:</b> total houses on street<br><b>Voters:</b> total voters on street<br><b>Complete:</b> houses completed / total houses<br><br><center>Tap a street name to view houses</center></div>', unsafe_allow_html=True)
    st.markdown('<div class="cc-back-bottom">', unsafe_allow_html=True)
    if st.button("← Back to Precincts" if st.session_state.get("selected_precinct_obj") else "← Back to My Lists", key="back_lists"):
        if st.session_state.get("selected_precinct_obj"):
            st.session_state.pop("selected_street", None)
            st.session_state["field_page"] = "precincts"
            st.rerun()
        else:
            set_page("lists")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

if page == "houses":
    street=st.session_state.get("selected_street", "")
    street_hhs=[h for h in households if parse_street(hh_address(h))==street]
    street_voters=[]
    for h in street_hhs:
        street_voters.extend(voter_map.get(_household_key(h), []))
    cc_header(f"Houses - {street}", f"{assignment_label} · {len(street_hhs)} houses · {len(street_voters)} voters")
    rows=[]
    for i,h in enumerate(street_hhs):
        hv=voter_map.get(_household_key(h), [])
        status, done, total=household_status(local, campaign_id, assignment_id, h, hv)
        rows.append({"Address": hh_address(h), "Voters": len(hv), "Status": f"{status} ›"})
    sel_idx = render_visible_click_rows(["Address", "Voters", "Status"], rows, "house_visible_row", [4, 1, 1.5])
    if sel_idx is not None:
        set_page("voters", household_idx=int(sel_idx))
    st.markdown('<div class="cc-legend"><b>Legend - Status</b><br>⚪ Not Started = no voters completed<br>🟡 In Progress = 1 or more voters started<br>🟢 Complete = all voters completed<br><br><b>Column / Icon Legend</b><br>F = Favorable &nbsp;&nbsp; U = Undecided &nbsp;&nbsp; A = Against &nbsp;&nbsp; NH = Not Home<br>YS = Yard Sign &nbsp;&nbsp; FU = Follow Up Needed &nbsp;&nbsp; ✉ = Mail Ballot Interest &nbsp;&nbsp; V = Volunteer Interest<br><br><center>Tap an address to view / record voters</center></div>', unsafe_allow_html=True)
    st.markdown('<div class="cc-back-bottom">', unsafe_allow_html=True)
    if st.button("← Back to Streets", key="back_streets"):
        set_page("streets")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

if page == "voters":
    street=st.session_state.get("selected_street", "")
    street_hhs=[h for h in households if parse_street(hh_address(h))==street]
    hh_idx=st.session_state.get("household_idx", 0)
    selected_household=street_hhs[hh_idx] if street_hhs and hh_idx < len(street_hhs) else {}
    hk=_household_key(selected_household)
    hh_voters=voter_map.get(hk, [])
    address=hh_address(selected_household)
    cc_header(f"Voters - {address}", f"{assignment_label} · {len(hh_voters)} voter(s) at this address")
    if hh_voters:
        st.markdown('<div class="cc-card">', unsafe_allow_html=True)
        st.markdown("**Select voter(s) to record results**")
        selected_voter_ids=[]
        for i,v in enumerate(hh_voters):
            vid=_voter_id(v) or _voter_name(v) or f"voter_{i}"
            label=f"{_voter_name(v) or 'Unnamed voter'} — Party: {voter_party(v)} · Age: {voter_age(v)} {voter_mb_icon(v)}"
            if st.checkbox(label, value=True, key=f"sel_voter_{hk}_{i}"):
                selected_voter_ids.append(vid)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.warning("This household has no voter detail in the package. You can still save a household-level result.")
        selected_voter_ids=[hk]

    # Prefill from the latest local queued/synced result so data remains visible when revisiting a voter/household.
    existing_for_form = []
    for _vid in selected_voter_ids:
        existing_for_form.append(existing_result_for_voter(local, campaign_id, assignment_id, hk, _vid))
    existing_for_form = [x for x in existing_for_form if isinstance(x, dict) and x]
    first_existing = existing_for_form[0] if existing_for_form else {}
    result_options = ["Favorable", "Undecided", "Against", "Not Home"]
    existing_result = clean_value(first_existing.get("result"))
    result_index_default = result_options.index(existing_result) if existing_result in result_options else 0

    with st.form("record_household_results"):
        st.markdown("**Result** *(choose one)*")
        result=st.radio("Result", result_options, index=result_index_default, horizontal=True, label_visibility="collapsed")
        st.markdown("**Additional Information** *(check all that apply)*")
        a,b=st.columns(2)
        with a:
            yard_sign=st.checkbox("Yard Sign", value=bool(first_existing.get("yard_sign")))
            mb_interest=st.checkbox("✉ Mail Ballot Interest", value=bool(first_existing.get("mail_ballot_interest")))
        with b:
            follow_up=st.checkbox("Follow Up Needed", value=bool(first_existing.get("follow_up")))
            volunteer_interest=st.checkbox("Volunteer Interest", value=bool(first_existing.get("volunteer_interest")))
        notes=st.text_area("Notes", value=clean_value(first_existing.get("notes")), height=90)
        save_clicked=st.form_submit_button("Save Results for Selected Voters")

    if save_clicked:

        try:

            cc25_mark_household_complete(locals().get("household") or locals().get("hh") or st.session_state.get("selected_household") or st.session_state.get("current_household") or {})

        except Exception:

            pass
        if not selected_voter_ids:
            st.error("Select at least one voter.")
        else:
            local=load_local_results(campaign_id)
            queued=local.setdefault("queued", [])
            voter_lookup={(_voter_id(v) or _voter_name(v) or f"voter_{i}"): v for i,v in enumerate(hh_voters)}
            for vid in selected_voter_ids:
                v=voter_lookup.get(vid, {})
                stable_key = result_key_for_voter(campaign_id, assignment_id, hk, vid)
                item={
                    "result_id": hashlib.sha1(stable_key.encode()).hexdigest()[:16],
                    "campaign_id": campaign_id,
                    "username": user.get("username"),
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_label,
                    "household_key": hk,
                    "household_address": address,
                    "precinct": clean_value((st.session_state.get("selected_precinct_obj") or {}).get("precinct")),
                    "street": clean_value(st.session_state.get("selected_street")),
                    "voter_id": vid,
                    "voter_name": _voter_name(v),
                    "party": voter_party(v),
                    "age": voter_age(v),
                    "result": result,
                    "yard_sign": bool(yard_sign),
                    "mail_ballot_interest": bool(mb_interest),
                    "follow_up": bool(follow_up),
                    "volunteer_interest": bool(volunteer_interest),
                    "notes": notes,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "field_app",
                    "sync_status": "queued",
                }
                local = upsert_local_result(local, item)
            save_local_results(campaign_id, local)
            st.success("Saved locally. Returning to houses.")
            st.session_state["field_page"]="houses"
            st.rerun()

    st.markdown('<div class="cc-legend"><b>Legend</b><br>F = Favorable · U = Undecided · A = Against · NH = Not Home<br>YS = Yard Sign · FU = Follow Up Needed · ✉ = Mail Ballot Interest · V = Volunteer Interest<br>Envelope icon ✉ indicates mail ballot interest or permanent mail-ballot status where available.</div>', unsafe_allow_html=True)
    if st.button("← Back to Houses", key="back_houses"):
        set_page("houses")
    st.stop()


def cc_mobile_render_precinct_first_view(selected_assignment):
    rows = cc_mobile_assignment_precinct_rows(selected_assignment)
    if not rows:
        return False
    st.markdown("### Precincts")
    st.caption("Choose a precinct first, then choose a street.")
    for i, p in enumerate(rows):
        label = f"{p['precinct']} — {p['street_count']:,} streets · {p['household_count']:,} houses · {p['voter_count']:,} voters"
        if st.button(label, key=f"mobile_precinct_select_{i}_{p['precinct']}"):
            st.session_state["selected_mobile_precinct"] = p
            st.session_state["mobile_view_mode"] = "streets"
            st.rerun()
    return True


