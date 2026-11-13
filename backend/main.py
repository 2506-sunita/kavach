import os, sqlite3, secrets, time
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "kavach.db")
ADMIN_KEY = os.getenv("ADMIN_KEY", "admin123")
DEBUG_OTP = os.getenv("DEBUG_OTP", "1") == "1"   # production me 0 kar do
app = FastAPI(title="Kavach API")
VOICE_DIR = os.path.join(BASE, "voice_notes")

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

with db() as c:
    c.executescript("""
    create table if not exists users(phone text primary key, name text, token text, created real);
    create table if not exists otps(phone text primary key, code text, exp real);
    create table if not exists contacts(id integer primary key autoincrement, user text, name text, phone text, is_emergency int default 0);
    create table if not exists alerts(id integer primary key autoincrement, user text, lat real, lng real, trigger text, status text default 'active', created real, updated real);
    """)

for col in ("message text", "track text", "voice text"):
    try:
        with db() as c: c.execute(f"alter table alerts add column {col}")
    except Exception: pass

def send_sms(to, text):
    """Twilio keys set hain to real SMS, warna console me print (demo mode)."""
    sid, tok, frm = os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"), os.getenv("TWILIO_FROM")
    if sid and tok and frm:
        from twilio.rest import Client
        Client(sid, tok).messages.create(to=to, from_=frm, body=text)
        return "sent"
    print(f"[DEMO SMS] to={to}\n{text}\n")
    return "demo"

def me(authorization: str = Header("")):
    t = authorization.replace("Bearer ", "").strip()
    with db() as c:
        u = c.execute("select * from users where token=?", (t,)).fetchone() if t else None
    if not u: raise HTTPException(401, "Login required")
    return u["phone"]

def admin(key: str):
    if key != ADMIN_KEY: raise HTTPException(403, "Bad admin key")

class OTPReq(BaseModel): phone: str
class Verify(BaseModel): phone: str; otp: str; name: str = ""
class Contact(BaseModel): name: str; phone: str; is_emergency: bool = False
class SOS(BaseModel): lat: Optional[float] = None; lng: Optional[float] = None; trigger: str = "button"; message: Optional[str] = None
class Loc(BaseModel): lat: float; lng: float

@app.post("/api/auth/send-otp")
def send_otp(r: OTPReq):
    code = f"{secrets.randbelow(10**6):06d}"
    with db() as c:
        c.execute("replace into otps values(?,?,?)", (r.phone, code, time.time() + 300))
    send_sms(r.phone, f"Kavach OTP: {code} (5 min valid)")
    return {"ok": True, "debug_otp": code if DEBUG_OTP else None}

@app.post("/api/auth/verify")
def verify(r: Verify):
    with db() as c:
        o = c.execute("select * from otps where phone=?", (r.phone,)).fetchone()
        if not o or o["code"] != r.otp or o["exp"] < time.time():
            raise HTTPException(400, "Galat ya expired OTP")
        tok = secrets.token_hex(24)
        c.execute("insert into users values(?,?,?,?) on conflict(phone) do update set token=?, name=coalesce(nullif(?,''),name)",
                  (r.phone, r.name, tok, time.time(), tok, r.name))
        c.execute("delete from otps where phone=?", (r.phone,))
    return {"token": tok}

@app.get("/api/contacts")
def list_contacts(authorization: str = Header("")):
    u = me(authorization)
    with db() as c:
        return [dict(x) for x in c.execute("select * from contacts where user=?", (u,))]

@app.post("/api/contacts")
def add_contact(r: Contact, authorization: str = Header("")):
    u = me(authorization)
    with db() as c:
        if r.is_emergency: c.execute("update contacts set is_emergency=0 where user=?", (u,))
        c.execute("insert into contacts(user,name,phone,is_emergency) values(?,?,?,?)", (u, r.name, r.phone, int(r.is_emergency)))
    return {"ok": True}

@app.delete("/api/contacts/{cid}")
def del_contact(cid: int, authorization: str = Header("")):
    u = me(authorization)
    with db() as c: c.execute("delete from contacts where id=? and user=?", (cid, u))
    return {"ok": True}

@app.post("/api/sos")
def sos(r: SOS, request: Request, authorization: str = Header("")):
    u = me(authorization); now = time.time(); track = secrets.token_urlsafe(8)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    base = os.getenv("PUBLIC_URL") or f"https://{host}"
    note = (r.message or "").strip()[:200]
    with db() as c:
        row = c.execute("select name from users where phone=?", (u,)).fetchone()
        name = row["name"] if row and row["name"] else u
        aid = c.execute("insert into alerts(user,lat,lng,trigger,created,updated,message,track) values(?,?,?,?,?,?,?,?)",
                        (u, r.lat, r.lng, r.trigger, now, now, note, track)).lastrowid
        contacts = [dict(x) for x in c.execute("select * from contacts where user=?", (u,))]
    link = f"https://www.google.com/maps?q={r.lat},{r.lng}" if r.lat is not None else "unavailable"
    track_url = f"{base}/track/{track}"
    msg = (f"EMERGENCY ALERT: {name} ({u}) needs immediate help.\n"
           + (f"Message: {note}\n" if note else "")
           + f"Location: {link}\nLive tracking and voice note: {track_url}")
    for ct in contacts:
        try: send_sms(ct["phone"], msg)
        except Exception as e: print("SMS fail", ct["phone"], e)
    emerg = next((c["phone"] for c in contacts if c["is_emergency"]), contacts[0]["phone"] if contacts else None)
    return {"alert_id": aid, "link": link, "track_url": track_url, "message": msg, "contacts": contacts, "call": emerg}

