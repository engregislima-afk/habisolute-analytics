# -*- coding: utf-8 -*-
# app.py — Habisolute Analytics (login + painel + tema + header + pipeline + validações + auditoria)

import io, re, os, json, base64, tempfile, zipfile, hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import streamlit as st
import pandas as pd
import pdfplumber
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# PDF (ReportLab)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,90Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido pela Habisolute Engenharia"

class NumberedCanvas(pdfcanvas.Canvas):
    ORANGE = colors.HexColor("#f97316")
    BLACK  = colors.black

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_fixed_bars_and_footer(total_pages)
            super().showPage()
        super().save()

    def _wrap_footer(self, text, font_name="Helvetica", font_size=7, max_width=None):
        if max_width is None:
            max_width = self._pagesize[0] - 36 - 120
        words = text.split()
        lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.stringWidth(test, font_name, font_size) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines

    def _draw_fixed_bars_and_footer(self, total_pages: int):
        w, h = self._pagesize
        # Cabeçalho
        self.setFillColor(self.ORANGE); self.rect(0, h - 10, w, 6, stroke=0, fill=1)
        self.setFillColor(self.BLACK);   self.rect(0, h - 16, w, 2, stroke=0, fill=1)
        # Rodapé
        self.setFillColor(self.BLACK);   self.rect(0, 8, w, 2, stroke=0, fill=1)
        self.setFillColor(self.ORANGE);  self.rect(0, 12, w, 6, stroke=0, fill=1)
        # Textos
        y0 = 44
        self.setFillColor(colors.black); self.setFont("Helvetica", 7)
        lines = self._wrap_footer(FOOTER_TEXT, "Helvetica", 7, w - 36 - 100)
        for i, ln in enumerate(lines):
            y = y0 + i * 8; self.drawString(18, y, ln)
        self.setFont("Helvetica-Oblique", 8)
        self.drawCentredString(w / 2.0, y0 - 8, FOOTER_BRAND_TEXT)
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, y0 - 18, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"; USERS_DB = PREFS_DIR / "users.json"

# ====== Auditoria (JSONL) ======
AUDIT_LOG = PREFS_DIR / "audit.jsonl"
def _now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
def log_event(action: str, meta: Dict[str, Any] | None = None, level: str = "INFO"):
    try:
        rec = {
            "ts": _now_iso(),
            "user": st.session_state.get("username") or "anon",
            "level": level,
            "action": action,
            "meta": meta or {},
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
def read_audit_df() -> pd.DataFrame:
    if not AUDIT_LOG.exists():
        return pd.DataFrame(columns=["ts","user","level","action","meta"])
    rows = []
    with AUDIT_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rows.append({
                    "ts": rec.get("ts"),
                    "user": rec.get("user"),
                    "level": rec.get("level"),
                    "action": rec.get("action"),
                    "meta": json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                })
            except Exception:
                continue
    df = pd.DataFrame(rows, columns=["ts","user","level","action","meta"])
    if not df.empty:
        df = df.sort_values("ts", ascending=False, kind="stable").reset_index(drop=True)
    return df

# ----- prefs util -----
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PREFS_PATH)
def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists(): return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception: pass
    return {}
def load_user_prefs(key: str = "default") -> Dict[str, Any]: return _load_all_prefs().get(key, {})
def save_user_prefs(prefs: Dict[str, Any], key: str = "default") -> None:
    data = _load_all_prefs(); data[key] = prefs; _save_all_prefs(data)

# ===== Estado =====
s = st.session_state
s.setdefault("logged_in", False); s.setdefault("username", None); s.setdefault("is_admin", False)
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0); s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0); s.setdefault("BATCH_MODE", False); s.setdefault("_prev_batch", s["BATCH_MODE"])

# Recupera usuário após refresh se necessário
if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"): s["username"] = _p["last_user"]

# --- preferências via URL ---
def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x):
            if x is None: return None
            return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr    = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno","Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja","Azul","Verde","Roxo"): s["brand"] = brand
        if qr: s["qr_url"] = qr
    except Exception: pass
_apply_query_prefs()

# =============================================================================
# Estilo e tema
# =============================================================================
BRAND_MAP = {
    "Laranja": ("#f97316", "#ea580c", "#c2410c"),
    "Azul":    ("#3b82f6", "#2563eb", "#1d4ed8"),
    "Verde":   ("#22c55e", "#16a34a", "#15803d"),
    "Roxo":    ("#a855f7", "#9333ea", "#7e22ce"),
}
brand, brand600, brand700 = BRAND_MAP.get(s["brand"], BRAND_MAP["Laranja"])

plt.rcParams.update({"font.size":10,"axes.titlesize":12,"axes.labelsize":10,"axes.titleweight":"semibold","figure.autolayout":False})

