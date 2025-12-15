# app.py — Habisolute Analytics (corrigido + melhorias dinâmicas + fix verificação 3d)

import io, re, json, base64, tempfile, zipfile, hashlib
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
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    Image as RLImage, PageBreak
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,90Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido por IA e pela Habisolute Engenharia"

class NumberedCanvas(pdfcanvas.Canvas):
    ORANGE = colors.HexColor("#c6c9cf")
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
        if PREFS_PATH.exists():
            return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}

def load_user_prefs(key: str = "default") -> Dict[str, Any]:
    return _load_all_prefs().get(key, {})

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
s.setdefault("last_sel_rels", [])
s.setdefault("last_date_range", None)
# novos campos de cabeçalho de relatório
s.setdefault("rt_responsavel", "")
s.setdefault("rt_cliente", "")
s.setdefault("rt_cidade", "")

# Recupera usuário após refresh
if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"): s["username"] = _p["last_user"]

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
    except Exception:
        pass
_apply_query_prefs()

s.setdefault("wide_layout", True)
MAX_W = 1800 if s.get("wide_layout") else 1300

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

plt.rcParams.update({
    "font.size":10,"axes.titlesize":12,"axes.labelsize":10,
    "axes.titleweight":"semibold","figure.autolayout":False
})

if s.get("theme_mode") == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#0b0f19; --panel:#0f172a; --surface:#111827; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top:56px; max-width: {MAX_W}px; }}
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
    .block-container{{ padding-top:56px; max-width: {MAX_W}px; }}
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
st.markdown(css, unsafe_allow_html=True)

def _render_header():
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='display:flex;justify-content:space-between;align-items:center;'>"
        "<span style='font-weight:800; font-size:22px; color: var(--text)'>🏗️ Habisolute IA</span>"
        "<span style='font-size:12.5px; opacity:.7'>Envie certificados e gere análises, gráficos e PDF.</span>"
        "</div>",
        unsafe_allow_html=True
    )

# =============================================================================
# Autenticação & gerenciamento de usuários
# =============================================================================
def _hash_password(pw: str) -> str:
    return hashlib.sha256(("habisolute|" + pw).encode("utf-8")).hexdigest()

def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hash_password(pw) == hashed
    except Exception:
        return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)

def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"),
                "is_admin": True,
                "active": True,
                "must_change": True,
                "created_at": datetime.now().isoformat(timespec="seconds")
            }
        return db
    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data)
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
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": (uname == "admin"),
                                "active": True,
                                "must_change": True,
                                "created_at": datetime.now().isoformat(timespec="seconds")
                            }
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": bool(item.get("is_admin", uname == "admin")),
                                "active": bool(item.get("active", True)),
                                "must_change": True,
                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))
                            }
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception:
        pass
    default = _bootstrap_admin({"users": {}}); _save_users(default); return default

def user_get(username: str) -> Optional[Dict[str, Any]]:
    return _load_users().get("users", {}).get(username)

def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)

def user_exists(username: str) -> bool:
    return user_get(username) is not None

def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out = []
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"] = uname; out.append(r)
    out.sort(key=lambda r: (not r.get("is_admin", False), r["username"]))
    return out

def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin":
            return
        db["users"].pop(username, None); _save_users(db)

def _auth_login_ui():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Entrar - 🏗️ Habisolute Analytics</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.3, 1.3, 0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password", label_visibility="collapsed", placeholder="Senha")
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
                s["logged_in"] = True; s["username"] = (user or "").strip()
                s["is_admin"] = bool(rec.get("is_admin", False)); s["must_change"] = bool(rec.get("must_change", False))
                prefs = load_user_prefs(); prefs["last_user"] = s["username"]; save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"]})
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password"); p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1) < 4:
            st.error("Use ao menos 4 caracteres.")
        elif p1 != p2:
            st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"] = _hash_password(p1); rec["must_change"] = False; user_set(username, rec)
            log_event("password_changed", {"username": username})
            st.success("Senha atualizada! Redirecionando…"); s["must_change"] = False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Tela de login
# =============================================================================
if not s["logged_in"]:
    _auth_login_ui()
    st.stop()

if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    st.stop()

