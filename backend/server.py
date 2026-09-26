"""A Music backend — FastAPI + Supabase (Postgres + Storage).
Works the same locally and on Vercel serverless.

Local run:  set SUPABASE_URL + SUPABASE_SERVICE_KEY env vars (see .env.example),
            pip install -r requirements.txt,  python backend/server.py
Vercel:     rewrites /api/* to api/index.py, static files served by Vercel CDN.
"""
import mimetypes
import os
import secrets
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.supa import sb

BASE = Path(__file__).resolve().parent.parent  # project root (a/)

app = FastAPI(title="A Music", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ---------- helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()

def fmt_dur(s: int) -> str:
    return f"{s // 60:02d}:{s % 60:02d}"

def as_time(iso: str, fmt="%H:%M:%S") -> str:
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except Exception:
        return "-"

ROLE_PERMS = {
    "super_admin": ["*"],
    "admin": ["music", "effects", "themes", "users", "settings", "analytics"],
    "content_manager": ["music", "effects"],
    "analytics_viewer": ["analytics"],
}

def require_admin(x_admin_token: str = Header(default="")) -> dict:
    if not x_admin_token:
        raise HTTPException(401, "Missing X-Admin-Token")
    tok = sb().table("admin_tokens").select("*").eq("token", x_admin_token).execute().data
    if not tok:
        raise HTTPException(401, "Invalid token")
    tok = tok[0]
    if datetime.fromisoformat(tok["expires_at"]) < datetime.now(timezone.utc):
        sb().table("admin_tokens").delete().eq("token", x_admin_token).execute()
        raise HTTPException(401, "Token expired")
    adm = sb().table("admins").select("*").eq("admin_id", tok["admin_id"]).execute().data
    if not adm:
        raise HTTPException(401, "Admin not found")
    return adm[0]

def need_perm(admin: dict, perm: str):
    p = ROLE_PERMS.get(admin["role"], [])
    if "*" not in p and perm not in p:
        raise HTTPException(403, f"Role '{admin['role']}' cannot access '{perm}'")

def storage_upload(bucket: str, filename: str, data: bytes) -> str:
    safe = f"{uuid.uuid4().hex[:10]}-{filename.replace(' ', '_')}"
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    sb().storage.from_(bucket).upload(safe, data, {"content-type": ctype})
    url = sb().storage.from_(bucket).get_public_url(safe)
    return str(url)

# ---------- first-run seed (idempotent; runs on cold start) ----------
SEED_SONGS = [
    ("Midnight Drive", "Neon Coast", "Chill", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3", 1, "Chill, Night, Electronic"),
    ("Ocean Dreams", "Waveform", "Relax", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3", 1, "Relax, Ocean, Ambient"),
    ("Cyber World", "Pixel Raid", "Gaming", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3", 1, "Gaming, Synth, Energy"),
    ("Summer Beat", "Sun Parade", "Workout", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3", 0, "Workout, Pop, Energy"),
    ("Night Rider", "Turbo Fox", "Trending", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3", 1, "Trending, Drive, Bass"),
    ("Deep Focus", "Mono Mind", "Instrumental", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-6.mp3", 0, "Instrumental, Focus"),
]
SEED_FX = [("Cartoon Boing", "😂"), ("Frog Croak", "🐸"), ("Chicken Cluck", "🐔"),
           ("Explosion", "💥"), ("Car Horn", "🚗"), ("Trumpet Fail", "🎺"),
           ("Ghost Woo", "👻"), ("Robot Voice", "🤖"), ("Cat Meow", "🐱"),
           ("Bass Drop", "🔊"), ("Scream", "😱"), ("Wrong Answer", "🎺")]

def seed_if_empty():
    db = sb()
    for k, v in {"site_name": "A Music", "tagline": "Music • Customize • Play • Remix",
                 "chaos_enabled": "1", "recommendations_enabled": "1"}.items():
        if not db.table("settings").select("key").eq("key", k).execute().data:
            db.table("settings").insert({"key": k, "value": v}).execute()
    if not db.table("themes").select("theme_id").limit(1).execute().data:
        db.table("themes").insert({
            "theme_id": "theme-cyberpunk", "name": "Cyberpunk",
            "primary_color": "#6C4EFF", "secondary_color": "#00E5FF",
            "background": "dark", "button_style": "rounded", "font_family": "Inter",
            "animation": "neon", "player_style": "glass",
            "site_name": "A Music", "logo_url": "", "is_active": 1}).execute()
    if not db.table("songs").select("song_id").limit(1).execute().data:
        for t, a, cat, url, feat, tags in SEED_SONGS:
            db.table("songs").insert({
                "song_id": f"song-{uuid.uuid4().hex[:8]}", "title": t, "artist": a,
                "category": cat, "audio_url": url, "thumbnail_url": "",
                "description": f"{t} by {a}. Demo track.", "tags": tags,
                "featured": feat, "enabled": 1, "play_count": 0,
                "created_at": now_iso()}).execute()
    if not db.table("sound_effects").select("effect_id").limit(1).execute().data:
        for n, icon in SEED_FX:
            db.table("sound_effects").insert({
                "effect_id": f"fx-{uuid.uuid4().hex[:8]}", "name": n, "icon": icon,
                "category": "Funny", "audio_url": "", "trigger_mode": "random",
                "frequency_min": 30, "frequency_max": 120, "probability": 15,
                "enabled": 1, "use_count": 0}).execute()
    if not db.table("admins").select("admin_id").limit(1).execute().data:
        salt = secrets.token_hex(16)
        db.table("admins").insert({
            "admin_id": "albin", "name": "albin",
            "password_hash": hash_pw("evangely", salt), "salt": salt,
            "role": "super_admin", "created_at": now_iso()}).execute()
        print("Seeded default super admin albin — CHANGE ITS PASSWORD after first login.")

try:
    seed_if_empty()
except Exception as e:
    print(f"Seed skipped ({e}) — set SUPABASE_URL/SUPABASE_SERVICE_KEY and run schema.sql.")

# ---------- admin: setup & login ----------
@app.post("/api/admin/setup")
def setup(name: str = Form("Super Admin"), admin_id: str = Form(""), password: str = Form("")):
    db = sb()
    if db.table("admins").select("admin_id").limit(1).execute().data:
        raise HTTPException(400, "Setup already done — ask an existing admin to create your account.")
    aid = admin_id.strip() or f"BF-ADM-{secrets.token_hex(2).upper()}"
    pwd = password or secrets.token_urlsafe(12)
    salt = secrets.token_hex(16)
    db.table("admins").insert({
        "admin_id": aid, "name": name, "password_hash": hash_pw(pwd, salt),
        "salt": salt, "role": "super_admin", "created_at": now_iso()}).execute()
    return {"admin_id": aid, "password": pwd, "role": "super_admin",
            "message": "Super admin created. SAVE THE PASSWORD — it is never shown again."}

@app.post("/api/admin/login")
def login(payload: dict):
    db = sb()
    rows = db.table("admins").select("*").eq("admin_id", payload.get("admin_id", "").strip()).execute().data
    if not rows or hash_pw(payload.get("password", ""), rows[0]["salt"]) != rows[0]["password_hash"]:
        raise HTTPException(401, "Invalid Admin ID or password")
    row = rows[0]
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    db.table("admin_tokens").insert({"token": token, "admin_id": row["admin_id"], "expires_at": exp}).execute()
    return {"token": token, "admin_id": row["admin_id"], "name": row["name"], "role": row["role"]}

@app.post("/api/admin/logout")
def logout(x_admin_token: str = Header(default="")):
    sb().table("admin_tokens").delete().eq("token", x_admin_token).execute()
    return {"ok": True}

@app.put("/api/admin/password")
def change_password(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    if hash_pw(payload.get("current_password", ""), admin["salt"]) != admin["password_hash"]:
        raise HTTPException(401, "Current password is wrong")
    new = payload.get("new_password", "")
    if len(new) < 4:
        raise HTTPException(400, "New password too short (min 4 chars)")
    salt = secrets.token_hex(16)
    sb().table("admins").update(
        {"password_hash": hash_pw(new, salt), "salt": salt}).eq("admin_id", admin["admin_id"]).execute()
    return {"ok": True}

@app.post("/api/admin/create")
def create_admin(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "settings")
    name = payload.get("name", "Admin").strip()
    role = payload.get("role", "admin")
    if role not in ROLE_PERMS:
        raise HTTPException(400, "Invalid role")
    if role == "super_admin" and admin["role"] != "super_admin":
        raise HTTPException(403, "Only Super Admin can create another Super Admin")
    if admin["role"] in ("content_manager", "analytics_viewer"):
        raise HTTPException(403, "Your role cannot create admins")
    aid = f"BF-ADM-{secrets.token_hex(2).upper()}"
    pwd = secrets.token_urlsafe(10)
    salt = secrets.token_hex(16)
    sb().table("admins").insert({
        "admin_id": aid, "name": name, "password_hash": hash_pw(pwd, salt),
        "salt": salt, "role": role, "created_at": now_iso()}).execute()
    return {"admin_id": aid, "password": pwd, "role": role, "name": name}

@app.get("/api/admins")
def list_admins(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "settings")
    rows = sb().table("admins").select("admin_id,name,role,created_at").order("created_at").execute().data
    return rows

@app.delete("/api/admins/{aid}")
def delete_admin(aid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    if admin["role"] != "super_admin":
        raise HTTPException(403, "Only Super Admin can delete admins")
    if aid == admin["admin_id"]:
        raise HTTPException(400, "You cannot delete yourself")
    db = sb()
    db.table("admins").delete().eq("admin_id", aid).execute()
    db.table("admin_tokens").delete().eq("admin_id", aid).execute()
    return {"ok": True}

# ---------- songs ----------
@app.get("/api/songs")
def get_songs(category: str = "", search: str = "", featured: str = ""):
    db = sb()
    q = db.table("songs").select("*").eq("enabled", 1)
    if category:
        q = q.eq("category", category)
    if search:
        s = search.replace(",", " ").replace("%", "")
        q = q.or_(f"title.ilike.%{s}%,artist.ilike.%{s}%,tags.ilike.%{s}%")
    if featured == "1":
        q = q.eq("featured", 1)
    return q.order("featured", desc=True).order("play_count", desc=True).execute().data

@app.get("/api/songs/all")
def get_songs_all(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "music")
    return sb().table("songs").select("*").order("created_at", desc=True).execute().data

@app.post("/api/songs/upload")
async def upload_song(x_admin_token: str = Header(default=""), title: str = Form(...),
                     artist: str = Form("Unknown"), category: str = Form("Trending"),
                     description: str = Form(""), tags: str = Form(""),
                     featured: int = Form(0), audio_url: str = Form(""),
                     audio: UploadFile = File(None), thumbnail: UploadFile = File(None)):
    admin = require_admin(x_admin_token)
    need_perm(admin, "music")
    a_url = audio_url.strip()
    if audio is not None and audio.filename:
        a_url = storage_upload("music", audio.filename, await audio.read())
    if not a_url:
        raise HTTPException(400, "Provide an audio file or an audio URL")
    t_url = ""
    if thumbnail is not None and thumbnail.filename:
        t_url = storage_upload("images", thumbnail.filename, await thumbnail.read())
    sid = f"song-{uuid.uuid4().hex[:8]}"
    sb().table("songs").insert({
        "song_id": sid, "title": title, "artist": artist, "category": category,
        "audio_url": a_url, "thumbnail_url": t_url, "description": description,
        "tags": tags, "featured": int(featured), "enabled": 1, "play_count": 0,
        "created_at": now_iso()}).execute()
    return {"song_id": sid}

@app.put("/api/songs/{sid}")
def update_song(sid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "music")
    allowed = ("title", "artist", "category", "description", "tags", "featured",
               "enabled", "audio_url", "thumbnail_url")
    patch = {k: payload[k] for k in payload if k in allowed}
    if not patch:
        raise HTTPException(400, "Nothing to update")
    sb().table("songs").update(patch).eq("song_id", sid).execute()
    return {"ok": True}

@app.delete("/api/songs/{sid}")
def delete_song(sid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "music")
    sb().table("songs").delete().eq("song_id", sid).execute()
    return {"ok": True}

@app.post("/api/songs/{sid}/play")
def play_song(sid: str, payload: dict = None):
    db = sb()
    rows = db.table("songs").select("play_count").eq("song_id", sid).execute().data
    if rows:
        db.table("songs").update({"play_count": (rows[0]["play_count"] or 0) + 1}).eq("song_id", sid).execute()
    if payload and payload.get("session_id"):
        srows = db.table("sessions").select("songs_played").eq("session_id", payload["session_id"]).execute().data
        if srows:
            db.table("sessions").update({
                "songs_played": (srows[0]["songs_played"] or 0) + 1,
                "current_song": sid, "last_activity": now_iso()
            }).eq("session_id", payload["session_id"]).execute()
        db.table("play_history").insert({
            "id": uuid.uuid4().hex, "session_id": payload["session_id"],
            "song_id": sid, "started_at": now_iso()}).execute()
    return {"ok": True}

@app.get("/api/categories")
def categories():
    rows = sb().table("songs").select("category").eq("enabled", 1).execute().data
    base = ["Trending", "Chill", "Workout", "Relax", "Gaming", "Funny Sounds", "Instrumental", "Featured"]
    found = list(dict.fromkeys([r["category"] for r in rows if r.get("category")]))
    return base + [c for c in found if c not in base]

# ---------- effects ----------
@app.get("/api/effects")
def get_effects():
    return sb().table("sound_effects").select("*").eq("enabled", 1).execute().data

@app.get("/api/effects/all")
def get_effects_all(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "effects")
    return sb().table("sound_effects").select("*").execute().data

@app.post("/api/effects")
async def create_effect(x_admin_token: str = Header(default=""), name: str = Form(...),
                       icon: str = Form("😂"), category: str = Form("Funny"),
                       trigger_mode: str = Form("random"), frequency_min: int = Form(30),
                       frequency_max: int = Form(120), probability: int = Form(15),
                       audio: UploadFile = File(None)):
    admin = require_admin(x_admin_token)
    need_perm(admin, "effects")
    a_url = ""
    if audio is not None and audio.filename:
        a_url = storage_upload("effects", audio.filename, await audio.read())
    eid = f"fx-{uuid.uuid4().hex[:8]}"
    sb().table("sound_effects").insert({
        "effect_id": eid, "name": name, "icon": icon, "category": category,
        "audio_url": a_url, "trigger_mode": trigger_mode,
        "frequency_min": frequency_min, "frequency_max": frequency_max,
        "probability": probability, "enabled": 1, "use_count": 0}).execute()
    return {"effect_id": eid}

@app.put("/api/effects/{eid}")
def update_effect(eid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "effects")
    allowed = ("name", "icon", "category", "trigger_mode", "frequency_min",
               "frequency_max", "probability", "enabled", "audio_url")
    patch = {k: payload[k] for k in payload if k in allowed}
    if not patch:
        raise HTTPException(400, "Nothing to update")
    sb().table("sound_effects").update(patch).eq("effect_id", eid).execute()
    return {"ok": True}

@app.delete("/api/effects/{eid}")
def delete_effect(eid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "effects")
    sb().table("sound_effects").delete().eq("effect_id", eid).execute()
    return {"ok": True}

@app.post("/api/effects/{eid}/use")
def use_effect(eid: str, payload: dict = None):
    db = sb()
    rows = db.table("sound_effects").select("use_count").eq("effect_id", eid).execute().data
    if rows:
        db.table("sound_effects").update({"use_count": (rows[0]["use_count"] or 0) + 1}).eq("effect_id", eid).execute()
    if payload and payload.get("session_id"):
        srows = db.table("sessions").select("effects_used").eq("session_id", payload["session_id"]).execute().data
        if srows:
            db.table("sessions").update(
                {"effects_used": (srows[0]["effects_used"] or 0) + 1}).eq("session_id", payload["session_id"]).execute()
    return {"ok": True}

# ---------- themes & settings ----------
@app.get("/api/themes/active")
def active_theme():
    db = sb()
    rows = db.table("themes").select("*").eq("is_active", 1).limit(1).execute().data
    if not rows:
        rows = db.table("themes").select("*").limit(1).execute().data
    t = dict(rows[0]) if rows else {}
    t["settings"] = {r["key"]: r["value"] for r in db.table("settings").select("*").execute().data}
    return t

@app.get("/api/themes")
def list_themes():
    return sb().table("themes").select("*").execute().data

@app.post("/api/themes")
def create_theme(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "themes")
    tid = f"theme-{uuid.uuid4().hex[:8]}"
    sb().table("themes").insert({
        "theme_id": tid, "name": payload.get("name", "Custom"),
        "primary_color": payload.get("primary_color", "#6C4EFF"),
        "secondary_color": payload.get("secondary_color", "#00E5FF"),
        "background": payload.get("background", "dark"),
        "button_style": payload.get("button_style", "rounded"),
        "font_family": payload.get("font_family", "Inter"),
        "animation": payload.get("animation", "neon"),
        "player_style": payload.get("player_style", "glass"),
        "site_name": payload.get("site_name", "A Music"),
        "logo_url": payload.get("logo_url", ""), "is_active": 0}).execute()
    return {"theme_id": tid}

@app.put("/api/themes/{tid}")
def update_theme(tid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "themes")
    db = sb()
    allowed = ("name", "primary_color", "secondary_color", "background", "button_style",
               "font_family", "animation", "player_style", "site_name", "logo_url")
    patch = {k: payload[k] for k in payload if k in allowed}
    if patch:
        db.table("themes").update(patch).eq("theme_id", tid).execute()
    if payload.get("is_active"):
        for r in db.table("themes").select("theme_id").execute().data:
            db.table("themes").update({"is_active": 0}).eq("theme_id", r["theme_id"]).execute()
        db.table("themes").update({"is_active": 1}).eq("theme_id", tid).execute()
        if payload.get("site_name"):
            if db.table("settings").select("key").eq("key", "site_name").execute().data:
                db.table("settings").update({"value": payload["site_name"]}).eq("key", "site_name").execute()
            else:
                db.table("settings").insert({"key": "site_name", "value": payload["site_name"]}).execute()
    return {"ok": True}

@app.delete("/api/themes/{tid}")
def delete_theme(tid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "themes")
    sb().table("themes").delete().eq("theme_id", tid).eq("is_active", 0).execute()
    return {"ok": True}

@app.get("/api/settings")
def get_settings():
    return {r["key"]: r["value"] for r in sb().table("settings").select("*").execute().data}

@app.put("/api/settings")
def put_settings(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "settings")
    db = sb()
    for k, v in payload.items():
        if db.table("settings").select("key").eq("key", k).execute().data:
            db.table("settings").update({"value": str(v)}).eq("key", k).execute()
        else:
            db.table("settings").insert({"key": k, "value": str(v)}).execute()
    return {"ok": True}

# ---------- sessions (guest, no login) + DB-backed live presence ----------
@app.post("/api/sessions/join")
def join(payload: dict):
    guest = payload.get("guest_id") or f"Guest-{secrets.randbelow(9000) + 1000}"
    sid = f"sess-{uuid.uuid4().hex[:10]}"
    sb().table("sessions").insert({
        "session_id": sid, "guest_id": guest, "entry_time": now_iso(),
        "last_activity": now_iso(), "theme": payload.get("theme", "")}).execute()
    return {"session_id": sid, "guest_id": guest}

@app.post("/api/sessions/heartbeat")
def heartbeat(payload: dict):
    sid = payload.get("session_id", "")
    patch = {"last_activity": now_iso()}
    if payload.get("current_song"):
        patch["current_song"] = payload["current_song"]
    if payload.get("theme") is not None:
        patch["theme"] = payload["theme"]
    sb().table("sessions").update(patch).eq("session_id", sid).execute()
    return {"ok": True}

@app.post("/api/sessions/leave")
def leave(payload: dict):
    sid = payload.get("session_id", "")
    db = sb()
    rows = db.table("sessions").select("entry_time").eq("session_id", sid).execute().data
    if rows and rows[0].get("entry_time"):
        try:
            dur = int((datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["entry_time"])).total_seconds())
        except Exception:
            dur = 0
        db.table("sessions").update({"exit_time": now_iso(), "duration_sec": dur}).eq("session_id", sid).execute()
    return {"ok": True}

@app.get("/api/live")
def live(x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    rows = sb().table("sessions").select("*").gte("last_activity", cutoff).execute().data
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r.get("exit_time"):
            continue
        try:
            entry = datetime.fromisoformat(r["entry_time"])
            dur = fmt_dur(max(0, int((now - entry).total_seconds())))
            entered = as_time(r["entry_time"])
        except Exception:
            dur, entered = "00:00", "-"
        out.append({"guest_id": r["guest_id"], "session_id": r["session_id"],
                    "entered": entered, "current_song": r.get("current_song") or "-",
                    "duration": dur})
    return {"count": len(out), "users": out}

# ---------- analytics ----------
@app.get("/api/analytics/overview")
def analytics_overview(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "analytics")
    db = sb()
    today = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"
    visitors = db.table("sessions").select("session_id", count="exact").gte("entry_time", today).execute().count or 0
    plays = sum((r.get("play_count") or 0) for r in db.table("songs").select("play_count").execute().data)
    total_time = sum((r.get("duration_sec") or 0) for r in db.table("sessions").select("duration_sec").execute().data)
    top = db.table("songs").select("title,play_count").order("play_count", desc=True).limit(1).execute().data
    fx = sum((r.get("use_count") or 0) for r in db.table("sound_effects").select("use_count").execute().data)
    hours = [0] * 24
    for r in db.table("sessions").select("entry_time").gte("entry_time", today).execute().data:
        try:
            hours[datetime.fromisoformat(r["entry_time"]).hour] += 1
        except Exception:
            pass
    hourly = [{"h": f"{h:02d}", "n": n} for h, n in enumerate(hours) if n > 0]
    top_songs = db.table("songs").select("title,artist,play_count").order("play_count", desc=True).limit(5).execute().data
    return {"visitors_today": visitors, "songs_played": plays, "total_time_sec": total_time,
            "popular_song": top[0] if top else {}, "effects_used": fx,
            "hourly": hourly, "top_songs": top_songs}

@app.get("/api/analytics/songs/{sid}")
def song_analytics(sid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    need_perm(admin, "analytics")
    db = sb()
    song = db.table("songs").select("*").eq("song_id", sid).execute().data
    if not song:
        raise HTTPException(404, "Song not found")
    hist = db.table("play_history").select("*").eq("song_id", sid).execute().data
    uniq = len({h["session_id"] for h in hist if h.get("session_id")})
    avg = int(sum(h.get("duration_sec") or 0 for h in hist) / len(hist)) if hist else 0
    comp = sum(1 for h in hist if h.get("completed"))
    skip = sum(1 for h in hist if h.get("skipped"))
    return {"song": song[0], "unique_sessions": uniq, "avg_listen_sec": avg,
            "completions": comp, "skips": skip}

# ---------- session-based recommendations (no account) ----------
@app.get("/api/recommend")
def recommend(session_id: str = ""):
    db = sb()
    cats, played = [], []
    if session_id:
        hist = db.table("play_history").select("song_id").eq("session_id", session_id).execute().data
        played = [h["song_id"] for h in hist if h.get("song_id")]
        if played:
            cat_count: dict = {}
            for s in db.table("songs").select("song_id,category").execute().data:
                if s["song_id"] in played and s.get("category"):
                    cat_count[s["category"]] = cat_count.get(s["category"], 0) + 1
            cats = sorted(cat_count, key=cat_count.get, reverse=True)[:2]
    if cats:
        recs = db.table("songs").select("*").eq("enabled", 1).in_("category", cats).order(
            "play_count", desc=True).limit(6).execute().data
        recs = [r for r in recs if r["song_id"] not in played][:3]
    else:
        recs = db.table("songs").select("*").eq("enabled", 1).order("play_count", desc=True).limit(3).execute().data
    note = f"You seem to prefer {', '.join(cats)}. " if cats else "Trending picks for new listeners. "
    return {"picks": recs, "note": note + "No account needed — based only on this session."}

# ---------- local static frontend (Vercel serves these itself; local uvicorn needs them) ----------
for _mp, _dd in (("/assets", "assets"), ("/css", "css"), ("/js", "js")):
    _dir = BASE / _dd
    if _dir.exists():
        app.mount(_mp, StaticFiles(directory=str(_dir)), name=_dd)

@app.get("/admin/{path:path}")
def admin_pages(path: str):
    f = BASE / "admin" / (path or "login.html")
    if path == "" or path.endswith("/"):
        f = BASE / "admin" / "dashboard.html"
    if f.suffix == "":
        f = f.with_suffix(".html")
    if f.exists() and f.is_file():
        return FileResponse(str(f))
    return FileResponse(str(BASE / "admin" / "login.html"))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "2.0.0-supabase"}

@app.get("/{path:path}")
def pages(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    f = BASE / f"{path or 'index.html'}"
    if path == "":
        f = BASE / "index.html"
    if f.exists() and f.is_file():
        return FileResponse(str(f))
    idx = BASE / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({"message": "A Music API — frontend served by Vercel"})

if __name__ == "__main__":
    import uvicorn
    print("\n  A Music -> http://localhost:8000  (Supabase-powered)")
    print("  Needs SUPABASE_URL + SUPABASE_SERVICE_KEY env vars\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