if s["theme_mode"] == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#0b0f19; --panel:#0f172a; --surface:#111827; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:rgba(148,163,184,.10); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.18) !important;
    }}
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div, .stDateInput input {{
      background: var(--surface) !important; color: var(--text) !important; border-color: var(--line) !important;
    }}
    .stExpander > details > summary {{ background: var(--panel) !important; color: var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f8fafc; --surface:#ffffff; --panel:#ffffff; --text:#0f172a; --muted:#475569; --line:rgba(2,6,23,.10);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--text); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.08) !important;
    }}
    .stTextInput input, .stNumberInput input, .stDateInput input {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stExpander > details > summary {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
# forçar legibilidade de alerts
css += """
st.markdown("""
<style>
.stAlert { border-radius:12px; border:1px solid var(--brand-600); }
.stAlert [data-testid="stMarkdown"] p,
.stAlert [data-testid="stMarkdown"] {
  color:#111827 !important;
}
</style>
""", unsafe_allow_html=True)

# -------- Cabeçalho ----------
def _render_header():
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='app-header'><span class='brand-title' style='font-weight:800; font-size:22px; color: var(--text)'>🏗️ Habisolute IA</span></div>", unsafe_allow_html=True)
    st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

# =============================================================================
# Autenticação & gerenciamento de usuários
# =============================================================================
def _hash_password(pw: str) -> str: return hashlib.sha256(("habisolute|" + pw).encode("utf-8")).hexdigest()
def _verify_password(pw: str, hashed: str) -> bool:
    try: return _hash_password(pw) == hashed
    except Exception: return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)
def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"), "is_admin": True, "active": True, "must_change": True,
                "created_at": datetime.now().isoformat(timespec="seconds")
            }
        return db
    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data); 
                    if fixed is not data: _save_users(fixed)
                    return fixed
                if isinstance(data, dict):
                    fixed = _bootstrap_admin({"users": data}); _save_users(fixed); return fixed
                if isinstance(data, list):
                    users_map: Dict[str, Any] = {}
                    for item in data:
                        if isinstance(item, str):
                            uname = item.strip()
                            if not uname: continue
                            users_map[uname] = {"password": _hash_password("1234"), "is_admin": (uname=="admin"),
                                                "active": True, "must_change": True,
                                                "created_at": datetime.now().isoformat(timespec="seconds")}
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {"password": _hash_password("1234"),
                                                "is_admin": bool(item.get("is_admin", uname=="admin")),
                                                "active": bool(item.get("active", True)),
                                                "must_change": True,
                                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))}
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception: pass
    default = _bootstrap_admin({"users": {}}); _save_users(default); return default

def user_get(username: str) -> Optional[Dict[str, Any]]: return _load_users().get("users", {}).get(username)
def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)
def user_exists(username: str) -> bool: return user_get(username) is not None
def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out=[]
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"]=uname; out.append(r)
    out.sort(key=lambda r:(not r.get("is_admin",False), r["username"])); return out
def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin": return
        db["users"].pop(username, None); _save_users(db)

def _auth_login_ui():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Entrar - 🏗️ Habisolute Analytics</div>", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.3,1.3,0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password",
                            label_visibility="collapsed", placeholder="Senha")
    with c3:
        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True):
                st.error("Usuário inexistente ou inativo.")
                log_event("login_fail", {"username": user, "reason": "not_found_or_inactive"}, level="WARN")
            elif not _verify_password(pwd, rec.get("password","")):
                st.error("Senha incorreta.")
                log_event("login_fail", {"username": user, "reason": "bad_password"}, level="WARN")
            else:
                s["logged_in"]=True; s["username"]=(user or "").strip()
                s["is_admin"]=bool(rec.get("is_admin",False)); s["must_change"]=bool(rec.get("must_change",False))
                prefs = load_user_prefs(); prefs["last_user"]=s["username"]; save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"]})
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password"); p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1)<4: st.error("Use ao menos 4 caracteres.")
        elif p1!=p2: st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"]=_hash_password(p1); rec["must_change"]=False; user_set(username, rec)
            log_event("password_changed", {"username": username})
            st.success("Senha atualizada! Redirecionando…"); s["must_change"]=False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Tela de login
# =============================================================================
if not s["logged_in"]:
    _auth_login_ui()
    st.stop()

# Troca obrigatória de senha
if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    st.stop()

# >>> Cabeçalho
_render_header()

# =============================================================================
# Toolbar de preferências
# =============================================================================
st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
c1,c2,c3,c4 = st.columns([1.1,1.1,2.5,1.1])
with c1:
    s["theme_mode"] = st.radio("Tema", ["Escuro moderno","Claro corporativo"],
                              index=0 if s.get("theme_mode")=="Escuro moderno" else 1, horizontal=True)
with c2:
    s["brand"] = st.selectbox("🎨 Cor da marca", ["Laranja","Azul","Verde","Roxo"],
                              index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja")))
with c3:
    s["qr_url"] = st.text_input("URL do resumo (QR opcional na capa do PDF)", value=s.get("qr_url",""),
                                placeholder="https://exemplo.com/resumo")
with c4:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
            save_user_prefs({
                "theme_mode": s["theme_mode"], "brand": s["brand"], "qr_url": s["qr_url"],
                "last_user": s.get("username") or load_user_prefs().get("last_user","")
            })
            try:
                qp = st.query_params; qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
            except Exception: pass
            st.success("Preferências salvas! Dica: adicione esta página aos favoritos.")
    with col_b:
        if st.button("Sair", use_container_width=True, key="k_logout"):
            log_event("logout", {"username": s.get("username")})
            s["logged_in"] = False; st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

# ---- Boas-vindas do usuário
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
papel = "Admin" if s.get("is_admin") else "Usuário"
st.markdown(
    f"""
    <div style="margin:10px 0 4px 0; padding:10px 12px; border-radius:12px;
                border:1px solid var(--line); background:rgba(148,163,184,.10); font-weight:600;">
      👋 Olá, <b>{nome_login}</b> — <span style="opacity:.85">{papel}</span>
    </div>
    """,
    unsafe_allow_html=True
)

# =============================================================================
# Painel de Usuários (somente admin) + Auditoria
# =============================================================================
CAN_ADMIN  = bool(s.get("is_admin", False))
CAN_EXPORT = CAN_ADMIN

def _empty_audit_df():
    return pd.DataFrame(columns=["ts", "user", "level", "action", "meta"])

df_log = _empty_audit_df()  # evita NameError para não-admin

if CAN_ADMIN:
    with st.expander("👤 Painel de Usuários (Admin)", expanded=False):
        st.markdown("Cadastre, ative/desative e redefina senhas dos usuários do sistema.")
        tab1, tab2, tab3 = st.tabs(["Usuários", "Novo usuário", "Auditoria"])

        with tab1:
            users = user_list()
            if not users:
                st.info("Nenhum usuário cadastrado.")
            else:
                for u in users:
                    colA,colB,colC,colD,colE = st.columns([2,1,1.2,1.6,1.4])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else "Usuário")
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write(("Exige troca" if u.get("must_change") else "Senha OK"))
                    with colE:
                        if u["username"] != "admin":
                            if st.button(("Desativar" if u.get("active", True) else "Reativar"), key=f"act_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["active"] = not rec.get("active", True)
                                user_set(u["username"], rec)
                                st.rerun()
                            if st.button("Redefinir", key=f"rst_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["password"] = _hash_password("1234")
                                rec["must_change"] = True
                                user_set(u["username"], rec)
                                st.rerun()
                            if st.button("Excluir", key=f"del_{u['username']}"):
                                user_delete(u["username"])
                                st.rerun()

        with tab2:
            st.markdown("### Novo usuário")
            new_u = st.text_input("Usuário (login)")
            is_ad = st.checkbox("Admin?", value=False)
            if st.button("Criar usuário", key="btn_new_user"):
                if not new_u.strip():
                    st.error("Informe o nome do usuário.")
                elif user_exists(new_u.strip()):
                    st.error("Usuário já existe.")
                else:
                    user_set(new_u.strip(), {
                        "password": _hash_password("1234"),
                        "is_admin": bool(is_ad),
                        "active": True,
                        "must_change": True,
                        "created_at": datetime.now().isoformat(timespec="seconds")
                    })
                    log_event("user_created", {"created_user": new_u.strip(), "is_admin": bool(is_ad)})
                    st.success("Usuário criado com senha inicial 1234 (forçará troca no primeiro acesso).")
                    st.rerun()

        with tab3:
            st.markdown("### Auditoria do Sistema")
            df_log = read_audit_df()
            if df_log.empty:
                st.info("Sem eventos de auditoria ainda.")
            else:
                try:
                    _d = pd.to_datetime(df_log["ts"].str.replace("Z", "", regex=False), errors="coerce").dt.date
                    hoje = datetime.utcnow().date()
                    tot_ev = int(len(df_log))
                    tot_usr = int(df_log["user"].nunique())
                    tot_act = int(df_log["action"].nunique())
                    tot_hoje = int((_d == hoje).sum())
                except Exception:
                    tot_ev = len(df_log); tot_usr = 0; tot_act = 0; tot_hoje = 0

                st.markdown(
                    f"""
                    <div style="display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 10px 0">
                      <div class="h-card"><div class="h-kpi-label">Eventos</div><div class="h-kpi">{tot_ev}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Por usuário</div><div class="h-kpi">{tot_usr}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Por ação</div><div class="h-kpi">{tot_act}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Hoje</div><div class="h-kpi">{tot_hoje}</div></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.6, 1.0])
                with c1:
                    users_opt = ["(Todos)"] + sorted([u for u in df_log["user"].dropna().unique().tolist()])
                    f_user = st.selectbox("Usuário", users_opt, index=0)
                with c2:
                    f_action = st.text_input("Ação contém...", "")
                with c3:
                    lv_opts = ["(Todos)", "INFO", "WARN", "ERROR"]
                    f_level = st.selectbox("Nível", lv_opts, index=0)
                with c4:
                    page_size = st.selectbox("Linhas", [100, 300, 1000], index=1)

                d1, d2 = st.columns(2)
                with d1:
                    dt_min = st.date_input("Data inicial", value=None, key="aud_dini")
                with d2:
                    dt_max = st.date_input("Data final", value=None, key="aud_dfim")

                logv = df_log.copy()
                if f_user and f_user != "(Todos)":
                    logv = logv[logv["user"] == f_user]
                if f_action:
                    logv = logv[logv["action"].str.contains(f_action, case=False, na=False)]
                if f_level and f_level != "(Todos)":
                    logv = logv[logv["level"] == f_level]

                if "ts" in logv.columns:
                    logv["_d"] = pd.to_datetime(logv["ts"].str.replace("Z", "", regex=False), errors="coerce").dt.date
                    if dt_min:
                        logv = logv[logv["_d"].apply(lambda d: (d is not None) and (d >= dt_min))]
                    if dt_max:
                        logv = logv[logv["_d"].apply(lambda d: (d is not None) and (d <= dt_max))]
                    logv = logv.drop(columns=["_d"], errors="ignore")

                st.caption(f"{len(logv)} evento(s) filtrados)")

                total = len(logv)
                if total > 0:
                    pcols = st.columns([1, 3, 1])
                    with pcols[0]:
                        page = st.number_input("Página", min_value=1, max_value=max(1, (total - 1) // page_size + 1), value=1, step=1)
                    start = (int(page) - 1) * int(page_size)
                    end = start + int(page_size)
                    view = logv.iloc[start:end].copy()
                else:
                    view = logv.copy()

                st.dataframe(view, use_container_width=True)

                try:
                    dts = pd.to_datetime(logv["ts"].str.replace("Z", "", regex=False), errors="coerce").dropna()
                    if not dts.empty:
                        pmin = dts.min().strftime("%Y-%m-%d")
                        pmax = dts.max().strftime("%Y-%m-%d")
                        periodo = f"{pmin}_{pmax}" if pmin != pmax else pmin
                    else:
                        periodo = datetime.utcnow().strftime("%Y-%m-%d")
                except Exception:
                    periodo = datetime.utcnow().strftime("%Y-%m-%d")
                usuario_lbl = s.get("username") or "anon"

                cdl1, cdl2 = st.columns([1, 1])
                with cdl1:
                    st.download_button(
                        "⬇️ CSV (filtro aplicado)",
                        data=logv.to_csv(index=False).encode("utf-8"),
                        file_name=f"audit_{periodo}_{usuario_lbl}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with cdl2:
                    st.download_button(
                        "⬇️ JSONL (completo)",
                        data=AUDIT_LOG.read_bytes() if AUDIT_LOG.exists() else b"",
                        file_name=f"audit_full_{periodo}.jsonl",
                        mime="application/json",
                        use_container_width=True,
                    )
else:
    pass
# =============================================================================
# >>> DAQUI PRA BAIXO (PIPELINE): uploader, parsing, gráficos, PDF, etc.
# =============================================================================
TOL_MP    = float(s.get("TOL_MP", 1.0))
BATCH_MODE = bool(s.get("BATCH_MODE", False))

# Sidebar (opções de relatório)
with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]))
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1)
    st.markdown("---")
    nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
papel = "Admin" if s.get("is_admin") else "Usuário"
st.caption(f"Usuário: **{nome_login}** ({papel})")

# ====================== Utilidades de parsing / limpeza ======================
def _limpa_horas(txt: str) -> str:
    txt = re.sub(r"\b\d{1,2}:\d{2}\b", "", txt)
    txt = re.sub(r"\bàs\s*\d{1,2}:\d{2}\b", "", txt, flags=re.I)
    return re.sub(r"\s{2,}", " ", txt).strip(" -•:;,.") 

def _limpa_usina_extra(txt: Optional[str]) -> Optional[str]:
    if not txt: return txt
    t = _limpa_horas(str(txt))
    t = re.sub(r"(?i)relat[óo]rio:\s*\d+\s*", "", t)
    t = re.sub(r"(?i)\busina:\s*", "", t)
    t = re.sub(r"(?i)\bsa[ií]da\s+da\s+usina\b.*$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -•:;,.")
    return t or None

def _detecta_usina(linhas: List[str]) -> Optional[str]:
    for sline in linhas:
        if re.search(r"(?i)\busina:", sline):
            s0 = _limpa_horas(sline)
            m = re.search(r"(?i)usina:\s*([A-Za-zÀ-ÿ0-9 .\-]+?)(?:\s+sa[ií]da\s+da\s+usina\b|$)", s0)
            if m: return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
            return _limpa_usina_extra(s0)
    for sline in linhas:
        if re.search(r"(?i)\busina\b", sline) or re.search(r"(?i)sa[ií]da da usina", sline):
            t = _limpa_horas(sline)
            t2 = re.sub(r"(?i)^.*\busina\b[:\-]?\s*", "", t).strip()
            if t2: return t2
            if t: return t
    return None