# Cabeçalho
_render_header()
# =============================================================================
# Toolbar de preferências
# =============================================================================
st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns([1.1, 1.1, 2.5, 1.1])
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
                qp = st.query_params
                qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
            except Exception:
                pass
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

CAN_ADMIN  = bool(s.get("is_admin", False))
CAN_EXPORT = CAN_ADMIN

def _empty_audit_df():
    return pd.DataFrame(columns=["ts", "user", "level", "action", "meta"])

df_log = _empty_audit_df()

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
                    colA, colB, colC, colD, colE = st.columns([2,1,1.2,1.6,1.4])
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

                c1_, c2_, c3_, c4_ = st.columns([1.4, 1.2, 1.6, 1.0])
                with c1_:
                    users_opt = ["(Todos)"] + sorted([u for u in df_log["user"].dropna().unique().tolist()])
                    f_user = st.selectbox("Usuário", users_opt, index=0)
                with c2_:
                    f_action = st.text_input("Ação contém...", "")
                with c3_:
                    lv_opts = ["(Todos)", "INFO", "WARN", "ERROR"]
                    f_level = st.selectbox("Nível", lv_opts, index=0)
                with c4_:
                    page_size = st.selectbox("Linhas", [100, 300, 1000], index=1)

                d1_, d2_ = st.columns(2)
                with d1_:
                    dt_min = st.date_input("Data inicial", value=None, key="aud_dini")
                with d2_:
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
                    start = (int(page) - 1) * int(page_size); end = start + int(page_size)
                    view = logv.iloc[start:end].copy()
                else:
                    view = logv.copy()
                st.dataframe(view, use_container_width=True)

                try:
                    dts = pd.to_datetime(logv["ts"].str.replace("Z", "", regex=False), errors="coerce").dropna()
                    if not dts.empty:
                        pmin = dts.min().strftime("%Y-%m-%d"); pmax = dts.max().strftime("%Y-%m-%d")
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

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["wide_layout"] = st.toggle("Tela larga (1800px)", value=bool(s.get("wide_layout", True)), key="opt_wide_layout")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]), key="opt_batch_mode")
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1, key="opt_tol_mpa")
    st.markdown("---")
    st.markdown("#### 📄 Dados do relatório")
    s["rt_responsavel"] = st.text_input("Responsável técnico", value=s.get("rt_responsavel",""))
    s["rt_cliente"]     = st.text_input("Cliente / Empreendimento", value=s.get("rt_cliente",""))
    s["rt_cidade"]      = st.text_input("Cidade / UF", value=s.get("rt_cidade",""))
    st.markdown("---")
    st.caption(f"Usuário: **{nome_login}** ({papel})")

# =============================================================================
# Utilidades de parsing / limpeza
# =============================================================================
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
    num = float(value)
    label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None: return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan': return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
    # mesmo do teu, já preparado para pegar idades variadas
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

                resistência, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistência = float(t.replace(",", "."))
                            res_idx = j; break

                if idade is None or resistência is None:
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
                    relatorio, cp, idade, resistência, nf, local,
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
                try: val_f = float(valor)
                except Exception: continue
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

# =============================================================================
# KPIs e utilidades
# =============================================================================
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

# =============================================================================
# Uploader
# =============================================================================
st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

BATCH_MODE = bool(s.get("BATCH_MODE", False))
_uploader_key = f"uploader_{'multi' if BATCH_MODE else 'single'}_{s['uploader_key']}"

if BATCH_MODE:
    uploaded_files = st.file_uploader("📁 PDF(s)", type=["pdf"], accept_multiple_files=True,
                                      key=_uploader_key, help="Carregue 1 ou mais PDFs.")
else:
    up1 = st.file_uploader("📁 PDF (1 arquivo)", type=["pdf"], accept_multiple_files=False,
                           key=_uploader_key, help="Carregue 1 PDF.")
    uploaded_files = [up1] if up1 is not None else []

