import os, requests, pandas as pd, streamlit as st
try:
    API = os.getenv("KAVACH_API") or st.secrets["KAVACH_API"]
except Exception:
    API = "http://localhost:8000"
API = API.rstrip("/")
st.set_page_config(page_title="Kavach Dashboard", page_icon="🛡️", layout="wide")
st.title("🛡️ Kavach Live Safety Dashboard")
key = st.sidebar.text_input("Admin key", value=os.getenv("ADMIN_KEY", ""), type="password")
st.sidebar.caption(f"API: {API}")
H = {"X-Admin-Key": key}

@st.fragment(run_every=10)   # har 10 sec auto-refresh
def live():
    try:
        s = requests.get(f"{API}/api/admin/stats", headers=H, timeout=60).json()
        a = requests.get(f"{API}/api/admin/alerts", headers=H, timeout=60).json()
    except Exception as e:
        st.error(f"Backend se connect nahi hua: {e}"); return
    if isinstance(s, dict) and "detail" in s: st.error(s["detail"]); return
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Users", s["users"]); c2.metric("Total Alerts", s["alerts"]); c3.metric("🔴 Active Alerts", s["active"])
    df = pd.DataFrame(a)
    if df.empty: st.info("Abhi koi alert nahi hai."); return
    for col in ("created", "updated"):
        df[col] = pd.to_datetime(df[col], unit="s") + pd.Timedelta(hours=5, minutes=30)
    st.subheader("Map (active alerts)")
    m = df[(df.status == "active") & df.lat.notna()].rename(columns={"lng": "lon"})
    if len(m): st.map(m[["lat", "lon"]])
    else: st.caption("Koi active alert location ke saath nahi.")
    st.subheader("All alerts")
    st.dataframe(df.drop(columns=["voice", "track"], errors="ignore"), use_container_width=True)
    if "voice" in df and df.voice.notna().any():
        st.subheader("Voice notes")
        pick = st.selectbox("Alert ID", df[df.voice.notna()].id.tolist(), key="vn")
        r = requests.get(f"{API}/api/admin/voice/{pick}", headers=H, timeout=10)
        if r.ok: st.audio(r.content)
live()