def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok: return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*\+?-?\s*(\d+(?:\.\d+)?))?\s*$", t)
    if not m: return None, None
    try:
        v = float(m.group(1))
        tol = float(m.group(2)) if m.group(2) is not None else None
        return v, tol
    except Exception:
        return None, None

def _detecta_abatimentos(linhas: List[str]) -> Tuple[Optional[float], Optional[float]]:
    abat_nf = None; abat_obra = None
    for sline in linhas:
        s_clean = sline.replace(",", ".").replace("±", "+-")
        m_nf = re.search(
            r"(?i)abat(?:imento|\.?im\.?)\s*(?:de\s*)?nf[^0-9]*"
            r"(\d+(?:\.\d+)?)(?:\s*\+?-?\s*\d+(?:\.\d+)?)?\s*mm?",
            s_clean
        )
        if m_nf and abat_nf is None:
            try: abat_nf = float(m_nf.group(1))
            except Exception: pass
        m_obra = re.search(
            r"(?i)abat(?:imento|\.?im\.?).*(obra|medido em obra)[^0-9]*"
            r"(\d+(?:\.\d+)?)\s*mm",
            s_clean
        )
        if m_obra and abat_obra is None:
            try: abat_obra = float(m_obra.group(2))
            except Exception: pass
    return abat_nf, abat_obra

def _extract_fck_values(line: str) -> List[float]:
    if not line or "fck" not in line.lower(): return []
    sanitized = line.replace(",", ".")
    parts = re.split(r"(?i)fck", sanitized)[1:]
    if not parts: return []
    values: List[float] = []
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\b\s*[:=]?", re.I)
    age_plain       = re.compile(r"^(\d{1,3})\b\s*[:=]?", re.I)
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = ("mpa","abatimento","slump","nota","usina","relatório","relatorio","consumo","traço","traco","cimento","dosagem")
    for segment in parts:
        starts_immediate = bool(segment) and not segment[0].isspace()
        seg = segment.lstrip(" :=;-()[]")
        changed = True
        while changed:
            changed = False
            m = age_with_suffix.match(seg)
            if m:
                age_val = int(m.group(1))
                if age_val in age_tokens:
                    seg = seg[m.end():].lstrip(" :=;-()[]"); changed = True; continue
            if starts_immediate:
                m2 = age_plain.match(seg)
                if m2:
                    age_val = int(m2.group(1))
                    if age_val in age_tokens:
                        seg = seg[m2.end():].lstrip(" :=;-()[]"); changed = True; continue
        lower_seg = seg.lower()
        cut_at = len(seg)
        for kw in cut_keywords:
            idx = lower_seg.find(kw)
            if idx != -1: cut_at = min(cut_at, idx)
        seg = seg[:cut_at]
        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try: val = float(num)
            except ValueError: continue
            if 3 <= val <= 120 and val not in values:
                values.append(val)
    return values

def _to_float_or_none(value: Any) -> Optional[float]:
    try: val = float(value)
    except (TypeError, ValueError): return None
    return None if pd.isna(val) else val

def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value): return "—"
    num = float(value); label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None: return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan': return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
    try:
        raw = uploaded_file.read()
        uploaded_file.seek(0)
    except Exception:
        raw = uploaded_file.getvalue()

    linhas_todas = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                txt = re.sub(r"[“”]", "\"", txt)
                txt = re.sub(r"[’´`]", "'", txt)
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip() ])
    except Exception:
        return (pd.DataFrame(columns=[
            "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
            "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)"
        ]), "NÃO IDENTIFICADA", "NÃO IDENTIFICADA", "NÃO IDENTIFICADO")

    cp_regex = re.compile(r"^(?:[A-Z]{0,2})?\d{3,6}(?:\.\d{3})?$")
    data_regex = re.compile(r"\d{2}/\d{2}/\d{4}")
    data_token = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    tipo_token = re.compile(r"^A\d$", re.I)
    float_token = re.compile(r"^\d+[.,]\d+$")
    nf_regex = re.compile(r"^(?:\d{2,6}[.\-\/]?\d{3,6}|\d{5,12})$")
    pecas_regex = re.compile(r"(?i)peç[ac]s?\s+concretad[ao]s?:\s*(.*)")

    obra = "NÃO IDENTIFICADA"
    data_relatorio = "NÃO IDENTIFICADA"
    fck_projeto = "NÃO IDENTIFICADO"
    local_por_relatorio: Dict[str, str] = {}
    relatorio_atual = None
    fck_por_relatorio: Dict[str, List[float]] = {}
    fck_valores_globais: List[float] = []

    for sline in linhas_todas:
        if sline.startswith("Obra:"):
            obra = sline.replace("Obra:", "").strip().split(" Data")[0]
        m_data = data_regex.search(sline)
        if m_data and data_relatorio == "NÃO IDENTIFICADA":
            data_relatorio = m_data.group()
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_atual = m_rel.group(1)
        m_pecas = pecas_regex.search(sline)
        if m_pecas and relatorio_atual:
            local_por_relatorio[relatorio_atual] = m_pecas.group(1).strip().rstrip(".")
        if "fck" in sline.lower():
            valores_fck = _extract_fck_values(sline)
            if valores_fck:
                if relatorio_atual:
                    fck_por_relatorio.setdefault(relatorio_atual, []).extend(valores_fck)
                else:
                    fck_valores_globais.extend(valores_fck)
                if not isinstance(fck_projeto, (int, float)):
                    try: fck_projeto = float(valores_fck[0])
                    except Exception: pass

    usina_nome = _limpa_usina_extra(_detecta_usina(linhas_todas))
    abat_nf_pdf, abat_obra_pdf = _detecta_abatimentos(linhas_todas)

    dados = []
    relatorio_cabecalho = None

    for sline in linhas_todas:
        partes = sline.split()

        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel: relatorio_cabecalho = m_rel.group(1)
            continue

        if len(partes) >= 5 and cp_regex.match(partes[0]):
            try:
                cp = partes[0]
                relatorio = relatorio_cabecalho or "NÃO IDENTIFICADO"

                i_data = next((i for i, t in enumerate(partes) if data_token.match(t)), None)
                if i_data is not None:
                    i_tipo = next((i for i in range(i_data + 1, len(partes)) if tipo_token.match(partes[i])), None)
                    start = (i_tipo + 1) if i_tipo is not None else (i_data + 1)
                else:
                    start = 1

                idade_idx, idade = None, None
                for j in range(start, len(partes)):
                    t = partes[j]
                    if t.isdigit():
                        v = int(t)
                        if 1 <= v <= 120:
                            idade = v; idade_idx = j; break

                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", "."))
                            res_idx = j; break

                if idade is None or resistencia is None:
                    continue

                nf, nf_idx = None, None
                start_nf = (res_idx + 1) if res_idx is not None else (idade_idx + 1)
                for j in range(start_nf, len(partes)):
                    tok = partes[j]
                    if nf_regex.match(tok) and tok != cp:
                        nf = tok; nf_idx = j; break

                abat_obra_val = None
                if i_data is not None:
                    for j in range(i_data - 1, max(-1, i_data - 6), -1):
                        tok = partes[j]
                        if re.fullmatch(r"\d{2,3}", tok):
                            v = int(tok)
                            if 20 <= v <= 250:
                                abat_obra_val = float(v); break

                abat_nf_val, abat_nf_tol = None, None
                if nf_idx is not None:
                    for tok in partes[nf_idx + 1: nf_idx + 5]:
                        v, tol = _parse_abatim_nf_pair(tok)
                        if v is not None and 20 <= v <= 250:
                            abat_nf_val = float(v)
                            abat_nf_tol = float(tol) if tol is not None else None
                            break

                local = local_por_relatorio.get(relatorio)
                dados.append([
                    relatorio, cp, idade, resistencia, nf, local,
                    usina_nome,
                    (abat_nf_val if abat_nf_val is not None else abat_nf_pdf),
                    abat_nf_tol,
                    (abat_obra_val if abat_obra_val is not None else abat_obra_pdf)
                ])
            except Exception:
                pass

    df = pd.DataFrame(dados, columns=[
        "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
        "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)"
    ])

    if not df.empty:
        rel_map = {}
        for rel, valores in fck_por_relatorio.items():
            uniques = []
            for valor in valores:
                try:
                    val_f = float(valor)
                except Exception:
                    continue
                if val_f not in uniques: uniques.append(val_f)
            if uniques: rel_map[rel] = uniques[0]

        fallback_fck = None
        if isinstance(fck_projeto, (int, float)):
            fallback_fck = float(fck_projeto)
        else:
            candidatos = []
            for valores in fck_por_relatorio.values(): candidatos.extend(valores)
            candidatos.extend(fck_valores_globais)
            for cand in candidatos:
                try:
                    fallback_fck = float(cand); break
                except Exception:
                    continue

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto

# =========================== KPIs e helpers de gráfico =========================
def compute_exec_kpis(df_view: pd.DataFrame, fck_val: Optional[float]):
    def _pct_hit(age):
        if fck_val is None or pd.isna(fck_val): return None
        g = df_view[df_view["Idade (dias)"] == age].groupby("CP")["Resistência (MPa)"].mean()
        if g.empty: return None
        return float((g >= fck_val).mean() * 100.0)
    pct28 = _pct_hit(28)
    pct63 = _pct_hit(63)
    media_geral = float(pd.to_numeric(df_view["Resistência (MPa)"], errors="coerce").mean()) if not df_view.empty else None
    dp_geral   = float(pd.to_numeric(df_view["Resistência (MPa)"], errors="coerce").std())  if not df_view.empty else None
    n_rel      = df_view["Relatório"].nunique()
    def _semaforo(p28, p63):
        if (p28 is None) and (p63 is None): return ("Sem dados", "#9ca3af")
        score = 0.0
        if p28 is not None: score += float(p28) * 0.6
        if p63 is not None: score += float(p63) * 0.4
        if score >= 90: return ("✅ Bom", "#16a34a")
        if score >= 75: return ("⚠️ Atenção", "#d97706")
        return ("🔴 Crítico", "#ef4444")
    status_txt, status_cor = _semaforo(pct28, pct63)
    return {"pct28": pct28, "pct63": pct63, "media": media_geral, "dp": dp_geral, "n_rel": n_rel, "status_txt": status_txt, "status_cor": status_cor}