# =============================================================================
# Helpers de nome de arquivo
# =============================================================================
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
# =============================================================================
# Análise / Limpeza / Gráficos
# =============================================================================
def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["Idade (dias)", "Resistência (MPa)", "Fck Projeto",
              "Abatimento NF (mm)", "Abatimento NF tol (mm)", "Abatimento Obra (mm)"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out

def _remove_outliers_sigma(df: pd.DataFrame, sigma: float = 3.0) -> pd.DataFrame:
    if df.empty or "Resistência (MPa)" not in df.columns:
        return df
    out = df.copy()
    x = pd.to_numeric(out["Resistência (MPa)"], errors="coerce")
    mu = x.mean()
    sd = x.std()
    if pd.isna(mu) or pd.isna(sd) or sd <= 0:
        return out
    z = (x - mu) / sd
    return out[(z.abs() <= sigma) | z.isna()].copy()

def _get_fck_from_df(df: pd.DataFrame) -> Optional[float]:
    if "Fck Projeto" in df.columns:
        vals = pd.to_numeric(df["Fck Projeto"], errors="coerce").dropna()
        if not vals.empty:
            return float(vals.mode().iat[0]) if not vals.mode().empty else float(vals.iloc[0])
    return None

def _triade_plot(df_view: pd.DataFrame, fck: Optional[float], title: str = "Crescimento da resistência por corpo de prova"):
    """
    Gráfico Triade: linhas contínuas conectando os pontos reais por CP ao longo do tempo.
    X = Idade (dias), Y = Resistência (MPa)
    """
    fig = plt.figure(figsize=(9.5, 4.8))
    ax = fig.add_subplot(111)

    if df_view.empty:
        ax.set_title(title)
        ax.set_xlabel("Idade (dias)")
        ax.set_ylabel("Resistência (MPa)")
        ax.text(0.5, 0.5, "Sem dados", ha="center", va="center", transform=ax.transAxes)
        return fig

    d = df_view.copy()
    d["Idade (dias)"] = pd.to_numeric(d["Idade (dias)"], errors="coerce")
    d["Resistência (MPa)"] = pd.to_numeric(d["Resistência (MPa)"], errors="coerce")
    d = d.dropna(subset=["CP", "Idade (dias)", "Resistência (MPa)"])
    d = d.sort_values(["CP", "Idade (dias)"], kind="stable")

    for cp, g in d.groupby("CP", sort=False):
        x = g["Idade (dias)"].astype(float).to_numpy()
        y = g["Resistência (MPa)"].astype(float).to_numpy()
        ax.plot(x, y, marker="o", linewidth=1.8, markersize=4, label=str(cp))

    if fck is not None and not pd.isna(fck):
        ax.axhline(float(fck), linestyle="--", linewidth=1.5)
        ax.text(0.99, float(fck), f" fck = {float(fck):.1f} MPa",
                transform=ax.get_yaxis_transform(), ha="right", va="bottom", fontsize=9)

    ax.set_title(title)
    ax.set_xlabel("Idade (dias)")
    ax.set_ylabel("Resistência (MPa)")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, alpha=0.25)
    place_right_legend(ax)
    return fig

def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

def _status_dot(color_hex: str, text: str, styles):
    return Paragraph(f'<font color="{color_hex}">●</font> {text}', styles["BodyText"])

def _mk_verif_table(df_view: pd.DataFrame, fck: Optional[float], styles):
    """
    Tabela: Verificação detalhada por CP (7/28/63 dias)
    7 dias = amarelo (informativo)
    28/63 = verde se >= fck, vermelho se < fck
    """
    base = df_view.copy()
    base["Resistência (MPa)"] = pd.to_numeric(base["Resistência (MPa)"], errors="coerce")
    base["Idade (dias)"] = pd.to_numeric(base["Idade (dias)"], errors="coerce")
    base = base.dropna(subset=["CP", "Idade (dias)", "Resistência (MPa)"])

    ages = [7, 28, 63]
    cps = sorted(base["CP"].astype(str).unique().tolist())

    def _mean(cp, age):
        g = base[(base["CP"].astype(str) == str(cp)) & (base["Idade (dias)"] == age)]
        if g.empty:
            return None
        return float(g["Resistência (MPa)"].mean())

    header = ["CP"]
    for a in ages:
        header += [f"{a}d (MPa)", f"{a}d Status"]

    data = [header]

    for cp in cps:
        row = [cp]
        for a in ages:
            val = _mean(cp, a)
            if val is None:
                row += ["—", _status_dot("#9ca3af", "Sem dado", styles)]
                continue

            row += [f"{val:.2f}".replace(".", ",")]

            if a == 7:
                row += [_status_dot("#f59e0b", "Informativo", styles)]
            else:
                if fck is None or pd.isna(fck):
                    row += [_status_dot("#9ca3af", "Sem fck", styles)]
                else:
                    if val >= float(fck):
                        row += [_status_dot("#16a34a", "Atendeu", styles)]
                    else:
                        row += [_status_dot("#ef4444", "Não atendeu", styles)]

        data.append(row)

    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e5e7eb")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.black),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0,1), (-1,-1), 8.5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    return tbl