@app.post("/api/sos/{aid}/voice")
async def upload_voice(aid: int, request: Request, authorization: str = Header("")):
    u = me(authorization); data = await request.body()
    if not data or len(data) > 5_000_000: raise HTTPException(400, "Invalid audio")
    ext = "mp4" if "mp4" in request.headers.get("content-type", "") else "webm"
    os.makedirs(VOICE_DIR, exist_ok=True)
    path = os.path.join(VOICE_DIR, f"{aid}.{ext}")
    with open(path, "wb") as f: f.write(data)
    with db() as c: c.execute("update alerts set voice=? where id=? and user=?", (path, aid, u))
    return {"ok": True}

def _by_track(t):
    with db() as c: a = c.execute("select * from alerts where track=?", (t,)).fetchone()
    if not a: raise HTTPException(404, "Not found")
    return a

@app.get("/track/{t}/voice")
def track_voice(t: str):
    a = _by_track(t)
    if not a["voice"] or not os.path.exists(a["voice"]): raise HTTPException(404, "No voice note")
    return FileResponse(a["voice"], media_type="audio/mp4" if a["voice"].endswith("mp4") else "audio/webm")

@app.get("/track/{t}", response_class=HTMLResponse)
def track_page(t: str):
    a = _by_track(t)
    has = a["lat"] is not None
    m = f"https://maps.google.com/maps?q={a['lat']},{a['lng']}&z=16&output=embed" if has else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15"><title>Kavach Live Tracking</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;padding:16px;max-width:560px;margin:auto;background:#f8fafc;color:#111827}}
.b{{display:inline-block;padding:4px 12px;border-radius:99px;color:#fff;background:{'#dc2626' if a['status']=='active' else '#16a34a'}}}
a.btn{{display:block;margin:12px 0;padding:14px;border-radius:10px;background:#dc2626;color:#fff;text-align:center;text-decoration:none}}</style></head><body>
<h2>Kavach Emergency Tracking</h2><p><span class="b">{a['status'].upper()}</span> &nbsp; Last update: {time.strftime('%d %b %Y, %H:%M:%S', time.gmtime(a['updated']+19800))} IST</p>
{f'<p><b>Message:</b> {a["message"]}</p>' if a['message'] else ''}
{f'<iframe src="{m}" width="100%" height="320" style="border:0;border-radius:12px"></iframe><a class="btn" href="https://www.google.com/maps?q={a["lat"]},{a["lng"]}">Open in Google Maps</a>' if has else '<p>Location not available.</p>'}
{f'<p><b>Voice note</b></p><audio controls style="width:100%" src="/track/{t}/voice"></audio>' if a['voice'] else '<p>Voice note: not received yet.</p>'}
<p style="color:#6b7280;font-size:13px">This page refreshes every 15 seconds.</p></body></html>"""

@app.get("/api/admin/voice/{aid}")
def admin_voice(aid: int, x_admin_key: str = Header("")):
    admin(x_admin_key)
    with db() as c: a = c.execute("select voice from alerts where id=?", (aid,)).fetchone()
    if not a or not a["voice"]: raise HTTPException(404, "No voice note")
    return FileResponse(a["voice"], media_type="audio/mp4" if a["voice"].endswith("mp4") else "audio/webm")

@app.post("/api/sos/{aid}/location")
def update_loc(aid: int, r: Loc, authorization: str = Header("")):
    u = me(authorization)
    with db() as c:
        c.execute("update alerts set lat=?,lng=?,updated=? where id=? and user=? and status='active'", (r.lat, r.lng, time.time(), aid, u))
    return {"ok": True}

@app.post("/api/sos/{aid}/resolve")
def resolve(aid: int, authorization: str = Header("")):
    u = me(authorization)
    with db() as c: c.execute("update alerts set status='resolved',updated=? where id=? and user=?", (time.time(), aid, u))
    return {"ok": True}

@app.get("/api/admin/alerts")
def admin_alerts(x_admin_key: str = Header("")):
    admin(x_admin_key)
    with db() as c:
        return [dict(x) for x in c.execute("select * from alerts order by id desc limit 200")]

@app.get("/api/admin/stats")
def admin_stats(x_admin_key: str = Header("")):
    admin(x_admin_key)
    with db() as c:
        q = lambda s: c.execute(s).fetchone()[0]
        return {"users": q("select count(*) from users"), "alerts": q("select count(*) from alerts"),
                "active": q("select count(*) from alerts where status='active'")}

app.mount("/", StaticFiles(directory=os.path.join(BASE, "..", "frontend"), html=True), name="web")