def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc="upper left", bbox_to_anchor=(1.02, 1.0),
              frameon=False, ncol=1, handlelength=2.2, handletextpad=0.8, labelspacing=0.35, prop={"size": 9})
    plt.subplots_adjust(right=0.80)

def render_print_block(pdf_all: bytes, pdf_cp: Optional[bytes], brand: str, brand600: str):
    b64_all = base64.b64encode(pdf_all).decode()
    cp_btn = ""
    if pdf_cp:
        b64_cp = base64.b64encode(pdf_cp).decode()
        cp_btn = f'<button class="h-print-btn" onclick="habiPrint(\'{b64_cp}\')">🖨️ Imprimir — CP focado</button>'
    html = f"""
    <style>
      :root {{ --brand:{brand}; --brand-600:{brand600}; }}
      .printbar {{ display:flex; flex-wrap:wrap; gap:12px; margin:10px 0 6px 0; }}
      .h-print-btn {{
        background: linear-gradient(180deg, var(--brand), var(--brand-600));
        color:#fff; border:0; border-radius:999px; padding:10px 16px; font-weight:700; cursor:pointer;
        box-shadow:0 10px 20px rgba(0,0,0,.10);
      }}
    </style>
    <div class="printbar">
      <button class="h-print-btn" onclick="habiPrint('{b64_all}')">🖨️ Imprimir — Tudo</button>
      {cp_btn}
      <span style="font-size:12px;color:#6b7280">Permita pop-ups para imprimir</span>
    </div>
    <script>
      function habiPrint(b64) {{
        try {{
          var bin=atob(b64), len=bin.length, bytes=new Uint8Array(len);
          for (var i=0;i<len;i++) bytes[i]=bin.charCodeAt(i);
          var blob=new Blob([bytes], {{type:'application/pdf'}});
          var url=URL.createObjectURL(blob);
          var w=window.open('', '_blank');
          if(!w){{ alert('Habilite pop-ups para imprimir.'); return; }}
          w.document.write('<!doctype html><html><head><title>Imprimir</title>'+
            '<style>html,body{{margin:0;height:100%}}</style></head><body>'+
            '<iframe id="__pf" style="width:100%;height:100%;border:0"></iframe>'+
            '<script>var f=document.getElementById("__pf");f.onload=function(){{try{{f.contentWindow.focus();f.contentWindow.print();}}catch(e){{}}}};f.src="'+url+'#zoom=page-width";<\/script>'+
            '</body></html>');
          w.document.close();
        }} catch(e) {{ alert('Falha ao preparar impressão: '+e); }}
      }}
    </script>
    """
    st.components.v1.html(html, height=74)

# ============================== Uploader ===============================
st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")
up_help = "Carregue 1 PDF (ou vários em modo lote)."
_uploader_key = f"uploader_{'multi' if BATCH_MODE else 'single'}_{s['uploader_key']}"

if BATCH_MODE:
    uploaded_files = st.file_uploader("📁 PDF(s)", type=["pdf"], accept_multiple_files=True,
                                      key=_uploader_key, help=up_help)
else:
    up1 = st.file_uploader("📁 PDF (1 arquivo)", type=["pdf"], accept_multiple_files=False,
                           key=_uploader_key, help=up_help)
    uploaded_files = [up1] if up1 is not None else []

# ============================ Função Visão Geral ============================
def render_overview_and_tables(df_view: pd.DataFrame, stats_cp_idade: pd.DataFrame, TOL_MP: float):
    import pandas as _pd
    from datetime import datetime as _dt

    st.markdown("#### Visão Geral")

    def _format_float_label(value: Optional[float]) -> str:
        if value is None or _pd.isna(value): return "—"
        num = float(value); label = f"{num:.2f}".rstrip("0").rstrip(".")
        return label or f"{num:.2f}"

    def _to_date(d):
        try: return _dt.strptime(str(d), "%d/%m/%Y").date()
        except Exception: return None

    obra_label = "—"; data_label = "—"; fck_label = "—"

    if not df_view.empty:
        ob = sorted(set(df_view["Obra"].astype(str)))
        obra_label = ob[0] if len(ob) == 1 else f"Múltiplas ({len(ob)})"
        fck_candidates: List[str] = []
        for raw in df_view["Fck Projeto"].tolist():
            normalized = _to_float_or_none(raw)
            if normalized is not None:
                formatted = _format_float_label(normalized)
                if formatted != "—": fck_candidates.append(formatted)
            else:
                raw_str = str(raw).strip()
                if raw_str and raw_str.lower() != "nan": fck_candidates.append(raw_str)
        if fck_candidates: fck_label = ", ".join(dict.fromkeys(fck_candidates))
        datas_validas = [_to_date(x) for x in df_view["Data Certificado"].unique()]
        datas_validas = [d for d in datas_validas if d is not None]
        if datas_validas:
            di, df_ = min(datas_validas), max(datas_validas)
            data_label = di.strftime('%d/%m/%Y') if di == df_ else f"{di.strftime('%d/%m/%Y')} — {df_.strftime('%d/%m/%Y')}"

    def _fmt_pct(v): return "--" if v is None else f"{v:.0f}%"

    fck_series_all = _pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
    fck_val = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None
    KPIs = compute_exec_kpis(df_view, fck_val)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Obra</div>'
            f'<div class="h-kpi">{obra_label}</div></div>',
            unsafe_allow_html=True
        )
    with k2:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Data da moldagem</div>'
            f'<div class="h-kpi">{data_label}</div></div>',
            unsafe_allow_html=True
        )
    with k3:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">fck de projeto (MPa)</div>'
            f'<div class="h-kpi">{fck_label}</div></div>',
            unsafe_allow_html=True
        )
    with k4:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Tolerância aplicada (MPa)</div>'
            f'<div class="h-kpi">±{TOL_MP:.1f}</div></div>',
            unsafe_allow_html=True
        )
    with k5:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">CPs ≥ fck aos 28d</div>'
            f'<div class="h-kpi">{_fmt_pct(KPIs["pct28"])}</div></div>',
            unsafe_allow_html=True
        )
    with k6:
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">CPs ≥ fck aos 63d</div>'
            f'<div class="h-kpi">{_fmt_pct(KPIs["pct63"])}</div></div>',
            unsafe_allow_html=True
        )

    e1, e2, e3, e4 = st.columns(4)
    with e1:
        media_txt = "--" if KPIs["media"] is None else f"{KPIs['media']:.1f} MPa"
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Média geral</div>'
            f'<div class="h-kpi">{media_txt}</div></div>',
            unsafe_allow_html=True
        )
    with e2:
        dp_txt = "--" if KPIs["dp"] is None else f"{KPIs['dp']:.1f}"
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Desvio-padrão</div>'
            f'<div class="h-kpi">{dp_txt}</div></div>',
            unsafe_allow_html=True
        )
    with e3:
        n_relatorios = df_view["Relatório"].nunique()
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Relatórios lidos</div>'
            f'<div class="h-kpi">{n_relatorios}</div></div>',
            unsafe_allow_html=True
        )
    with e4:
        snf = _pd.to_numeric(df_view.get("Abatimento NF (mm)"), errors="coerce")
        stol = _pd.to_numeric(df_view.get("Abatimento NF tol (mm)"), errors="coerce") \
            if "Abatimento NF tol (mm)" in df_view.columns else _pd.Series(dtype=float)
        abat_nf_label = "—"
        if snf is not None and not snf.dropna().empty:
            v = float(snf.dropna().mode().iloc[0])
            if stol is not None and not stol.dropna().empty:
                t = float(stol.dropna().mode().iloc[0])
                abat_nf_label = f"{v:.0f} ± {t:.0f} mm"
            else:
                abat_nf_label = f"{v:.0f} mm"
        st.markdown(
            f'<div class="h-card"><div class="h-kpi-label">Abatimento NF</div>'
            f'<div class="h-kpi">{abat_nf_label}</div></div>',
            unsafe_allow_html=True
        )

    p28 = KPIs.get("pct28"); p63 = KPIs.get("pct63")
    score = None
    if (p28 is not None) or (p63 is not None):
        score = (0 if p28 is None else 0.6 * p28) + (0 if p63 is None else 0.4 * p63)

    def _hits(df_src, age, fck):
        if fck is None or _pd.isna(fck): return (0, 0)
        sub = df_src[df_src["Idade (dias)"] == age].groupby("CP")["Resistência (MPa)"].mean()
        return int((sub >= fck).sum()), int(sub.shape[0])

    h28, t28 = _hits(df_view, 28, fck_val)
    h63, t63 = _hits(df_view, 63, fck_val)

    st.markdown(f"<div class='pill' style='margin:8px 0 2px 0; color:{KPIs['status_cor']}; font-weight:800'>{KPIs['status_txt']}</div>", unsafe_allow_html=True)
    explic_html = (
        "<div class='kpi-help' style='margin:8px 0 14px 0; line-height:1.45'>"
        "<div style='display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px'>"
        "<span class='pill'>Cálculo do semáforo</span><span class='pill'>28d = 60%</span><span class='pill'>63d = 40%</span>"
        "</div>"
        f"<div style='font-size:13px'>"
        f"<div>28 dias: <b>{'--' if p28 is None else f'{p28:.0f}%'}</b> ({h28}/{t28} CPs ≥ fck)</div>"
        f"<div>63 dias: <b>{'--' if p63 is None else f'{p63:.0f}%'}</b> ({h63}/{t63} CPs ≥ fck)</div>"
        f"<div style='margin-top:6px'>Score ponderado = <b>{'-' if score is None else f'{score:.0f}%'}</b> "
        f"&rarr; <b style='color:{KPIs['status_cor']}'>{KPIs['status_txt']}</b></div>"
        "<div style='margin-top:4px'>Faixas: <b>≥90</b> ✅Bom • <b>≥75</b> ⚠️Atenção • <b>&lt;75</b> 🔴Crítico.</div>"
        "</div></div>"
    )
    st.markdown(explic_html, unsafe_allow_html=True)

    # Tabelas base
    st.write("#### Resultados Individuais")
    st.dataframe(df_view, use_container_width=True)
    st.write("#### Estatísticas por CP")
    st.dataframe(stats_cp_idade, use_container_width=True)