def _mk_main_table(df_view: pd.DataFrame, styles):
    cols = [
        "Relatório","CP","Idade (dias)","Resistência (MPa)",
        "Fck Projeto","Nota Fiscal","Local","Usina",
        "Abatimento NF (mm)","Abatimento Obra (mm)"
    ]
    for c in cols:
        if c not in df_view.columns:
            df_view[c] = None

    d = df_view[cols].copy()
    d["Idade (dias)"] = pd.to_numeric(d["Idade (dias)"], errors="coerce")
    d["Resistência (MPa)"] = pd.to_numeric(d["Resistência (MPa)"], errors="coerce")
    d["Fck Projeto"] = pd.to_numeric(d["Fck Projeto"], errors="coerce")

    header = ["Rel.", "CP", "Idade", "MPa", "fck", "NF", "Local", "Usina", "Abat NF", "Abat Obra"]
    data = [header]

    for _, r in d.iterrows():
        data.append([
            str(r["Relatório"]) if pd.notna(r["Relatório"]) else "—",
            str(r["CP"]) if pd.notna(r["CP"]) else "—",
            str(int(r["Idade (dias)"])) if pd.notna(r["Idade (dias)"]) else "—",
            (f"{float(r['Resistência (MPa)']):.2f}".replace(".", ",") if pd.notna(r["Resistência (MPa)"]) else "—"),
            (_format_float_label(r["Fck Projeto"]) if pd.notna(r["Fck Projeto"]) else "—"),
            str(r["Nota Fiscal"]) if pd.notna(r["Nota Fiscal"]) else "—",
            str(r["Local"]) if pd.notna(r["Local"]) else "—",
            str(r["Usina"]) if pd.notna(r["Usina"]) else "—",
            (_format_float_label(r["Abatimento NF (mm)"]) if pd.notna(r["Abatimento NF (mm)"]) else "—"),
            (_format_float_label(r["Abatimento Obra (mm)"]) if pd.notna(r["Abatimento Obra (mm)"]) else "—"),
        ])

    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e5e7eb")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.black),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0,1), (-1,-1), 8.2),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    return tbl

def _doc_id(df_view: pd.DataFrame) -> str:
    # ID estável baseado em obra+relatórios+linhas
    base = ""
    try:
        obra = str(_safe_mode(df_view.get("Obra", pd.Series([""])).astype(str))) if "Obra" in df_view.columns else ""
        rel  = str(_safe_mode(df_view.get("Relatório", pd.Series([""])).astype(str))) if "Relatório" in df_view.columns else ""
        base = f"{obra}|{rel}|{len(df_view)}"
    except Exception:
        base = f"len={len(df_view)}"
    h = hashlib.sha256(("HAB|" + base).encode("utf-8")).hexdigest()[:12].upper()
    return f"HAB-{h}"

