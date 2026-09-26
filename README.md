# 🎵 A MUSIC — Music • Customize • Play • Remix

No-login music experience + real-time Web Audio studio + chaos funny-sounds mode + full admin CMS.

## Run (2 commands)

```powershell
cd a/backend
pip install -r requirements.txt
python server.py
```

Open → **http://localhost:8000**

## User side (no account)
- `/` — home, featured, trending, session-based recommendations
- `/music.html` — library with search + category filter
- `/player.html` — **Audio Studio**: volume, bass, treble, beat, intensity, speed, pitch, vocal, reverb, echo, distortion, stereo + 11 presets + **Chaos Mode** (OFF/LOW/MEDIUM/HIGH/INSANE) that randomly drops funny sounds
- `/effects.html` — funny soundboard (synthesized live via Web Audio API, zero files needed)

Audio graph: `audio → bass → treble → distortion → echo/reverb sends → stereo → master → analyser → speakers` (+ separate synth bus for effects).

## Admin side
- `/admin/login.html` — first visit: create the **Super Admin** (ID auto-generated like `BF-ADM-8K29X`, password auto-generated, stored as PBKDF2-SHA256 salted hash, shown once)
- `/admin/dashboard.html` — dashboard, music manager (upload file or URL + thumbnail + tags + featured), live users (WebSocket, no refresh), analytics (visitors, plays, hourly chart, per-song stats), theme builder (colors, fonts, background, live preview, activate instantly), sound-effect manager (frequency + probability drive chaos mode), admin manager (roles: super_admin / admin / content_manager / analytics_viewer), settings.

## API cheat sheet
- `POST /api/admin/setup`, `POST /api/admin/login`, `POST /api/admin/create`
- `GET /api/songs`, `POST /api/songs/upload`, `POST /api/songs/{id}/play`
- `GET /api/effects`, `POST /api/effects/{id}/use`
- `GET /api/themes/active`, `POST /api/themes`
- `POST /api/sessions/join|heartbeat|leave`, `GET /api/live`, `WS /ws/live`
- `GET /api/analytics/overview`, `GET /api/recommend?session_id=`

Demo songs use public SoundHelix sample MP3s so it plays instantly; replace via Admin → Music.