# ========================== Helpers de nome de arquivo =========================
def _slugify_for_filename(text: str) -> str:
    import unicodedata, re as _re
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    t = _re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t or "relatorio"

def _safe_mode(series: pd.Series):
    if series is None or series.dropna().empty:
        return None
    try:
        m = series.mode()
        return None if m.empty else m.iat[0]
    except Exception:
        return series.dropna().iloc[0]

def _to_date_obj(d: str):
    from datetime import datetime as _dt
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return _dt.strptime(str(d), fmt).date()
        except Exception:
            pass
    return None

def _dd_mm_aaaa(d) -> str:
    try:
        return f"{int(d.day):02d}_{int(d.month):02d}_{int(d.year):04d}"
    except Exception:
        return ""

def _extract_rel_tail_from_files(uploaded_files: list) -> str | None:
    import re as _re
    for f in uploaded_files or []:
        fname = (getattr(f, "name", "") or "").lower()
        m = _re.search(r"(\d{3,6})[_\-]([0-9]{1,2}d)[_\-](\d{2}[_\-]\d{2}[_\-]\d{4})", fname)
        if m:
            rid = int(m.group(1)) % 1000
            return f"{rid:03d}_{m.group(2)}_{m.group(3).replace('-', '_')}"
        m2 = _re.search(r"(\d{3,6})", fname)
        if m2:
            rid = int(m2.group(1)) % 1000
            return f"{rid:03d}"
    return None

def _extract_rel_tail_from_df(df_view: pd.DataFrame) -> str | None:
    import re as _re
    if "Relatório" not in df_view.columns or df_view["Relatório"].dropna().empty:
        return None
    rel_mode = str(_safe_mode(df_view["Relatório"]))
    m = _re.search(r"(\d{3,})", rel_mode)
    if m:
        rid = int(m.group(1)) % 1000
        return f"{rid:03d}"
    return None

def _extract_age_token(df_view: pd.DataFrame) -> str | None:
    if "Idade (dias)" not in df_view.columns or df_view["Idade (dias)"].dropna().empty:
        return None
    ages = pd.to_numeric(df_view["Idade (dias)"], errors="coerce").dropna().astype(int)
    if ages.empty: return None
    age = _safe_mode(ages)
    return f"{int(age)}d" if age is not None else None

def _extract_cert_date_token(df_view: pd.DataFrame) -> str | None:
    if "Data Certificado" not in df_view.columns:
        return None
    dates = [_to_date_obj(x) for x in df_view["Data Certificado"].dropna().unique().tolist()]
    dates = [d for d in dates if d is not None]
    if not dates: return None
    return _dd_mm_aaaa(min(dates))

def build_pdf_filename(df_view: pd.DataFrame, uploaded_files: list) -> str:
    if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty:
        obra = _safe_mode(df_view["Obra"].astype(str)) or "Obra"
    else:
        obra = "Obra"
    obra_slug = _slugify_for_filename(obra)

    rel_tail = _extract_rel_tail_from_files(uploaded_files)
    age_tok  = _extract_age_token(df_view) or ""
    date_tok = _extract_cert_date_token(df_view) or ""

    if rel_tail and "_" in rel_tail and rel_tail.count("_") >= 2:
        final_tail = rel_tail
    else:
        rrr = rel_tail if (rel_tail and rel_tail.isdigit() and len(rel_tail) == 3) else (_extract_rel_tail_from_df(df_view) or "")
        tail_parts = [p for p in [rrr, age_tok, date_tok] if p]
        final_tail = "_".join(tail_parts)

    base = f"Relatorio_analise_certificado_obra_{obra_slug}"
    if final_tail:
        return f"{base}_{final_tail}.pdf"
    if date_tok:
        return f"{base}_{date_tok}.pdf"
    from datetime import datetime as _dt
    return f"{base}_{_dt.utcnow().strftime('%d_%m_%Y')}.pdf"