def _try_make_qr_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        import qrcode  # pode não existir
        qr = qrcode.QRCode(version=2, box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        b = io.BytesIO()
        img.save(b, format="PNG")
        b.seek(0)
        return b.getvalue()
    except Exception:
        return None

def gerar_pdf(
    df_view: pd.DataFrame,
    obra: str,
    data_relatorio: str,
    fck: Optional[float],
    responsavel: str,
    cliente: str,
    cidade: str,
    qr_url: str,
    basic_mode_pdf: bool = False,
    cp_focus: Optional[str] = None
) -> bytes:
    """
    basic_mode_pdf=True => Mantém somente:
      1) Tabela principal
      2) Gráfico 1 (Triade)
      3) Tabela Verificação detalhada por CP (7/28/63)
    """
    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18, rightMargin=18, topMargin=22, bottomMargin=56
    )

    story: List[Any] = []

    # capa/identificação
    titulo = "Relatório de Análise de Certificado"
    if cp_focus:
        titulo += f" — CP {cp_focus}"

    story.append(Paragraph(f"<b>{titulo}</b>", styles["Title"]))
    story.append(Spacer(1, 6))

    # QR opcional
    qr_bytes = _try_make_qr_bytes(qr_url.strip()) if qr_url else None
    if qr_bytes:
        try:
            tmp = io.BytesIO(qr_bytes)
            story.append(Paragraph("Resumo (QR):", styles["BodyText"]))
            story.append(RLImage(tmp, width=92, height=92))
            story.append(Spacer(1, 6))
        except Exception:
            pass

    # infos
    info_lines = [
        f"<b>Obra:</b> {obra or '—'}",
        f"<b>Data do certificado:</b> {data_relatorio or '—'}",
        f"<b>Cliente:</b> {cliente or '—'}",
        f"<b>Cidade/UF:</b> {cidade or '—'}",
        f"<b>Responsável técnico:</b> {responsavel or '—'}",
        f"<b>fck:</b> {(_format_float_label(fck) + ' MPa') if (fck is not None and not pd.isna(fck)) else '—'}",
    ]
    story.append(Paragraph("<br/>".join(info_lines), styles["BodyText"]))
    story.append(Spacer(1, 10))

    # 1) tabela principal
    story.append(Paragraph("<b>1) Tabela principal</b>", styles["Heading2"]))
    story.append(Spacer(1, 4))
    story.append(_mk_main_table(df_view, styles))
    story.append(Spacer(1, 10))

    # 2) gráfico 1
    story.append(Paragraph("<b>2) Crescimento da resistência por corpo de prova</b>", styles["Heading2"]))
    story.append(Spacer(1, 4))
    fig1 = _triade_plot(df_view, fck, title="Crescimento da resistência por corpo de prova")
    img1 = _fig_to_png_bytes(fig1)
    story.append(RLImage(io.BytesIO(img1), width=520, height=260))
    story.append(Spacer(1, 10))

    # 3) verificação detalhada
    story.append(Paragraph("<b>3) Verificação do fck / CP detalhado</b>", styles["Heading2"]))
    story.append(Spacer(1, 4))
    story.append(_mk_verif_table(df_view, fck, styles))
    story.append(Spacer(1, 12))

    # Se NÃO for básico, aqui entrariam as demais seções (resumos, gráficos extras, score etc.)
    # (mantido propositalmente fora do modo básico do PDF)
    if not basic_mode_pdf:
        story.append(Paragraph("<b>Observações</b>", styles["Heading2"]))
        story.append(Paragraph(
            "• 7 dias: indicador informativo (amarelo).<br/>"
            "• 28/63 dias: atendimento ao fck conforme valor informado no certificado (ou definido no sistema).<br/>"
            "• Score sugerido: 28d (60%) e 63d (40%).",
            styles["BodyText"]
        ))
        story.append(Spacer(1, 10))

    # frase final + ID (logo abaixo, sem ir para rodapé)
    doc_id = _doc_id(df_view)
    story.append(Paragraph("Documento emitido pelo Sistema Habisolute IA.", styles["BodyText"]))
    story.append(Paragraph(f"<b>ID do documento:</b> {doc_id}", styles["BodyText"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()

# =============================================================================
# Pipeline pós-upload
# =============================================================================
if not uploaded_files or all(f is None for f in uploaded_files):
    st.info("Envie um PDF para iniciar.")
    st.stop()

all_rows = []
metas = []
for f in uploaded_files:
    if f is None:
        continue
    df0, obra0, data0, fck0 = extrair_dados_certificado(f)
    if df0 is None:
        continue
    df0 = _coerce_numeric(df0)
    df0["Obra"] = obra0
    df0["Data Certificado"] = data0
    df0["Arquivo"] = getattr(f, "name", "PDF")
    # tenta preencher fck
    if "Fck Projeto" not in df0.columns:
        df0["Fck Projeto"] = None
    try:
        if isinstance(fck0, (int, float)):
            df0["Fck Projeto"] = df0["Fck Projeto"].fillna(float(fck0))
    except Exception:
        pass

    all_rows.append(df0)
    metas.append({"arquivo": getattr(f, "name", ""), "obra": obra0, "data": data0, "fck": fck0})

df_all = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()

if df_all.empty:
    st.error("Não consegui extrair dados válidos desse(s) PDF(s).")
    st.stop()

# limpeza opcional
colL, colR = st.columns([1.2, 2.8])
with colL:
    use_out = st.toggle("Remover outliers (3σ)", value=True)
    sigma = st.slider("Sigma", 2.0, 5.0, float(s.get("OUTLIER_SIGMA", 3.0)), 0.1)
    s["OUTLIER_SIGMA"] = sigma
with colR:
    st.markdown(
        "<div class='pill'>📌 Dica: o PDF básico remove apenas seções extras do relatório, "
        "mas o sistema continua completo na tela.</div>",
        unsafe_allow_html=True
    )

if use_out:
    df_all = _remove_outliers_sigma(df_all, sigma=float(sigma))

# filtros
st.markdown("### 🔎 Filtros")
cA, cB, cC, cD = st.columns([1.2, 1.2, 1.2, 1.6])

with cA:
    rel_opts = sorted(df_all["Relatório"].astype(str).dropna().unique().tolist())
    rel_sel = st.multiselect("Relatório", rel_opts, default=rel_opts[:1] if rel_opts else [])
with cB:
    nf_opts = sorted(df_all["Nota Fiscal"].astype(str).dropna().unique().tolist())
    nf_sel = st.multiselect("Nota Fiscal", nf_opts, default=[])
with cC:
    age_opts = sorted(pd.to_numeric(df_all["Idade (dias)"], errors="coerce").dropna().astype(int).unique().tolist())
    age_sel = st.multiselect("Idade (dias)", age_opts, default=age_opts)
with cD:
    cp_opts = sorted(df_all["CP"].astype(str).dropna().unique().tolist())
    cp_focus = st.selectbox("CP focado (opcional para PDF)", ["(Nenhum)"] + cp_opts, index=0)

df_view = df_all.copy()
if rel_sel:
    df_view = df_view[df_view["Relatório"].astype(str).isin([str(x) for x in rel_sel])]
if nf_sel:
    df_view = df_view[df_view["Nota Fiscal"].astype(str).isin([str(x) for x in nf_sel])]
if age_sel:
    df_view = df_view[pd.to_numeric(df_view["Idade (dias)"], errors="coerce").isin([int(x) for x in age_sel])]

obra = str(_safe_mode(df_view.get("Obra", pd.Series(["NÃO IDENTIFICADA"])).astype(str))) if "Obra" in df_view.columns else "NÃO IDENTIFICADA"
data_relatorio = str(_safe_mode(df_view.get("Data Certificado", pd.Series(["NÃO IDENTIFICADA"])).astype(str))) if "Data Certificado" in df_view.columns else "NÃO IDENTIFICADA"

# fck: auto + override
fck_auto = _get_fck_from_df(df_view)
c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.6])
with c1:
    fck_manual_on = st.toggle("Definir fck manual", value=False)
with c2:
    fck_manual = st.number_input("fck (MPa)", min_value=0.0, max_value=200.0, value=float(fck_auto or 0.0), step=0.5, disabled=not fck_manual_on)
with c3:
    tol_mpa = float(s.get("TOL_MP", 1.0))
    st.metric("Tolerância (Real×Est.)", f"{tol_mpa:.1f} MPa")
with c4:
    basic_mode_pdf = st.toggle("📄 Emissão de relatório básico (somente PDF)", value=False)

fck = float(fck_manual) if fck_manual_on else fck_auto

# KPIs
k = compute_exec_kpis(df_view, fck)
st.markdown("### 📌 KPIs")
k1, k2, k3, k4, k5 = st.columns(5)
k1.markdown(f"<div class='h-card'><div class='h-kpi-label'>Relatórios</div><div class='h-kpi'>{k['n_rel']}</div></div>", unsafe_allow_html=True)
k2.markdown(f"<div class='h-card'><div class='h-kpi-label'>Média (MPa)</div><div class='h-kpi'>{_format_float_label(k['media'])}</div></div>", unsafe_allow_html=True)
k3.markdown(f"<div class='h-card'><div class='h-kpi-label'>DP (MPa)</div><div class='h-kpi'>{_format_float_label(k['dp'])}</div></div>", unsafe_allow_html=True)
k4.markdown(f"<div class='h-card'><div class='h-kpi-label'>Atendeu 28d</div><div class='h-kpi'>{('—' if k['pct28'] is None else f'{k['pct28']:.0f}%')}</div></div>", unsafe_allow_html=True)
k5.markdown(f"<div class='h-card'><div class='h-kpi-label'>Status</div><div class='h-kpi'>{k['status_txt']}</div></div>", unsafe_allow_html=True)

# tabela + gráfico na tela
st.markdown("### 🧾 Tabela principal")
st.dataframe(df_view, use_container_width=True)

st.markdown("### 📈 Gráfico 1 — Crescimento da resistência por CP")
fig = _triade_plot(df_view, fck, title="Crescimento da resistência por corpo de prova")
st.pyplot(fig, use_container_width=True)
# =============================================================================
# Verificação detalhada (tela) + Botões PDF
# =============================================================================
st.markdown("### ✅ Verificação detalhada por CP (7/28/63)")
# tabela visual na tela
def _screen_verif_df(df_view: pd.DataFrame, fck: Optional[float]) -> pd.DataFrame:
    base = df_view.copy()
    base["Resistência (MPa)"] = pd.to_numeric(base["Resistência (MPa)"], errors="coerce")
    base["Idade (dias)"] = pd.to_numeric(base["Idade (dias)"], errors="coerce")
    base = base.dropna(subset=["CP", "Idade (dias)", "Resistência (MPa)"])
    cps = sorted(base["CP"].astype(str).unique().tolist())
    ages = [7, 28, 63]

    rows = []
    for cp in cps:
        row = {"CP": cp}
        for a in ages:
            g = base[(base["CP"].astype(str) == cp) & (base["Idade (dias)"] == a)]
            if g.empty:
                row[f"{a}d (MPa)"] = None
                row[f"{a}d Status"] = "Sem dado"
                continue
            val = float(g["Resistência (MPa)"].mean())
            row[f"{a}d (MPa)"] = val
            if a == 7:
                row[f"{a}d Status"] = "Informativo"
            else:
                if fck is None or pd.isna(fck):
                    row[f"{a}d Status"] = "Sem fck"
                else:
                    row[f"{a}d Status"] = "Atendeu" if val >= float(fck) else "Não atendeu"
        rows.append(row)
    return pd.DataFrame(rows)

df_ver = _screen_verif_df(df_view, fck)
st.dataframe(df_ver, use_container_width=True)

# =============================================================================
# Geração de PDF (normal e CP focado)
# =============================================================================
st.markdown("### 📄 Gerar PDF")

cp_focus_value = None if (cp_focus == "(Nenhum)") else str(cp_focus)

# para CP focado, filtra df
df_cp = df_view[df_view["CP"].astype(str) == cp_focus_value].copy() if cp_focus_value else None

# nome do arquivo do PDF
pdf_name = build_pdf_filename(df_view, uploaded_files)

btn1, btn2, btn3 = st.columns([1.3, 1.3, 2.4])

with btn1:
    if st.button("Gerar PDF (Tudo)", use_container_width=True):
        try:
            log_event("pdf_generate_all", {"basic_mode": bool(basic_mode_pdf), "rows": int(len(df_view))})
            pdf_bytes = gerar_pdf(
                df_view=df_view,
                obra=obra,
                data_relatorio=data_relatorio,
                fck=fck,
                responsavel=s.get("rt_responsavel",""),
                cliente=s.get("rt_cliente",""),
                cidade=s.get("rt_cidade",""),
                qr_url=s.get("qr_url",""),
                basic_mode_pdf=bool(basic_mode_pdf),
                cp_focus=None
            )
            s["_last_pdf_all"] = pdf_bytes
            st.success("PDF gerado!")
        except Exception as e:
            log_event("pdf_generate_error", {"err": str(e)}, level="ERROR")
            st.error(f"Erro ao gerar PDF: {e}")

with btn2:
    if st.button("Gerar PDF (CP focado)", use_container_width=True, disabled=(df_cp is None or df_cp.empty)):
        try:
            log_event("pdf_generate_cp", {"basic_mode": bool(basic_mode_pdf), "cp": cp_focus_value, "rows": int(len(df_cp))})
            pdf_bytes_cp = gerar_pdf(
                df_view=df_cp,
                obra=obra,
                data_relatorio=data_relatorio,
                fck=fck,
                responsavel=s.get("rt_responsavel",""),
                cliente=s.get("rt_cliente",""),
                cidade=s.get("rt_cidade",""),
                qr_url=s.get("qr_url",""),
                basic_mode_pdf=bool(basic_mode_pdf),
                cp_focus=cp_focus_value
            )
            s["_last_pdf_cp"] = pdf_bytes_cp
            st.success("PDF (CP) gerado!")
        except Exception as e:
            log_event("pdf_generate_cp_error", {"err": str(e)}, level="ERROR")
            st.error(f"Erro ao gerar PDF CP: {e}")

with btn3:
    pdf_all = s.get("_last_pdf_all")
    pdf_cp  = s.get("_last_pdf_cp")
    if pdf_all:
        st.download_button(
            "⬇️ Baixar PDF (Tudo)",
            data=pdf_all,
            file_name=pdf_name,
            mime="application/pdf",
            use_container_width=True,
        )
    if pdf_cp:
        st.download_button(
            "⬇️ Baixar PDF (CP focado)",
            data=pdf_cp,
            file_name=pdf_name.replace(".pdf", f"_CP_{cp_focus_value}.pdf"),
            mime="application/pdf",
            use_container_width=True,
        )

# Botões de impressão estilo “igual print”
pdf_all = s.get("_last_pdf_all")
pdf_cp  = s.get("_last_pdf_cp")
if pdf_all:
    render_print_block(pdf_all, pdf_cp, brand=brand, brand600=brand600)

# =============================================================================
# Modo lote (gera ZIP com PDFs por arquivo)
# =============================================================================
if bool(s.get("BATCH_MODE", False)) and len([f for f in uploaded_files if f is not None]) > 1:
    st.markdown("### 📦 Modo Lote — ZIP com PDFs")
    st.caption("Gera 1 PDF por arquivo carregado. O modo básico também se aplica aqui (somente ao PDF).")
    if st.button("Gerar ZIP (PDFs)", use_container_width=True):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in uploaded_files:
                if f is None:
                    continue
                df0, obra0, data0, fck0 = extrair_dados_certificado(f)
                df0 = _coerce_numeric(df0)
                df0["Obra"] = obra0
                df0["Data Certificado"] = data0
                if "Fck Projeto" not in df0.columns:
                    df0["Fck Projeto"] = None
                # fck do arquivo (ou override do sistema, se tiver manual ligado)
                fck_file = float(fck_manual) if fck_manual_on else _get_fck_from_df(df0)
                nome = build_pdf_filename(df0, [f]).replace(".pdf", f"_{_slugify_for_filename(getattr(f,'name','arquivo'))}.pdf")

                pdf_bytes = gerar_pdf(
                    df_view=df0,
                    obra=obra0,
                    data_relatorio=data0,
                    fck=fck_file,
                    responsavel=s.get("rt_responsavel",""),
                    cliente=s.get("rt_cliente",""),
                    cidade=s.get("rt_cidade",""),
                    qr_url=s.get("qr_url",""),
                    basic_mode_pdf=bool(basic_mode_pdf),
                    cp_focus=None
                )
                zf.writestr(nome, pdf_bytes)

        zbuf.seek(0)
        st.download_button(
            "⬇️ Baixar ZIP",
            data=zbuf.getvalue(),
            file_name=f"PDFs_{_slugify_for_filename(obra)}.zip",
            mime="application/zip",
            use_container_width=True
        )

st.markdown("---")
st.caption("✅ Pronto. Se quiser, eu reativo as seções completas do PDF (resumo estatístico, score, gráfico 4 etc.) mantendo o modo básico como opção.")
