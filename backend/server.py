"""A Music backend — FastAPI + SQLite + WebSocket live users.
Run:  pip install -r requirements.txt
      python server.py
Serves the whole site at http://localhost:8000
"""
import os, sqlite3, uuid, hashlib, secrets, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

BASE = Path(__file__).resolve().parent.parent  # beatforge/
DB_PATH = BASE / "backend" / "beatforge.db"
MUSIC_DIR = BASE / "assets" / "music"
EFFECTS_DIR = BASE / "assets" / "effects"
IMAGES_DIR = BASE / "assets" / "images"
for d in (MUSIC_DIR, EFFECTS_DIR, IMAGES_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="A Music", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ---------- DB ----------
def db():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    c = con.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS admins(
      admin_id TEXT PRIMARY KEY, name TEXT, password_hash TEXT, salt TEXT,
      role TEXT DEFAULT 'admin', created_at TEXT);
    CREATE TABLE IF NOT EXISTS admin_tokens(
      token TEXT PRIMARY KEY, admin_id TEXT, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS songs(
      song_id TEXT PRIMARY KEY, title TEXT, artist TEXT, category TEXT,
      audio_url TEXT, thumbnail_url TEXT, description TEXT, tags TEXT,
      featured INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
      play_count INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS sound_effects(
      effect_id TEXT PRIMARY KEY, name TEXT, icon TEXT, category TEXT,
      audio_url TEXT, trigger_mode TEXT DEFAULT 'random',
      frequency_min INTEGER DEFAULT 30, frequency_max INTEGER DEFAULT 120,
      probability INTEGER DEFAULT 15, enabled INTEGER DEFAULT 1, use_count INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS themes(
      theme_id TEXT PRIMARY KEY, name TEXT, primary_color TEXT, secondary_color TEXT,
      background TEXT, button_style TEXT, font_family TEXT, animation TEXT,
      player_style TEXT, site_name TEXT, logo_url TEXT, is_active INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      session_id TEXT PRIMARY KEY, guest_id TEXT, entry_time TEXT, last_activity TEXT,
      exit_time TEXT, duration_sec INTEGER DEFAULT 0, current_song TEXT,
      songs_played INTEGER DEFAULT 0, effects_used INTEGER DEFAULT 0, theme TEXT);
    CREATE TABLE IF NOT EXISTS play_history(
      id TEXT PRIMARY KEY, session_id TEXT, song_id TEXT,
      started_at TEXT, stopped_at TEXT, duration_sec INTEGER DEFAULT 0,
      completed INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0);
    """)
    # default settings
    defaults = {"site_name": "A MUSIC", "tagline": "Music • Customize • Play • Remix",
                "chaos_enabled": "1", "recommendations_enabled": "1"}
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    # default theme
    c.execute("SELECT COUNT(*) n FROM themes")
    if c.fetchone()["n"] == 0:
        c.execute("""INSERT INTO themes(theme_id,name,primary_color,secondary_color,background,
          button_style,font_family,animation,player_style,site_name,is_active)
          VALUES('theme-cyberpunk','Cyberpunk','#6C4EFF','#00E5FF','dark','rounded','Inter','neon','glass','A MUSIC',1)""")
    con.commit()
    # seed songs (public sample mp3s so the demo plays instantly)
    c.execute("SELECT COUNT(*) n FROM songs")
    if c.fetchone()["n"] == 0:
        seed = [
            ("Midnight Drive", "Neon Coast", "Chill", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3", 1, "Chill, Night, Electronic"),
            ("Ocean Dreams", "Waveform", "Relax", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3", 1, "Relax, Ocean, Ambient"),
            ("Cyber World", "Pixel Raid", "Gaming", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3", 1, "Gaming, Synth, Energy"),
            ("Summer Beat", "Sun Parade", "Workout", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3", 0, "Workout, Pop, Energy"),
            ("Night Rider", "Turbo Fox", "Trending", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3", 1, "Trending, Drive, Bass"),
            ("Deep Focus", "Mono Mind", "Instrumental", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-6.mp3", 0, "Instrumental, Focus"),
        ]
        for t, a, cat, url, feat, tags in seed:
            c.execute("""INSERT INTO songs(song_id,title,artist,category,audio_url,thumbnail_url,
              description,tags,featured,enabled,play_count,created_at)
              VALUES(?,?,?,?,?,'',?,?,?,1,0,?)""",
              (f"song-{uuid.uuid4().hex[:8]}", t, a, cat, url, feat, f"{t} by {a}. Demo track.", tags,
               datetime.now(timezone.utc).isoformat()))
    # seed funny effects (synthesized client-side; audio_url empty = synth)
    c.execute("SELECT COUNT(*) n FROM sound_effects")
    if c.fetchone()["n"] == 0:
        fx = [("Cartoon Boing", "😂", "Funny"), ("Frog Croak", "🐸", "Funny"),
              ("Chicken Cluck", "🐔", "Funny"), ("Explosion", "💥", "Funny"),
              ("Car Horn", "🚗", "Funny"), ("Trumpet Fail", "🎺", "Funny"),
              ("Ghost Woo", "👻", "Funny"), ("Robot Voice", "🤖", "Funny"),
              ("Cat Meow", "🐱", "Funny"), ("Bass Drop", "🔊", "Funny"),
              ("Scream", "😱", "Funny"), ("Wrong Answer", "🎺", "Funny")]
        for n, icon, cat in fx:
            c.execute("""INSERT INTO sound_effects(effect_id,name,icon,category,audio_url,
              trigger_mode,frequency_min,frequency_max,probability,enabled,use_count)
              VALUES(?,?,?,?,?,'random',30,120,15,1,0)""",
              (f"fx-{uuid.uuid4().hex[:8]}", n, icon, cat, ""))
    con.commit(); con.close()

init_db()

# ---------- auth helpers ----------
def hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()

def require_admin(x_admin_token: str = Header(default="")):
    if not x_admin_token:
        raise HTTPException(401, "Missing X-Admin-Token")
    con = db(); c = con.cursor()
    c.execute("SELECT * FROM admin_tokens WHERE token=?", (x_admin_token,))
    row = c.fetchone()
    if not row:
        con.close(); raise HTTPException(401, "Invalid token")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        c.execute("DELETE FROM admin_tokens WHERE token=?", (x_admin_token,))
        con.commit(); con.close(); raise HTTPException(401, "Token expired")
    c.execute("SELECT * FROM admins WHERE admin_id=?", (row["admin_id"],))
    admin = dict(c.fetchone()); con.close()
    return admin

def need_roles(admin, *roles):
    if admin["role"] not in roles:
        raise HTTPException(403, f"Requires role: {'/'.join(roles)} (you are {admin['role']})")

ROLE_PERMS = {
    "super_admin": ["*"],
    "admin": ["music", "effects", "themes", "users", "settings", "analytics"],
    "content_manager": ["music", "effects"],
    "analytics_viewer": ["analytics"],
}
def can(admin, perm):
    p = ROLE_PERMS.get(admin["role"], [])
    return "*" in p or perm in p
def need_perm(admin, perm):
    if not can(admin, perm):
        raise HTTPException(403, f"Role '{admin['role']}' cannot access '{perm}'")

# ---------- admin: setup & login ----------
@app.post("/api/admin/setup")
def setup(name: str = Form("Super Admin"), admin_id: str = Form(""), password: str = Form("")):
    con = db(); c = con.cursor()
    c.execute("SELECT COUNT(*) n FROM admins")
    if c.fetchone()["n"] > 0:
        con.close(); raise HTTPException(400, "Setup already done — ask an existing admin to create your account.")
    aid = admin_id.strip() or f"BF-ADM-{secrets.token_hex(2).upper()}"
    pwd = password or secrets.token_urlsafe(12)
    salt = secrets.token_hex(16)
    c.execute("INSERT INTO admins VALUES(?,?,?,?,?,?)",
              (aid, name, hash_pw(pwd, salt), salt, "super_admin", datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    return {"admin_id": aid, "password": pwd, "role": "super_admin",
            "message": "Super admin created. SAVE THE PASSWORD — it is never shown again."}

@app.post("/api/admin/login")
def login(payload: dict):
    con = db(); c = con.cursor()
    c.execute("SELECT * FROM admins WHERE admin_id=?", (payload.get("admin_id", "").strip(),))
    row = c.fetchone()
    if not row or hash_pw(payload.get("password", ""), row["salt"]) != row["password_hash"]:
        con.close(); raise HTTPException(401, "Invalid Admin ID or password")
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    c.execute("INSERT INTO admin_tokens VALUES(?,?,?)", (token, row["admin_id"], exp))
    con.commit(); con.close()
    return {"token": token, "admin_id": row["admin_id"], "name": row["name"], "role": row["role"]}

@app.get("/api/admin/me")
def me(admin=Header(default="")):
    return require_admin(admin)

@app.post("/api/admin/logout")
def logout(x_admin_token: str = Header(default="")):
    con = db(); con.execute("DELETE FROM admin_tokens WHERE token=?", (x_admin_token,)); con.commit(); con.close()
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
    con = db()
    con.execute("INSERT INTO admins VALUES(?,?,?,?,?,?)",
                (aid, name, hash_pw(pwd, salt), salt, role, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    return {"admin_id": aid, "password": pwd, "role": role, "name": name}

@app.get("/api/admins")
def list_admins(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "settings")
    con = db()
    rows = con.execute("SELECT admin_id,name,role,created_at FROM admins ORDER BY created_at").fetchall()
    con.close()
    return [dict(r) for r in rows]

@app.delete("/api/admins/{aid}")
def delete_admin(aid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token)
    if admin["role"] != "super_admin":
        raise HTTPException(403, "Only Super Admin can delete admins")
    if aid == admin["admin_id"]:
        raise HTTPException(400, "You cannot delete yourself")
    con = db(); con.execute("DELETE FROM admins WHERE admin_id=?", (aid,))
    con.execute("DELETE FROM admin_tokens WHERE admin_id=?", (aid,))
    con.commit(); con.close()
    return {"ok": True}

# ---------- songs ----------
@app.get("/api/songs")
def get_songs(category: str = "", search: str = "", featured: str = ""):
    con = db()
    q = "SELECT * FROM songs WHERE enabled=1"
    args = []
    if category: q += " AND category=?"; args.append(category)
    if search:
        q += " AND (title LIKE ? OR artist LIKE ? OR tags LIKE ?)"; args += [f"%{search}%"]*3
    if featured == "1": q += " AND featured=1"
    q += " ORDER BY featured DESC, play_count DESC"
    rows = con.execute(q, args).fetchall(); con.close()
    return [dict(r) for r in rows]

@app.get("/api/songs/all")
def get_songs_all(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "music")
    con = db(); rows = con.execute("SELECT * FROM songs ORDER BY created_at DESC").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.post("/api/songs/upload")
async def upload_song(x_admin_token: str = Header(default=""), title: str = Form(...),
                     artist: str = Form("Unknown"), category: str = Form("Trending"),
                     description: str = Form(""), tags: str = Form(""),
                     featured: int = Form(0), audio_url: str = Form(""),
                     audio: UploadFile = File(None), thumbnail: UploadFile = File(None)):
    admin = require_admin(x_admin_token); need_perm(admin, "music")
    a_url = audio_url.strip()
    if audio is not None and audio.filename:
        fn = f"{uuid.uuid4().hex[:10]}-{audio.filename.replace(' ','_')}"
        dest = MUSIC_DIR / fn
        dest.write_bytes(await audio.read())
        a_url = f"/assets/music/{fn}"
    if not a_url:
        raise HTTPException(400, "Provide an audio file or an audio URL")
    t_url = ""
    if thumbnail is not None and thumbnail.filename:
        fn = f"{uuid.uuid4().hex[:10]}-{thumbnail.filename.replace(' ','_')}"
        (IMAGES_DIR / fn).write_bytes(await thumbnail.read())
        t_url = f"/assets/images/{fn}"
    sid = f"song-{uuid.uuid4().hex[:8]}"
    con = db()
    con.execute("""INSERT INTO songs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, title, artist, category, a_url, t_url, description, tags,
                 int(featured), 1, 0, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    return {"song_id": sid}

@app.put("/api/songs/{sid}")
def update_song(sid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "music")
    allowed = ("title", "artist", "category", "description", "tags", "featured", "enabled", "audio_url", "thumbnail_url")
    sets = [f"{k}=?" for k in payload if k in allowed]
    if not sets: raise HTTPException(400, "Nothing to update")
    con = db()
    con.execute(f"UPDATE songs SET {','.join(sets)} WHERE song_id=?",
                [payload[k] for k in payload if k in allowed] + [sid])
    con.commit(); con.close()
    return {"ok": True}

@app.delete("/api/songs/{sid}")
def delete_song(sid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "music")
    con = db(); con.execute("DELETE FROM songs WHERE song_id=?", (sid,)); con.commit(); con.close()
    return {"ok": True}

@app.post("/api/songs/{sid}/play")
def play_song(sid: str, payload: dict = None):
    con = db()
    con.execute("UPDATE songs SET play_count=play_count+1 WHERE song_id=?", (sid,))
    if payload and payload.get("session_id"):
        sidn = payload["session_id"]
        con.execute("UPDATE sessions SET songs_played=songs_played+1, current_song=?, last_activity=? WHERE session_id=?",
                    (sid, datetime.now(timezone.utc).isoformat(), sidn))
        con.execute("INSERT INTO play_history(id,session_id,song_id,started_at) VALUES(?,?,?,?)",
                    (uuid.uuid4().hex, sidn, sid, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    return {"ok": True}

@app.get("/api/categories")
def categories():
    con = db()
    rows = con.execute("SELECT DISTINCT category FROM songs WHERE enabled=1").fetchall(); con.close()
    base = ["Trending", "Chill", "Workout", "Relax", "Gaming", "Funny Sounds", "Instrumental", "Featured"]
    found = [r["category"] for r in rows]
    return base + [c for c in found if c not in base]

# ---------- effects ----------
@app.get("/api/effects")
def get_effects():
    con = db(); rows = con.execute("SELECT * FROM sound_effects WHERE enabled=1").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.get("/api/effects/all")
def get_effects_all(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "effects")
    con = db(); rows = con.execute("SELECT * FROM sound_effects").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.post("/api/effects")
async def create_effect(x_admin_token: str = Header(default=""), name: str = Form(...),
                       icon: str = Form("😂"), category: str = Form("Funny"),
                       trigger_mode: str = Form("random"), frequency_min: int = Form(30),
                       frequency_max: int = Form(120), probability: int = Form(15),
                       audio: UploadFile = File(None)):
    admin = require_admin(x_admin_token); need_perm(admin, "effects")
    a_url = ""
    if audio is not None and audio.filename:
        fn = f"{uuid.uuid4().hex[:10]}-{audio.filename.replace(' ','_')}"
        (EFFECTS_DIR / fn).write_bytes(await audio.read())
        a_url = f"/assets/effects/{fn}"
    eid = f"fx-{uuid.uuid4().hex[:8]}"
    con = db()
    con.execute("INSERT INTO sound_effects VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (eid, name, icon, category, a_url, trigger_mode, frequency_min, frequency_max,
                 probability, 1, 0))
    con.commit(); con.close()
    return {"effect_id": eid}

@app.put("/api/effects/{eid}")
def update_effect(eid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "effects")
    allowed = ("name", "icon", "category", "trigger_mode", "frequency_min", "frequency_max",
               "probability", "enabled", "audio_url")
    sets = [f"{k}=?" for k in payload if k in allowed]
    if not sets: raise HTTPException(400, "Nothing to update")
    con = db()
    con.execute(f"UPDATE sound_effects SET {','.join(sets)} WHERE effect_id=?",
                [payload[k] for k in payload if k in allowed] + [eid])
    con.commit(); con.close()
    return {"ok": True}

@app.delete("/api/effects/{eid}")
def delete_effect(eid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "effects")
    con = db(); con.execute("DELETE FROM sound_effects WHERE effect_id=?", (eid,)); con.commit(); con.close()
    return {"ok": True}

@app.post("/api/effects/{eid}/use")
def use_effect(eid: str, payload: dict = None):
    con = db()
    con.execute("UPDATE sound_effects SET use_count=use_count+1 WHERE effect_id=?", (eid,))
    if payload and payload.get("session_id"):
        con.execute("UPDATE sessions SET effects_used=effects_used+1 WHERE session_id=?", (payload["session_id"],))
    con.commit(); con.close()
    return {"ok": True}

# ---------- themes & settings ----------
@app.get("/api/themes/active")
def active_theme():
    con = db()
    row = con.execute("SELECT * FROM themes WHERE is_active=1").fetchone()
    if not row: row = con.execute("SELECT * FROM themes LIMIT 1").fetchone()
    s = con.execute("SELECT * FROM settings").fetchall(); con.close()
    t = dict(row) if row else {}
    t["settings"] = {r["key"]: r["value"] for r in s}
    return t

@app.get("/api/themes")
def list_themes():
    con = db(); rows = con.execute("SELECT * FROM themes").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.post("/api/themes")
def create_theme(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "themes")
    tid = f"theme-{uuid.uuid4().hex[:8]}"
    con = db()
    con.execute("""INSERT INTO themes(theme_id,name,primary_color,secondary_color,background,
      button_style,font_family,animation,player_style,site_name,logo_url,is_active)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""",
      (tid, payload.get("name", "Custom"), payload.get("primary_color", "#6C4EFF"),
       payload.get("secondary_color", "#00E5FF"), payload.get("background", "dark"),
       payload.get("button_style", "rounded"), payload.get("font_family", "Inter"),
       payload.get("animation", "neon"), payload.get("player_style", "glass"),
       payload.get("site_name", "A MUSIC"), payload.get("logo_url", "")))
    con.commit(); con.close()
    return {"theme_id": tid}

@app.put("/api/themes/{tid}")
def update_theme(tid: str, payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "themes")
    allowed = ("name", "primary_color", "secondary_color", "background", "button_style",
               "font_family", "animation", "player_style", "site_name", "logo_url")
    sets = [f"{k}=?" for k in payload if k in allowed]
    con = db()
    if sets:
        con.execute(f"UPDATE themes SET {','.join(sets)} WHERE theme_id=?",
                    [payload[k] for k in payload if k in allowed] + [tid])
    if payload.get("is_active"):
        con.execute("UPDATE themes SET is_active=0")
        con.execute("UPDATE themes SET is_active=1 WHERE theme_id=?", (tid,))
        if payload.get("site_name"):
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('site_name',?)",
                        (payload["site_name"],))
    con.commit(); con.close()
    return {"ok": True}

@app.delete("/api/themes/{tid}")
def delete_theme(tid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "themes")
    con = db(); con.execute("DELETE FROM themes WHERE theme_id=? AND is_active=0", (tid,))
    con.commit(); con.close()
    return {"ok": True}

@app.get("/api/settings")
def get_settings():
    con = db(); rows = con.execute("SELECT * FROM settings").fetchall(); con.close()
    return {r["key"]: r["value"] for r in rows}

@app.put("/api/settings")
def put_settings(payload: dict, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "settings")
    con = db()
    for k, v in payload.items():
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(v)))
    con.commit(); con.close()
    return {"ok": True}

# ---------- sessions (guest, no login) ----------
@app.post("/api/sessions/join")
def join(payload: dict):
    guest = payload.get("guest_id") or f"Guest-{secrets.randbelow(9000)+1000}"
    sid = f"sess-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    con = db()
    con.execute("INSERT INTO sessions(session_id,guest_id,entry_time,last_activity,theme) VALUES(?,?,?,?,?)",
                (sid, guest, now, now, payload.get("theme", "")))
    con.commit(); con.close()
    LIVE[guest] = {"session_id": sid, "guest_id": guest, "entry": time.time(), "song": "-", "last": time.time()}
    return {"session_id": sid, "guest_id": guest}

@app.post("/api/sessions/heartbeat")
def heartbeat(payload: dict):
    sid = payload.get("session_id", "")
    now = datetime.now(timezone.utc).isoformat()
    con = db()
    con.execute("""UPDATE sessions SET last_activity=?, current_song=COALESCE(?,current_song),
      theme=COALESCE(?,theme) WHERE session_id=?""",
      (now, payload.get("current_song"), payload.get("theme"), sid))
    con.commit()
    row = con.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
    con.close()
    if row and row["guest_id"] in LIVE:
        LIVE[row["guest_id"]].update(last=time.time(), song=payload.get("current_song") or LIVE[row["guest_id"]]["song"])
    return {"ok": True}

@app.post("/api/sessions/leave")
def leave(payload: dict):
    sid = payload.get("session_id", "")
    now = datetime.now(timezone.utc)
    con = db()
    row = con.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
    if row:
        entry = datetime.fromisoformat(row["entry_time"])
        dur = int((now - entry).total_seconds())
        con.execute("UPDATE sessions SET exit_time=?, duration_sec=? WHERE session_id=?",
                    (now.isoformat(), dur, sid))
        con.commit()
        LIVE.pop(row["guest_id"], None)
    con.close()
    return {"ok": True}

@app.get("/api/live")
def live(x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    now = time.time()
    out = []
    for g, v in list(LIVE.items()):
        if now - v["last"] > 90:  # stale
            LIVE.pop(g, None); continue
        out.append({"guest_id": g, "session_id": v["session_id"],
                    "entered": datetime.fromtimestamp(v["entry"]).strftime("%H:%M:%S"),
                    "current_song": v["song"], "duration": fmt_dur(int(now - v["entry"]))})
    return {"count": len(out), "users": out}

def fmt_dur(s):
    return f"{s//60:02d}:{s%60:02d}"

# ---------- analytics ----------
@app.get("/api/analytics/overview")
def analytics_overview(x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "analytics")
    con = db()
    today = datetime.now(timezone.utc).date().isoformat()
    visitors = con.execute("SELECT COUNT(*) n FROM sessions WHERE date(entry_time)=?", (today,)).fetchone()["n"]
    plays = con.execute("SELECT COALESCE(SUM(play_count),0) s FROM songs").fetchone()["s"]
    total_time = con.execute("SELECT COALESCE(SUM(duration_sec),0) s FROM sessions").fetchone()["s"]
    top = con.execute("SELECT title,play_count FROM songs ORDER BY play_count DESC LIMIT 1").fetchone()
    fx = con.execute("SELECT COALESCE(SUM(use_count),0) s FROM sound_effects").fetchone()["s"]
    hourly = con.execute("""SELECT strftime('%H',entry_time) h, COUNT(*) n FROM sessions
      WHERE date(entry_time)=? GROUP BY h ORDER BY h""", (today,)).fetchall()
    top_songs = con.execute("SELECT title,artist,play_count FROM songs ORDER BY play_count DESC LIMIT 5").fetchall()
    con.close()
    return {"visitors_today": visitors, "songs_played": plays, "total_time_sec": total_time,
            "popular_song": dict(top) if top else {}, "effects_used": fx,
            "hourly": [dict(r) for r in hourly],
            "top_songs": [dict(r) for r in top_songs]}

@app.get("/api/analytics/songs/{sid}")
def song_analytics(sid: str, x_admin_token: str = Header(default="")):
    admin = require_admin(x_admin_token); need_perm(admin, "analytics")
    con = db()
    song = con.execute("SELECT * FROM songs WHERE song_id=?", (sid,)).fetchone()
    uniq = con.execute("SELECT COUNT(DISTINCT session_id) n FROM play_history WHERE song_id=?", (sid,)).fetchone()["n"]
    avg = con.execute("SELECT COALESCE(AVG(duration_sec),0) a FROM play_history WHERE song_id=?", (sid,)).fetchone()["a"]
    comp = con.execute("SELECT COUNT(*) n FROM play_history WHERE song_id=? AND completed=1", (sid,)).fetchone()["n"]
    skip = con.execute("SELECT COUNT(*) n FROM play_history WHERE song_id=? AND skipped=1", (sid,)).fetchone()["n"]
    con.close()
    if not song: raise HTTPException(404, "Song not found")
    return {"song": dict(song), "unique_sessions": uniq, "avg_listen_sec": int(avg or 0),
            "completions": comp, "skips": skip}

# ---------- AI-ish recommendations (no account, session-based) ----------
@app.get("/api/recommend")
def recommend(session_id: str = ""):
    con = db()
    if session_id:
        rows = con.execute("""SELECT s.category, COUNT(*) n FROM play_history h
          JOIN songs s ON s.song_id=h.song_id WHERE h.session_id=? GROUP BY s.category
          ORDER BY n DESC LIMIT 2""", (session_id,)).fetchall()
        cats = [r["category"] for r in rows]
        played = [r["song_id"] for r in con.execute(
            "SELECT song_id FROM play_history WHERE session_id=?", (session_id,)).fetchall()]
    else:
        cats, played = [], []
    if cats:
        ph = ",".join("?" * len(cats))
        recs = con.execute(f"""SELECT * FROM songs WHERE enabled=1 AND category IN ({ph})
          ORDER BY play_count DESC LIMIT 6""", cats).fetchall()
        recs = [dict(r) for r in recs if r["song_id"] not in played][:3]
    else:
        recs = [dict(r) for r in con.execute(
            "SELECT * FROM songs WHERE enabled=1 ORDER BY play_count DESC LIMIT 3").fetchall()]
    con.close()
    note = f"You seem to prefer {', '.join(cats)}. " if cats else "Trending picks for new listeners. "
    return {"picks": recs, "note": note + "No account needed — based only on this session."}

# ---------- live websocket for admin dashboard ----------
LIVE: dict = {}
ADMIN_WS: set = set()

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    ADMIN_WS.add(ws)
    try:
        while True:
            now = time.time()
            users = [{"guest_id": g, "song": v["song"],
                      "duration": fmt_dur(int(now - v["entry"]))}
                     for g, v in list(LIVE.items()) if now - v["last"] <= 90]
            await ws.send_json({"type": "live", "count": len(users), "users": users, "ts": int(now)})
            import asyncio
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        ADMIN_WS.discard(ws)

# ---------- static frontend ----------
app.mount("/assets", StaticFiles(directory=str(BASE / "assets")), name="assets")
app.mount("/css", StaticFiles(directory=str(BASE / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(BASE / "js")), name="js")

@app.get("/admin/{path:path}")
def admin_pages(path: str):
    f = BASE / "admin" / (path or "login.html")
    if path == "" or path.endswith("/"): f = BASE / "admin" / "dashboard.html"
    if f.suffix == "": f = f.with_suffix(".html")
    if f.exists() and f.is_file(): return FileResponse(str(f))
    return FileResponse(str(BASE / "admin" / "login.html"))

@app.get("/{path:path}")
def pages(path: str):
    if path.startswith("api/"): raise HTTPException(404, "Not found")
    f = BASE / f"{path or 'index.html'}"
    if path == "": f = BASE / "index.html"
    if f.exists() and f.is_file(): return FileResponse(str(f))
    idx = BASE / "index.html"
    if idx.exists(): return FileResponse(str(idx))
    return JSONResponse({"message": "A Music API — frontend not built yet"})

if __name__ == "__main__":
    import uvicorn
    print("\n  A MUSIC -> http://localhost:8000")
    print("  First run? POST /api/admin/setup to create the Super Admin\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