# ================================ PIPELINE ================================
if uploaded_files:
    frames = []
    for f in uploaded_files:
        if f is None: continue
        df_i, obra_i, data_i, fck_i = extrair_dados_certificado(f)
        if not df_i.empty:
            df_i["Data Certificado"] = data_i
            df_i["Obra"] = obra_i
            if "Fck Projeto" in df_i.columns:
                scalar_fck = _to_float_or_none(fck_i)
                if scalar_fck is not None:
                    df_i["Fck Projeto"] = pd.to_numeric(df_i["Fck Projeto"], errors="coerce").fillna(float(scalar_fck))
            else:
                df_i["Fck Projeto"] = fck_i
            df_i["Arquivo"] = getattr(f, "name", "arquivo.pdf")
            frames.append(df_i)
            log_event("file_parsed", {
                "file": getattr(f, "name", "arquivo.pdf"),
                "rows": int(df_i.shape[0]),
                "relatorios": int(df_i["Relatório"].nunique()),
                "obra": obra_i,
                "data_cert": data_i,
            })

    if not frames:
        st.error("⚠️ Não encontrei CPs válidos nos PDFs enviados.")
    else:
        df = pd.concat(frames, ignore_index=True)

        # ===== Validações cruzadas NF/Relatório e CP/Relatório
        if not df.empty:
            nf_rel = df.dropna(subset=["Nota Fiscal","Relatório"]).astype({"Relatório": str})
            nf_multi = (nf_rel.groupby(["Nota Fiscal"])["Relatório"]
                        .nunique().reset_index(name="n_rel"))
            viol_nf = nf_multi[nf_multi["n_rel"] > 1]["Nota Fiscal"].tolist()
            if viol_nf:
                detalhes = (nf_rel[nf_rel["Nota Fiscal"].isin(viol_nf)]
                            .groupby(["Nota Fiscal","Relatório"])["CP"].nunique().reset_index()
                           )
                st.error("🚨 **Nota Fiscal repetida em relatórios diferentes!**")
                st.dataframe(detalhes.rename(columns={"CP":"#CPs distintos"}), use_container_width=True)
                try:
                    log_event("violation_nf_duplicate", {
                        "nf_list": list(map(str, viol_nf)),
                        "details": detalhes.to_dict(orient="records")
                    }, level="WARN")
                except Exception:
                    pass

            cp_rel = df.dropna(subset=["CP","Relatório"]).astype({"Relatório": str})
            cp_multi = (cp_rel.groupby(["CP"])["Relatório"]
                        .nunique().reset_index(name="n_rel"))
            viol_cp = cp_multi[cp_multi["n_rel"] > 1]["CP"].tolist()
            if viol_cp:
                detalhes_cp = (cp_rel[cp_rel["CP"].isin(viol_cp)]
                               .groupby(["CP","Relatório"])["Idade (dias)"].count().reset_index(name="#leituras"))
                st.error("🚨 **CP repetido em relatórios diferentes!**")
                st.dataframe(detalhes_cp, use_container_width=True)
                try:
                    log_event("violation_cp_duplicate", {
                        "cp_list": list(map(str, viol_cp)),
                        "details": detalhes_cp.to_dict(orient="records")
                    }, level="WARN")
                except Exception:
                    pass

        # ---------------- Filtros
        st.markdown("#### Filtros")
        fc1, fc2, fc3 = st.columns([2.0, 2.0, 1.0])
        with fc1:
            rels = sorted(df["Relatório"].astype(str).unique())
            sel_rels = st.multiselect("Relatórios", rels, default=rels)

        def to_date(d):
            try: return datetime.strptime(str(d), "%d/%m/%Y").date()
            except Exception: return None

        df["_DataObj"] = df["Data Certificado"].apply(to_date)
        valid_dates = [d for d in df["_DataObj"] if d is not None]
        with fc2:
            if valid_dates:
                dmin, dmax = min(valid_dates), max(valid_dates)
                dini, dfim = st.date_input("Intervalo de data do certificado", (dmin, dmax))
            else:
                dini, dfim = None, None
        with fc3:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Limpar filtros / Novo upload", use_container_width=True):
                s["uploader_key"] += 1
                st.rerun()

        mask = df["Relatório"].astype(str).isin(sel_rels)
        if valid_dates and dini and dfim:
            mask = mask & df["_DataObj"].apply(lambda d: d is not None and dini <= d <= dfim)
        df_view = df.loc[mask].drop(columns=["_DataObj"]).copy()

        # --- Gestão de múltiplos fck (banner legível) ---
        df_view["_FckLabel"] = df_view["Fck Projeto"].apply(_normalize_fck_label)
        fck_labels = list(dict.fromkeys(df_view["_FckLabel"]))
        multiple_fck_detected = len(fck_labels) > 1

        if multiple_fck_detected:
            st.markdown("""
            <style>
              .hb-multifck {
                display:flex; align-items:center; gap:10px;
                background:#FFF3CD;
                border:1px solid #F59E0B;
                color:#111827;
                padding:12px 14px; border-radius:12px;
                font-weight:700; line-height:1.35;
                box-shadow:0 2px 8px rgba(0,0,0,.06);
                margin: 4px 0 8px 0;
              }
              .hb-multifck .dot {
                width:10px; height:10px; border-radius:999px; background:#F59E0B;
                flex:0 0 auto;
              }
            </style>
            <div class="hb-multifck">
              <span class="dot"></span>
              Detectamos <b>múltiplos fck</b> no conjunto selecionado. Escolha qual deseja analisar.
            </div>
            """, unsafe_allow_html=True)

            selected_fck_label = st.selectbox(
                "fck para análise", fck_labels,
                format_func=lambda lbl: lbl if lbl != "—" else "Não informado"
            )
            df_view = df_view[df_view["_FckLabel"] == selected_fck_label].copy()
        else:
            selected_fck_label = fck_labels[0] if fck_labels else "—"

        if df_view.empty:
            st.info("Nenhum dado disponível para o fck selecionado.")
            st.stop()

        df_view = df_view.drop(columns=["_FckLabel"], errors="ignore")

        # ===== Estatística por CP/Idade
        stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                  .agg(Média="mean", Desvio_Padrão="std", n="count").reset_index()
        )

        # ===== VISÃO GERAL
        render_overview_and_tables(df_view, stats_cp_idade, TOL_MP)

        # ---------------- Gráficos
        st.markdown("---")
        st.markdown("### Gráficos")
        st.sidebar.subheader("🎯 Foco nos gráficos")
        cp_foco_manual = st.sidebar.text_input("Digitar CP p/ gráficos (opcional)", "", key="cp_manual")
        cp_select = st.sidebar.selectbox("CP para gráficos", ["(Todos)"] + sorted(df_view["CP"].astype(str).unique()),
                                         key="cp_select")
        cp_focus = (cp_foco_manual.strip() or (cp_select if cp_select != "(Todos)" else "")).strip()
        df_plot = df_view[df_view["CP"].astype(str) == cp_focus].copy() if cp_focus else df_view.copy()

        fck_series_focus = pd.to_numeric(df_plot["Fck Projeto"], errors="coerce").dropna()
        fck_series_all_g = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active = float(fck_series_focus.mode().iloc[0]) if not fck_series_focus.empty else (
            float(fck_series_all_g.mode().iloc[0]) if not fck_series_all_g.empty else None
        )

        stats_all_focus = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index()

        # ===== Gráfico 1 — Crescimento Real
        st.write("##### Gráfico 1 — Crescimento da Resistência (Real)")
        fig1, ax = plt.subplots(figsize=(9.6, 4.9))
        for cp, sub in df_plot.groupby("CP"):
            sub = sub.sort_values("Idade (dias)")
            ax.plot(sub["Idade (dias)"], sub["Resistência (MPa)"], marker="o", linewidth=1.6, label=f"CP {cp}")
        sa_dp = stats_all_focus[stats_all_focus["count"] >= 2].copy()
        if not sa_dp.empty:
            ax.plot(sa_dp["Idade (dias)"], sa_dp["mean"], linewidth=2.2, marker="s", label="Média")
        _sdp = sa_dp.dropna(subset=["std"]).copy()
        if not _sdp.empty:
            ax.fill_between(_sdp["Idade (dias)"], _sdp["mean"] - _sdp["std"], _sdp["mean"] + _sdp["std"], alpha=0.2, label="±1 DP")
        if fck_active is not None:
            ax.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
        ax.set_xlabel("Idade (dias)"); ax.set_ylabel("Resistência (MPa)")
        ax.set_title("Crescimento da resistência por corpo de prova")
        place_right_legend(ax)
        ax.grid(True, linestyle="--", alpha=0.35); ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        st.pyplot(fig1)

        # ===== Gráfico 2 — Curva Estimada
        st.write("##### Gráfico 2 — Curva Estimada (Referência técnica)")
        fig2, est_df = None, None
        fck28 = df_plot.loc[df_plot["Idade (dias)"] == 28, "Resistência (MPa)"].mean()
        fck7  = df_plot.loc[df_plot["Idade (dias)"] == 7,  "Resistência (MPa)"].mean()
        if pd.notna(fck28):
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [fck28*0.65, fck28, fck28*1.15]})
        elif pd.notna(fck7):
            _f28 = fck7 / 0.70
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [float(fck7), float(_f28), float(_f28)*1.15]})
        if est_df is not None:
            fig2, ax2 = plt.subplots(figsize=(7.8, 4.8))
            ax2.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Curva Estimada")
            for x, y in zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]): ax2.text(x, y, f"{y:.1f}", ha="center", va="bottom", fontsize=9)
            ax2.set_title("Curva estimada (referência técnica, não critério normativo)")
            ax2.set_xlabel("Idade (dias)"); ax2.set_ylabel("Resistência (MPa)")
            place_right_legend(ax2); ax2.grid(True, linestyle="--", alpha=0.5)
            st.pyplot(fig2)
        else:
            st.info("Não foi possível calcular a curva estimada (sem médias em 7 ou 28 dias).")

        # ===== Gráfico 3 — Comparação médias
        st.write("##### Gráfico 3 — Comparação Real × Estimado (médias)")
        fig3, cond_df, verif_fck_df = None, None, None
        mean_by_age = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].mean()
        m7  = mean_by_age.get(7,  float("nan"))
        m28 = mean_by_age.get(28, float("nan"))
        m63 = mean_by_age.get(63, float("nan"))

        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [7, 28, 63],
            "Média Real (MPa)": [m7, m28, m63],
            "fck Projeto (MPa)": [
                float("nan"),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
            ],
        })

        if est_df is not None:
            sa = stats_all_focus.copy(); sa["std"] = sa["std"].fillna(0.0)
            fig3, ax3 = plt.subplots(figsize=(9.6, 4.9))
            ax3.plot(sa["Idade (dias)"], sa["mean"], marker="s", linewidth=2, label=("Média (CP focado)" if cp_focus else "Média Real"))
            _sa_dp = sa[sa["count"] >= 2].copy()
            if not _sa_dp.empty:
                ax3.fill_between(_sa_dp["Idade (dias)"], _sa_dp["mean"] - _sa_dp["std"], _sa_dp["mean"] + _sa_dp["std"], alpha=0.2, label="Real ±1 DP")
            ax3.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Estimado")
            if fck_active is not None:
                ax3.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
            ax3.set_xlabel("Idade (dias)"); ax3.set_ylabel("Resistência (MPa)")
            ax3.set_title("Comparação Real × Estimado (médias)")
            place_right_legend(ax3); ax3.grid(True, linestyle="--", alpha=0.5)
            st.pyplot(fig3)

            def _status_row(delta, tol):
                if pd.isna(delta): return "⚪ Sem dados"
                if abs(delta) <= tol: return "✅ Dentro dos padrões"
                return "🔵 Acima do padrão" if delta > 0 else "🔴 Abaixo do padrão"

            _TOL = float(TOL_MP)
            cond_df = pd.DataFrame({
                "Idade (dias)": [7, 28, 63],
                "Média Real (MPa)": [
                    sa.loc[sa["Idade (dias)"] == 7,  "mean"].mean(),
                    sa.loc[sa["Idade (dias)"] == 28, "mean"].mean(),
                    sa.loc[sa["Idade (dias)"] == 63, "mean"].mean(),
                ],
                "Estimado (MPa)": est_df.set_index("Idade (dias)")["Resistência (MPa)"].reindex([7, 28, 63]).values
            })
            cond_df["Δ (Real-Est.)"] = cond_df["Média Real (MPa)"] - cond_df["Estimado (MPa)"]
            cond_df["Status"] = [_status_row(d, _TOL) for d in cond_df["Δ (Real-Est.)"]]
            st.write("#### 📊 Condição Real × Estimado (médias)")
            st.dataframe(cond_df, use_container_width=True)
        else:
            st.info("Sem curva estimada → não é possível comparar médias (Gráfico 3).")

        # ===== Gráfico 4 — Pareamento ponto-a-ponto
        st.write("##### Gráfico 4 — Real × Estimado ponto-a-ponto (sem médias)")
        fig4, pareamento_df = None, None
        if 'est_df' in locals() and est_df is not None and not est_df.empty:
            est_map = dict(zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]))
            pares = []
            for cp, sub in df_plot.groupby("CP"):
                for _, r in sub.iterrows():
                    idade = int(r["Idade (dias)"])
                    if idade in est_map:
                        real = float(r["Resistência (MPa)"]); est  = float(est_map[idade]); delta = real - est
                        _TOL = float(TOL_MP)
                        status = "✅ OK" if abs(delta) <= _TOL else ("🔵 Acima" if delta > 0 else "🔴 Abaixo")
                        pares.append([str(cp), idade, real, est, delta, status])
            pareamento_df = pd.DataFrame(pares, columns=["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]).sort_values(["CP","Idade (dias)"])
            fig4, ax4 = plt.subplots(figsize=(10.2, 5.0))
            for cp, sub in df_plot.groupby("CP"):
                sub = sub.sort_values("Idade (dias)")
                x = sub["Idade (dias)"].tolist(); y_real = sub["Resistência (MPa)"].tolist()
                x_est = [i for i in x if i in est_map]; y_est = [est_map[i] for i in x_est]
                ax4.plot(x, y_real, marker="o", linewidth=1.6, label=f"CP {cp} — Real")
                if x_est:
                    ax4.plot(x_est, y_est, marker="^", linestyle="--", linewidth=1.6, label=f"CP {cp} — Est.")
                    for xx, yr, ye in zip(x_est, [rv for i, rv in zip(x, y_real) if i in est_map], y_est):
                        ax4.vlines(xx, min(yr, ye), max(yr, ye), linestyles=":", linewidth=1)
            if fck_active is not None:
                ax4.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
            ax4.set_xlabel("Idade (dias)"); ax4.set_ylabel("Resistência (MPa)")
            ax4.set_title("Pareamento Real × Estimado por CP (sem médias)")
            place_right_legend(ax4); ax4.grid(True, linestyle="--", alpha=0.5)
            st.pyplot(fig4)
            st.write("#### 📑 Pareamento ponto-a-ponto")
            st.dataframe(pareamento_df, use_container_width=True)
        else:
            st.info("Sem curva estimada → não é possível parear pontos (Gráfico 4).")

        # ===== Verificação do fck (Resumo)
        st.write("#### ✅ Verificação do fck de Projeto")
        fck_series_all = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active2 = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None

        mean_by_age = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].mean()
        m7  = mean_by_age.get(7,  float("nan"))
        m28 = mean_by_age.get(28, float("nan"))
        m63 = mean_by_age.get(63, float("nan"))
        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [7, 28, 63],
            "Média Real (MPa)": [m7, m28, m63],
            "fck Projeto (MPa)": [float("nan"), (fck_active2 if fck_active2 is not None else float("nan")), (fck_active2 if fck_active2 is not None else float("nan"))],
        })
        resumo_status = []
        for idade, media, fckp in verif_fck_df.itertuples(index=False):
            if idade == 7:
                resumo_status.append("🟡 Informativo (7d)")
            else:
                if pd.isna(media) or pd.isna(fckp):
                    resumo_status.append("⚪ Sem dados")
                else:
                    resumo_status.append("🟢 Atingiu fck" if float(media) >= float(fckp) else "🔴 Não atingiu fck")
        verif_fck_df["Status"] = resumo_status
        st.dataframe(verif_fck_df, use_container_width=True)

        # ===== Verificação detalhada por CP (pares Δ>2MPa)
        st.markdown("#### ✅ Verificação detalhada por CP (7/28/63 dias)")
        pv_cp_status = None
        tmp_v = df_view[df_view["Idade (dias)"].isin([7, 28, 63])].copy()
        if tmp_v.empty:
            st.info("Sem CPs de 7/28/63 dias no filtro atual.")
        else:
            tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
            tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1

            pv_multi = tmp_v.pivot_table(index="CP", columns=["Idade (dias)", "rep"], values="MPa", aggfunc="first").sort_index(axis=1)

            for age in [7, 28, 63]:
                if age not in pv_multi.columns.get_level_values(0):
                    pv_multi[(age, 1)] = pd.NA

            ordered = []
            for age in [7, 28, 63]:
                reps = sorted([r for (a, r) in pv_multi.columns if a == age])
                for r in reps: ordered.append((age, r))
            pv_multi = pv_multi.reindex(columns=ordered)

            def _flat(age, rep):
                base = f"{age}d"
                return f"{base} (MPa)" if rep == 1 else f"{base} #{rep} (MPa)"

            pv = pv_multi.copy(); pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
            pv = pv.reset_index()

            try:
                pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
            except Exception:
                pv["__cp_sort__"] = range(len(pv))
            pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")

            fck_series_focus2 = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
            fck_active2 = float(fck_series_focus2.mode().iloc[0]) if not fck_series_focus2.empty else None

            def _status_text_media(media_idade, age, fckp):
                if pd.isna(media_idade) or (fckp is None) or pd.isna(fckp): return "⚪ Sem dados"
                if age == 7: return "🟡 Informativo (7d)"
                return "🟢 Atingiu fck" if float(media_idade) >= float(fckp) else "🔴 Não atingiu fck"

            media_7  = pv_multi[7].mean(axis=1)  if 7  in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)
            media_63 = pv_multi[63].mean(axis=1) if 63 in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)

            if 28 in pv_multi.columns.get_level_values(0) and (fck_active2 is not None) and not pd.isna(fck_active2):
                cols28 = pv_multi[28]
                def _all_reps_ok(row):
                    vals = row.dropna().astype(float)
                    if vals.empty: return None
                    return bool((vals >= float(fck_active2)).all())
                ok28 = cols28.apply(_all_reps_ok, axis=1)
            else:
                ok28 = pd.Series([None] * pv_multi.shape[0], index=pv_multi.index)

            def _status_from_ok(ok):
                if ok is None: return "⚪ Sem dados"
                return "🟢 Atingiu fck" if ok else "🔴 Não atingiu fck"

            status_df = pd.DataFrame({
                "Status 7d":  [ _status_text_media(v, 7,  fck_active2) for v in media_7.reindex(pv_multi.index) ],
                "Status 28d": [ _status_from_ok(v) for v in ok28.reindex(pv_multi.index) ],
                "Status 63d": [ _status_text_media(v, 63, fck_active2) for v in media_63.reindex(pv_multi.index) ],
            }, index=pv_multi.index)

            def _delta_flag(row_vals: pd.Series) -> bool:
                vals = pd.to_numeric(row_vals.dropna(), errors="coerce").dropna().astype(float)
                if vals.empty: return False
                return (vals.max() - vals.min()) > 2.0

            alerta_pares = []
            for idx in pv_multi.index:
                flag = False
                for age in [7, 28, 63]:
                    cols = [c for c in pv_multi.columns if c[0] == age]
                    if not cols: continue
                    series_age = pv_multi.loc[idx, cols]
                    if _delta_flag(series_age):
                        flag = True; break
                alerta_pares.append("🟠 Δ pares > 2 MPa" if flag else "")

            pv = pv.merge(status_df, left_on="CP", right_index=True, how="left")
            pv["Alerta Pares (Δ>2 MPa)"] = alerta_pares

            cols_cp = ["CP"]
            cols_7  = [c for c in pv.columns if c.startswith("7d")]
            cols_28 = [c for c in pv.columns if c.startswith("28d")]
            cols_63 = [c for c in pv.columns if c.startswith("63d")]

            ordered_cols = (
                cols_cp + cols_7 + (["Status 7d"] if "Status 7d" in pv.columns else []) +
                cols_28 + (["Status 28d"] if "Status 28d" in pv.columns else []) +
                cols_63 + (["Status 63d"] if "Status 63d" in pv.columns else []) +
                ["Alerta Pares (Δ>2 MPa)"]
            )
            pv = pv[ordered_cols].rename(columns={"Status 7d":"7 dias — Status", "Status 28d":"28 dias — Status", "Status 63d":"63 dias — Status"})
            pv_cp_status = pv.copy()
            st.dataframe(pv_cp_status, use_container_width=True)

        # =============================================================================
        # PDF — Cabeçalho + gráficos + detalhamento CP
        # =============================================================================
        def _usina_label_from_df(df_: pd.DataFrame) -> str:
            if "Usina" not in df_.columns: return "—"
            seri = df_["Usina"].dropna().astype(str)
            if seri.empty: return "—"
            m = seri.mode()
            return str(m.iat[0]) if not m.empty else "—"

        def _abat_nf_header_label(df_: pd.DataFrame) -> str:
            snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
            stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
            if snf.empty: return "—"
            v = float(snf.mode().iloc[0]); t = float(stol.mode().iloc[0]) if not stol.empty else 0.0
            return f"{v:.0f} ± {t:.0f} mm"

        def _doc_id() -> str: return "HAB-" + datetime.now().strftime("%Y%m%d-%H%M%S")

        def gerar_pdf(df: pd.DataFrame, stats: pd.DataFrame, fig1, fig2, fig3, fig4,
                      obra_label: str, data_label: str, fck_label: str,
                      verif_fck_df: Optional[pd.DataFrame],
                      cond_df: Optional[pd.DataFrame],
                      pareamento_df: Optional[pd.DataFrame],
                      pv_cp_status: Optional[pd.DataFrame],
                      qr_url: str) -> bytes:
            use_landscape = (len(df.columns) >= 8)
            pagesize = landscape(A4) if use_landscape else A4
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=pagesize, leftMargin=18, rightMargin=18, topMargin=26, bottomMargin=56)
            styles = getSampleStyleSheet()
            styles["Title"].fontName = "Helvetica-Bold"; styles["Title"].fontSize = 18
            styles["Heading2"].fontName = "Helvetica-Bold"; styles["Heading2"].fontSize = 14
            styles["Heading3"].fontName = "Helvetica-Bold"; styles["Heading3"].fontSize = 12
            styles["Normal"].fontName = "Helvetica"; styles["Normal"].fontSize = 9
            story = []

            story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
            story.append(Paragraph("Relatório de Rompimento de Corpos de Prova", styles['Heading2']))
            usina_hdr = _usina_label_from_df(df); abat_nf_hdr = _abat_nf_header_label(df)
            story.append(Paragraph(f"Obra: {obra_label}", styles['Normal']))
            story.append(Paragraph(f"Período (datas dos certificados): {data_label}", styles['Normal']))
            story.append(Paragraph(f"fck de projeto: {fck_label}", styles['Normal']))
            story.append(Paragraph(f"Usina: {usina_hdr}", styles['Normal']))
            story.append(Paragraph(f"Abatimento de NF: {abat_nf_hdr}", styles['Normal']))
            if qr_url: story.append(Paragraph(f"Resumo/QR: {qr_url}", styles['Normal']))
            story.append(Spacer(1, 8))

            headers = ["Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local","Usina","Abatimento NF (mm)","Abatimento Obra (mm)"]
            rows = df[headers].values.tolist()
            table = Table([headers] + rows, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                ("GRID",(0,0),(-1,-1),0.5,colors.black),
                ("ALIGN",(0,0),(-1,-1),"CENTER"),
                ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                ("FONTSIZE",(0,0),(-1,-1),8.5),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
                ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
            ]))
            story.append(table); story.append(Spacer(1, 8))

            if not stats.empty:
                from copy import deepcopy
                stt = [["CP","Idade (dias)","Média","DP","n"]] + deepcopy(stats).values.tolist()
                story.append(Paragraph("Resumo Estatístico (Média + DP)", styles['Heading3']))
                t2 = Table(stt, repeatRows=1)
                t2.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(t2); story.append(Spacer(1, 10))

            def _img_from_fig_pdf(_fig, w=620, h=420):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                _fig.savefig(tmp.name, dpi=200, bbox_inches="tight")
                return RLImage(tmp.name, width=w, height=h)

            if fig1: story.append(_img_from_fig_pdf(fig1, w=640, h=430)); story.append(Spacer(1, 8))
            if fig2: story.append(_img_from_fig_pdf(fig2, w=600, h=400)); story.append(Spacer(1, 8))
            if fig3: story.append(_img_from_fig_pdf(fig3, w=640, h=430)); story.append(Spacer(1, 8))
            if fig4: story.append(_img_from_fig_pdf(fig4, w=660, h=440)); story.append(Spacer(1, 8))

            if verif_fck_df is not None and not verif_fck_df.empty:
                story.append(PageBreak())
                story.append(Paragraph("Verificação do fck de Projeto (Resumo por idade)", styles["Heading3"]))
                rows_v = [["Idade (dias)","Média Real (MPa)","fck Projeto (MPa)","Status"]]
                for _, r in verif_fck_df.iterrows():
                    rows_v.append([
                        r["Idade (dias)"],
                        f"{r['Média Real (MPa)']:.3f}" if pd.notna(r['Média Real (MPa)']) else "—",
                        f"{r.get('fck Projeto (MPa)', float('nan')):.3f}" if pd.notna(r.get('fck Projeto (MPa)', float('nan'))) else "—",
                        r.get("Status","—")
                    ])
                tv = Table(rows_v, repeatRows=1)
                tv.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tv); story.append(Spacer(1, 8))

            if cond_df is not None and not cond_df.empty:
                story.append(Paragraph("Condição Real × Estimado (médias)", styles["Heading3"]))
                rows_c = [["Idade (dias)","Média Real (MPa)","Estimado (MPa)","Δ (Real-Est.)","Status"]]
                for _, r in cond_df.iterrows():
                    rows_c.append([
                        r["Idade (dias)"],
                        f"{r['Média Real (MPa)']:.3f}" if pd.notna(r['Média Real (MPa)']) else "—",
                        f"{r['Estimado (MPa)']:.3f}" if pd.notna(r['Estimado (MPa)']) else "—",
                        f"{r['Δ (Real-Est.)']:.3f}" if pd.notna(r['Δ (Real-Est.)']) else "—",
                        r["Status"]
                    ])
                tc = Table(rows_c, repeatRows=1)
                tc.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tc); story.append(Spacer(1, 8))

            if pareamento_df is not None and not pareamento_df.empty:
                story.append(Paragraph("Pareamento ponto-a-ponto (Real × Estimado, sem médias)", styles["Heading3"]))
                head = ["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]
                rows_p = pareamento_df[head].values.tolist()
                tp = Table([head] + rows_p, repeatRows=1)
                tp.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tp); story.append(Spacer(1, 8))

            if pv_cp_status is not None and not pv_cp_status.empty:
                story.append(PageBreak())
                story.append(Paragraph("Verificação detalhada por CP (7/28/63 dias)", styles["Heading3"]))
                cols = list(pv_cp_status.columns); tab = [cols] + pv_cp_status.values.tolist()
                t_det = Table(tab, repeatRows=1)
                t_det.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.4,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.2),
                    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                    ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),
                    ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1),
                ]))
                story.append(t_det); story.append(Spacer(1, 6))

            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>ID do documento:</b> {_doc_id()}", styles["Normal"]))

            doc.build(story, canvasmaker=NumberedCanvas)
            pdf = buffer.getvalue(); buffer.close(); return pdf

        # ===== PDF / Exportações (somente admin)
        has_df = isinstance(df_view, pd.DataFrame) and (not df_view.empty)
        if has_df and CAN_EXPORT:
            try:
                pdf_bytes = gerar_pdf(
                    df_view, stats_cp_idade,
                    fig1 if 'fig1' in locals() else None,
                    fig2 if 'fig2' in locals() else None,
                    fig3 if 'fig3' in locals() else None,
                    fig4 if 'fig4' in locals() else None,
                    str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                    (lambda _d: (
                        (min(_d).strftime('%d/%m/%Y') if min(_d) == max(_d) else f"{min(_d).strftime('%d/%m/%Y')} — {max(_d).strftime('%d/%m/%Y')}")
                        if _d else "—"
                    ))([d for d in df["_DataObj"].dropna().tolist()] if "_DataObj" in df.columns else []),
                    (lambda v: "—" if v is None else (f"{v:.2f}".rstrip("0").rstrip(".")))(fck_active),
                    verif_fck_df if 'verif_fck_df' in locals() else None,
                    cond_df if 'cond_df' in locals() else None,
                    pareamento_df if 'pareamento_df' in locals() else None,
                    pv_cp_status if 'pv_cp_status' in locals() else None,
                    s.get("qr_url","")
                )

                file_name_pdf = build_pdf_filename(df_view, uploaded_files)

                st.download_button(
                    "📄 Baixar Relatório (PDF)",
                    data=pdf_bytes,
                    file_name=file_name_pdf,
                    mime="application/pdf",
                    use_container_width=True
                )
                log_event("export_pdf", {
                    "rows": int(df_view.shape[0]),
                    "relatorios": int(df_view["Relatório"].nunique()),
                    "obra": str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                    "file_name": file_name_pdf,
                })
            except Exception as e:
                st.error(f"Falha ao gerar PDF: {e}")

            if 'pdf_bytes' in locals() and pdf_bytes and CAN_EXPORT:
                try: render_print_block(pdf_bytes, None, locals().get("brand", "#3b82f6"), locals().get("brand600", "#2563eb"))
                except Exception: pass

            # ====== EXCEL/ZIP (apenas admin) ======
            try:
                stats_all_full = (df_view.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index())
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_view.to_excel(writer, sheet_name="Individuais", index=False)
                    stats_cp_idade.to_excel(writer, sheet_name="Médias_DP", index=False)
                    comp_df = stats_all_full.rename(columns={"mean": "Média Real", "std": "DP Real", "count": "n"})
                    _est_df = locals().get("est_df")
                    if isinstance(_est_df, pd.DataFrame) and (not _est_df.empty):
                        comp_df = comp_df.merge(_est_df.rename(columns={"Resistência (MPa)": "Estimado"}), on="Idade (dias)", how="outer").sort_values("Idade (dias)")
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                    else:
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                    try:
                        ws_md = writer.sheets.get("Médias_DP")
                        if ws_md is not None and "fig1" in locals() and fig1 is not None:
                            img1 = tempfile.NamedTemporaryFile(delete=False, suffix=".png"); fig1.savefig(img1.name, dpi=150, bbox_inches="tight")
                            ws_md.insert_image("H2", img1.name, {"x_scale": 0.7, "y_scale": 0.7})
                    except Exception: pass
                    try:
                        ws_comp = writer.sheets.get("Comparação")
                        if ws_comp is not None and "fig2" in locals() and fig2 is not None:
                            img2 = tempfile.NamedTemporaryFile(delete=False, suffix=".png"); fig2.savefig(img2.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H20", img2.name, {"x_scale": 0.7, "y_scale": 0.7})
                        if ws_comp is not None and "fig3" in locals() and fig3 is not None:
                            img3 = tempfile.NamedTemporaryFile(delete=False, suffix=".png"); fig3.savefig(img3.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H38", img3.name, {"x_scale": 0.7, "y_scale": 0.7})
                    except Exception: pass

                st.download_button("📊 Baixar Excel (XLSX)", data=excel_buffer.getvalue(),
                                   file_name="Relatorio_Graficos.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   use_container_width=True)
                log_event("export_excel", { "rows": int(df_view.shape[0]) })

                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
                    z.writestr("Medias_DP.csv", stats_cp_idade.to_csv(index=False, sep=";"))
                    if isinstance(_est_df, pd.DataFrame) and (not _est_df.empty):
                        z.writestr("Estimativas.csv", _est_df.to_csv(index=False, sep=";"))
                    if "comp_df" in locals():
                        z.writestr("Comparacao.csv", comp_df.to_csv(index=False, sep=";"))
                st.download_button("🗃️ Baixar CSVs (ZIP)", data=zip_buf.getvalue(),
                                   file_name="Relatorio_Graficos_CSVs.zip",
                                   mime="application/zip", use_container_width=True)
                log_event("export_zip", { "rows": int(df_view.shape[0]) })
            except Exception:
                pass
else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# 5) Ler Novo(s) Certificado(s)
if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
    s["uploader_key"] += 1
    st.rerun()

st.markdown("</div>", unsafe_allow_html=True)
st.markdown("---")

# ===== Rodapé: Normas =====
st.subheader("📘 Normas de Referência")
st.markdown("""
- **NBR 5738** – Concreto: Procedimento para moldagem e cura de corpos de prova
- **NBR 5739** – Concreto: Ensaio de compressão de corpos de prova cilíndricos
- **NBR 12655** – Concreto de cimento Portland: Preparo, controle e recebimento
- **NBR 7215** – Cimento Portland: Determinação da resistência à compressão
""")
st.markdown(
    """
    <div style="text-align:center; font-size:18px; font-weight:600; opacity:.9; margin-top:10px;">
      Sistema desenvolvido pela Habisolute Engenharia
    </div>
    """,
    unsafe_allow_html=True
)



