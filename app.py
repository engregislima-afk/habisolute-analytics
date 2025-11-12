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
    BLACK = colors.black

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
        self.setFillColor(self.ORANGE)
        self.rect(0, h - 10, w, 6, stroke=0, fill=1)
        self.setFillColor(self.BLACK)
        self.rect(0, h - 16, w, 2, stroke=0, fill=1)
        # Rodapé
        self.setFillColor(self.BLACK)
        self.rect(0, 8, w, 2, stroke=0, fill=1)
        self.setFillColor(self.ORANGE)
        self.rect(0, 12, w, 6, stroke=0, fill=1)
        # Textos
        y0 = 44
        self.setFillColor(colors.black)
        self.setFont("Helvetica", 7)
        lines = self._wrap_footer(FOOTER_TEXT, "Helvetica", 7, w - 36 - 100)
        for i, ln in enumerate(lines):
            y = y0 + i * 8
            self.drawString(18, y, ln)
        self.setFont("Helvetica-Oblique", 8)
        self.drawCentredString(w / 2.0, y0 - 8, FOOTER_BRAND_TEXT)
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, y0 - 18, f"Página {self._pageNumber} de {total_pages}")


# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"
PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"
USERS_DB = PREFS_DIR / "users.json"
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
        return pd.DataFrame(columns=["ts", "user", "level", "action", "meta"])
    rows = []
    with AUDIT_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rows.append(
                    {
                        "ts": rec.get("ts"),
                        "user": rec.get("user"),
                        "level": rec.get("level"),
                        "action": rec.get("action"),
                        "meta": json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                    }
                )
            except Exception:
                continue
    df = pd.DataFrame(rows, columns=["ts", "user", "level", "action", "meta"])
    if not df.empty:
        df = df.sort_values("ts", ascending=False, kind="stable").reset_index(drop=True)
    return df


# ----- prefs util -----
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PREFS_PATH)


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
    data = _load_all_prefs()
    data[key] = prefs
    _save_all_prefs(data)


# ===== Estado =====
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("username", None)
s.setdefault("is_admin", False)
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0)
s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0)
s.setdefault("BATCH_MODE", False)
s.setdefault("_prev_batch", s["BATCH_MODE"])
s.setdefault("last_sel_rels", [])
s.setdefault("last_date_range", None)
s.setdefault("rt_responsavel", "")
s.setdefault("rt_cliente", "")
s.setdefault("rt_cidade", "")

# Recupera usuário após refresh
if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"):
        s["username"] = _p["last_user"]


def _apply_query_prefs():
    try:
        qp = st.query_params

        def _first(x):
            if x is None:
                return None
            return x[0] if isinstance(x, list) else x

        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno", "Claro corporativo"):
            s["theme_mode"] = theme
        if brand in ("Laranja", "Azul", "Verde", "Roxo"):
            s["brand"] = brand
        if qr:
            s["qr_url"] = qr
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
    "Azul": ("#3b82f6", "#2563eb", "#1d4ed8"),
    "Verde": ("#22c55e", "#16a34a", "#15803d"),
    "Roxo": ("#a855f7", "#9333ea", "#7e22ce"),
}
brand, brand600, brand700 = BRAND_MAP.get(s["brand"], BRAND_MAP["Laranja"])

plt.rcParams.update(
    {
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.titleweight": "semibold",
        "figure.autolayout": False,
    }
)

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
        unsafe_allow_html=True,
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
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_DB)


def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"),
                "is_admin": True,
                "active": True,
                "must_change": True,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        return db

    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data)
                    if fixed is not data:
                        _save_users(fixed)
                    return fixed
                if isinstance(data, dict):
                    fixed = _bootstrap_admin({"users": data})
                    _save_users(fixed)
                    return fixed
                if isinstance(data, list):
                    users_map: Dict[str, Any] = {}
                    for item in data:
                        if isinstance(item, str):
                            uname = item.strip()
                            if not uname:
                                continue
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": (uname == "admin"),
                                "active": True,
                                "must_change": True,
                                "created_at": datetime.now().isoformat(timespec="seconds"),
                            }
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname:
                                continue
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": bool(item.get("is_admin", uname == "admin")),
                                "active": bool(item.get("active", True)),
                                "must_change": True,
                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds")),
                            }
                    fixed = _bootstrap_admin({"users": users_map})
                    _save_users(fixed)
                    return fixed
    except Exception:
        pass
    default = _bootstrap_admin({"users": {}})
    _save_users(default)
    return default


def user_get(username: str) -> Optional[Dict[str, Any]]:
    return _load_users().get("users", {}).get(username)


def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users()
    db.setdefault("users", {})[username] = record
    _save_users(db)


def user_exists(username: str) -> bool:
    return user_get(username) is not None


def user_list() -> List[Dict[str, Any]]:
    db = _load_users()
    out = []
    for uname, rec in db.get("users", {}).items():
        r = dict(rec)
        r["username"] = uname
        out.append(r)
    out.sort(key=lambda r: (not r.get("is_admin", False), r["username"]))
    return out


def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin":
            return
        db["users"].pop(username, None)
        _save_users(db)


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
            elif not _verify_password(pwd, rec.get("password", "")):
                st.error("Senha incorreta.")
                log_event("login_fail", {"username": user, "reason": "bad_password"}, level="WARN")
            else:
                s["logged_in"] = True
                s["username"] = (user or "").strip()
                s["is_admin"] = bool(rec.get("is_admin", False))
                s["must_change"] = bool(rec.get("must_change", False))
                prefs = load_user_prefs()
                prefs["last_user"] = s["username"]
                save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"]})
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)


def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password")
    p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1) < 4:
            st.error("Use ao menos 4 caracteres.")
        elif p1 != p2:
            st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"] = _hash_password(p1)
            rec["must_change"] = False
            user_set(username, rec)
            log_event("password_changed", {"username": username})
            st.success("Senha atualizada! Redirecionando…")
            s["must_change"] = False
            st.rerun()
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
    s["theme_mode"] = st.radio(
        "Tema",
        ["Escuro moderno", "Claro corporativo"],
        index=0 if s.get("theme_mode") == "Escuro moderno" else 1,
        horizontal=True,
    )
with c2:
    s["brand"] = st.selectbox(
        "🎨 Cor da marca",
        ["Laranja", "Azul", "Verde", "Roxo"],
        index=["Laranja", "Azul", "Verde", "Roxo"].index(s.get("brand", "Laranja")),
    )
with c3:
    s["qr_url"] = st.text_input(
        "URL do resumo (QR opcional na capa do PDF)",
        value=s.get("qr_url", ""),
        placeholder="https://exemplo.com/resumo",
    )
with c4:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
            save_user_prefs(
                {
                    "theme_mode": s["theme_mode"],
                    "brand": s["brand"],
                    "qr_url": s["qr_url"],
                    "last_user": s.get("username") or load_user_prefs().get("last_user", ""),
                }
            )
            try:
                qp = st.query_params
                qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
            except Exception:
                pass
            st.success("Preferências salvas! Dica: adicione esta página aos favoritos.")
    with col_b:
        if st.button("Sair", use_container_width=True, key="k_logout"):
            log_event("logout", {"username": s.get("username")})
            s["logged_in"] = False
            st.rerun()
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
    unsafe_allow_html=True,
)

CAN_ADMIN = bool(s.get("is_admin", False))
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
                    colA, colB, colC, colD, colE = st.columns([2, 1, 1.2, 1.6, 1.4])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else "Usuário")
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write("Exige troca" if u.get("must_change") else "Senha OK")
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
                    user_set(
                        new_u.strip(),
                        {
                            "password": _hash_password("1234"),
                            "is_admin": bool(is_ad),
                            "active": True,
                            "must_change": True,
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
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
                    tot_ev = len(df_log)
                    tot_usr = 0
                    tot_act = 0
                    tot_hoje = 0

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

                st.caption(f"{len(logv)} evento(s) filtrados")

                total = len(logv)
                if total > 0:
                    pcols = st.columns([1, 3, 1])
                    with pcols[0]:
                        page = st.number_input(
                            "Página", min_value=1, max_value=max(1, (total - 1) // page_size + 1), value=1, step=1
                        )
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
    s["rt_responsavel"] = st.text_input("Responsável técnico", value=s.get("rt_responsavel", ""))
    s["rt_cliente"] = st.text_input("Cliente / Empreendimento", value=s.get("rt_cliente", ""))
    s["rt_cidade"] = st.text_input("Cidade / UF", value=s.get("rt_cidade", ""))
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
    if not txt:
        return txt
    t = _limpa_horas(str(txt))
    t = re.sub(r"(?i)relat[óo]rio:\s*\d+\s*", "", t)
    t = re.sub(r"(?i)\busina:\s*", "", t)
    t = re.sub(r"(?i)\bsa[ií]da\s+da\s+usina\b.*$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -•:;,.")
    return t or None


def _detecta_usina(linhas: List[str]) -> Optional[str]:
    # tenta achar "usina:" primeiro
    for sline in linhas:
        if re.search(r"(?i)\busina:", sline):
            s0 = _limpa_horas(sline)
            m = re.search(r"(?i)usina:\s*([A-Za-zÀ-ÿ0-9 .\-]+?)(?:\s+sa[ií]da\s+da\s+usina\b|$)", s0)
            if m:
                return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
            return _limpa_usina_extra(s0)
    # tenta pegar linha que tenha a palavra usina
    for sline in linhas:
        if re.search(r"(?i)\busina\b", sline) or re.search(r"(?i)sa[ií]da da usina", sline):
            t = _limpa_horas(sline)
            t2 = re.sub(r"(?i)^.*\busina\b[:\-]?\s*", "", t).strip()
            if t2:
                return t2
            if t:
                return t
    return None


def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok:
        return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*\+?-?\s*(\d+(?:\.\d+)?))?\s*$", t)
    if not m:
        return None, None
    try:
        v = float(m.group(1))
        tol = float(m.group(2)) if m.group(2) is not None else None
        return v, tol
    except Exception:
        return None, None


def _detecta_abatimentos(linhas: List[str]) -> Tuple[Optional[float], Optional[float]]:
    abat_nf = None
    abat_obra = None
    for sline in linhas:
        s_clean = sline.replace(",", ".").replace("±", "+-")
        # NF
        m_nf = re.search(
            r"(?i)abat(?:imento|\.?im\.?)\s*(?:de\s*)?nf[^0-9]*"
            r"(\d+(?:\.\d+)?)(?:\s*\+?-?\s*\d+(?:\.\d+)?)?\s*mm?",
            s_clean,
        )
        if m_nf and abat_nf is None:
            try:
                abat_nf = float(m_nf.group(1))
            except Exception:
                pass
        # obra
        m_obra = re.search(
            r"(?i)abat(?:imento|\.?im\.?).*(obra|medido em obra)[^0-9]*"
            r"(\d+(?:\.\d+)?)\s*mm",
            s_clean,
        )
        if m_obra and abat_obra is None:
            try:
                abat_obra = float(m_obra.group(2))
            except Exception:
                pass
    return abat_nf, abat_obra


def _extract_fck_values(line: str) -> List[float]:
    # pega valores de fck das linhas
    if not line or "fck" not in line.lower():
        return []
    sanitized = line.replace(",", ".")
    parts = re.split(r"(?i)fck", sanitized)[1:]
    if not parts:
        return []
    values: List[float] = []
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\b\s*[:=]?", re.I)
    age_plain = re.compile(r"^(\d{1,3})\b\s*[:=]?", re.I)
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = (
        "mpa",
        "abatimento",
        "slump",
        "nota",
        "usina",
        "relatório",
        "relatorio",
        "consumo",
        "traço",
        "traco",
        "cimento",
        "dosagem",
    )
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
                    seg = seg[m.end() :].lstrip(" :=;-()[]")
                    changed = True
                    continue
            if starts_immediate:
                m2 = age_plain.match(seg)
                if m2:
                    age_val = int(m2.group(1))
                    if age_val in age_tokens:
                        seg = seg[m2.end() :].lstrip(" :=;-()[]")
                        changed = True
                        continue
        lower_seg = seg.lower()
        cut_at = len(seg)
        for kw in cut_keywords:
            idx = lower_seg.find(kw)
            if idx != -1:
                cut_at = min(cut_at, idx)
        seg = seg[:cut_at]
        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try:
                val = float(num)
            except ValueError:
                continue
            if 3 <= val <= 120 and val not in values:
                values.append(val)
    return values


def _to_float_or_none(value: Any) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(val) else val


def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return "—"
    num = float(value)
    label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"


def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None:
        return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == "nan":
        return "—"
    return raw


def extrair_dados_certificado(uploaded_file):
    """
    Lê 1 PDF e devolve:
    - df de CPs
    - nome da obra
    - data do relatório
    - fck de projeto detectado
    """
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
                txt = re.sub(r"[“”]", '"', txt)
                txt = re.sub(r"[’´`]", "'", txt)
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip()])
    except Exception:
        return (
            pd.DataFrame(
                columns=[
                    "Relatório",
                    "CP",
                    "Idade (dias)",
                    "Resistência (MPa)",
                    "Nota Fiscal",
                    "Local",
                    "Usina",
                    "Abatimento NF (mm)",
                    "Abatimento NF tol (mm)",
                    "Abatimento Obra (mm)",
                ]
            ),
            "NÃO IDENTIFICADA",
            "NÃO IDENTIFICADA",
            "NÃO IDENTIFICADO",
        )

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

    # varredura para cabecalhos, obra, data e fck
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
                    try:
                        fck_projeto = float(valores_fck[0])
                    except Exception:
                        pass

    usina_nome = _limpa_usina_extra(_detecta_usina(linhas_todas))
    abat_nf_pdf, abat_obra_pdf = _detecta_abatimentos(linhas_todas)
    dados = []
    relatorio_cabecalho = None

    for sline in linhas_todas:
        partes = sline.split()

        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_cabecalho = m_rel.group(1)
            continue

        # linhas de CP costumam ter muitas colunas
        if len(partes) >= 5 and cp_regex.match(partes[0]):
            try:
                cp = partes[0]
                relatorio = relatorio_cabecalho or "NÃO IDENTIFICADO"

                # acha data
                i_data = next((i for i, t in enumerate(partes) if data_token.match(t)), None)
                if i_data is not None:
                    i_tipo = next((i for i in range(i_data + 1, len(partes)) if tipo_token.match(partes[i])), None)
                    start = (i_tipo + 1) if i_tipo is not None else (i_data + 1)
                else:
                    start = 1

                # idade
                idade_idx, idade = None, None
                for j in range(start, len(partes)):
                    t = partes[j]
                    if t.isdigit():
                        v = int(t)
                        if 1 <= v <= 120:
                            idade = v
                            idade_idx = j
                            break

                # resistência
                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", "."))
                            res_idx = j
                            break

                if idade is None or resistencia is None:
                    continue

                # nota fiscal
                nf, nf_idx = None, None
                start_nf = (res_idx + 1) if res_idx is not None else (idade_idx + 1)
                for j in range(start_nf, len(partes)):
                    tok = partes[j]
                    if nf_regex.match(tok) and tok != cp:
                        nf = tok
                        nf_idx = j
                        break

                # abatimento obra: vem logo antes da data às vezes
                abat_obra_val = None
                if i_data is not None:
                    for j in range(i_data - 1, max(-1, i_data - 6), -1):
                        tok = partes[j]
                        if re.fullmatch(r"\d{2,3}", tok):
                            v = int(tok)
                            if 20 <= v <= 250:
                                abat_obra_val = float(v)
                                break

                # abatimento NF (logo após NF)
                abat_nf_val, abat_nf_tol = None, None
                if nf_idx is not None:
                    for tok in partes[nf_idx + 1 : nf_idx + 5]:
                        v, tol = _parse_abatim_nf_pair(tok)
                        if v is not None and 20 <= v <= 250:
                            abat_nf_val = float(v)
                            abat_nf_tol = float(tol) if tol is not None else None
                            break

                local = local_por_relatorio.get(relatorio)
                dados.append(
                    [
                        relatorio,
                        cp,
                        idade,
                        resistencia,
                        nf,
                        local,
                        usina_nome,
                        (abat_nf_val if abat_nf_val is not None else abat_nf_pdf),
                        abat_nf_tol,
                        (abat_obra_val if abat_obra_val is not None else abat_obra_pdf),
                    ]
                )
            except Exception:
                pass

    df = pd.DataFrame(
        dados,
        columns=[
            "Relatório",
            "CP",
            "Idade (dias)",
            "Resistência (MPa)",
            "Nota Fiscal",
            "Local",
            "Usina",
            "Abatimento NF (mm)",
            "Abatimento NF tol (mm)",
            "Abatimento Obra (mm)",
        ],
    )

    # completa fck por relatório
    if not df.empty:
        rel_map = {}
        for rel, valores in fck_por_relatorio.items():
            uniques = []
            for valor in valores:
                try:
                    val_f = float(valor)
                except Exception:
                    continue
                if val_f not in uniques:
                    uniques.append(val_f)
            if uniques:
                rel_map[rel] = uniques[0]

        fallback_fck = None
        if isinstance(fck_projeto, (int, float)):
            fallback_fck = float(fck_projeto)
        else:
            candidatos = []
            for valores in fck_por_relatorio.values():
                candidatos.extend(valores)
            candidatos.extend(fck_valores_globais)
            for cand in candidatos:
                try:
                    fallback_fck = float(cand)
                    break
                except Exception:
                    continue

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto


# =============================================================================
# Helpers de nome de arquivo e legend
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
    if ages.empty:
        return None
    age = _safe_mode(ages)
    return f"{int(age)}d" if age is not None else None


def _extract_cert_date_token(df_view: pd.DataFrame) -> str | None:
    if "Data Certificado" not in df_view.columns:
        return None
    dates = [_to_date_obj(x) for x in df_view["Data Certificado"].dropna().unique().tolist()]
    dates = [d for d in dates if d is not None]
    if not dates:
        return None
    return _dd_mm_aaaa(min(dates))


def build_pdf_filename(df_view: pd.DataFrame, uploaded_files: list) -> str:
    if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty:
        obra = _safe_mode(df_view["Obra"].astype(str)) or "Obra"
    else:
        obra = "Obra"
    obra_slug = _slugify_for_filename(obra)

    rel_tail = _extract_rel_tail_from_files(uploaded_files)
    age_tok = _extract_age_token(df_view) or ""
    date_tok = _extract_cert_date_token(df_view) or ""

    if rel_tail and "_" in rel_tail and rel_tail.count("_") >= 2:
        final_tail = rel_tail
    else:
        rrr = (
            rel_tail
            if (rel_tail and rel_tail.isdigit() and len(rel_tail) == 3)
            else (_extract_rel_tail_from_df(df_view) or "")
        )
        tail_parts = [p for p in [rrr, age_tok, date_tok] if p]
        final_tail = "_".join(tail_parts)

    base = f"Relatorio_analise_certificado_obra_{obra_slug}"
    if final_tail:
        return f"{base}_{final_tail}.pdf"
    if date_tok:
        return f"{base}_{date_tok}.pdf"
    from datetime import datetime as _dt

    return f"{base}_{_dt.utcnow().strftime('%d_%m_%Y')}.pdf"


def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    # remove duplicadas
    by_label = dict(zip(labels, handles))
    ax.legend(
        by_label.values(),
        by_label.keys(),
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        frameon=False,
        ncol=1,
        handlelength=2.2,
        handletextpad=0.8,
        labelspacing=0.35,
        prop={"size": 9},
    )
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
    uploaded_files = st.file_uploader(
        "📁 PDF(s)", type=["pdf"], accept_multiple_files=True, key=_uploader_key, help="Carregue 1 ou mais PDFs."
    )
else:
    up1 = st.file_uploader(
        "📁 PDF (1 arquivo)", type=["pdf"], accept_multiple_files=False, key=_uploader_key, help="Carregue 1 PDF."
    )
    uploaded_files = [up1] if up1 is not None else []
# =============================================================================
# Pipeline principal (continuação após o uploader)
# =============================================================================
if uploaded_files:
    frames = []
    progress_holder = st.empty()

    for idx, f in enumerate(uploaded_files, start=1):
        if f is None:
            continue
        progress_holder.info(f"📥 Lendo PDF {idx}/{len(uploaded_files)}: {getattr(f,'name','arquivo.pdf')}")
        df_i, obra_i, data_i, fck_i = extrair_dados_certificado(f)
        if not df_i.empty:
            df_i["Data Certificado"] = data_i
            df_i["Obra"] = obra_i
            # garante coluna Fck Projeto
            if "Fck Projeto" in df_i.columns:
                scalar_fck = _to_float_or_none(fck_i)
                if scalar_fck is not None:
                    df_i["Fck Projeto"] = pd.to_numeric(df_i["Fck Projeto"], errors="coerce").fillna(float(scalar_fck))
            else:
                df_i["Fck Projeto"] = fck_i
            df_i["Arquivo"] = getattr(f, "name", "arquivo.pdf")
            frames.append(df_i)
            log_event(
                "file_parsed",
                {
                    "file": getattr(f, "name", "arquivo.pdf"),
                    "rows": int(df_i.shape[0]),
                    "relatorios": int(df_i["Relatório"].nunique()),
                    "obra": obra_i,
                    "data_cert": data_i,
                },
            )
    progress_holder.empty()

    if not frames:
        st.error("⚠️ Não encontrei CPs válidos nos PDFs enviados.")
    else:
        # junta tudo
        df = pd.concat(frames, ignore_index=True)

        # ========= valida duplicidades de NF e CP =========
        has_nf_violation = False
        has_cp_violation = False

        if not df.empty:
            nf_rel = df.dropna(subset=["Nota Fiscal", "Relatório"]).astype({"Relatório": str})
            nf_multi = nf_rel.groupby(["Nota Fiscal"])["Relatório"].nunique().reset_index(name="n_rel")
            viol_nf = nf_multi[nf_multi["n_rel"] > 1]["Nota Fiscal"].tolist()
            if viol_nf:
                has_nf_violation = True
                detalhes = (
                    nf_rel[nf_rel["Nota Fiscal"].isin(viol_nf)]
                    .groupby(["Nota Fiscal", "Relatório"])["CP"]
                    .nunique()
                    .reset_index()
                )
                st.error("🚨 Nota Fiscal repetida em relatórios diferentes! Confira o PDF de origem.")
                st.dataframe(detalhes.rename(columns={"CP": "#CPs distintos"}), use_container_width=True)

            cp_rel = df.dropna(subset=["CP", "Relatório"]).astype({"Relatório": str})
            cp_multi = cp_rel.groupby(["CP"])["Relatório"].nunique().reset_index(name="n_rel")
            viol_cp = cp_multi[cp_multi["n_rel"] > 1]["CP"].tolist()
            if viol_cp:
                has_cp_violation = True
                detalhes_cp = (
                    cp_rel[cp_rel["CP"].isin(viol_cp)]
                    .groupby(["CP", "Relatório"])["Idade (dias)"]
                    .count()
                    .reset_index(name="#leituras")
                )
                st.error("🚨 CP repetido em relatórios diferentes!")
                st.dataframe(detalhes_cp, use_container_width=True)

        # ================ FILTROS ==================
        st.markdown("#### Filtros")
        fc1, fc2, fc3 = st.columns([2.0, 2.0, 1.0])

        with fc1:
            rels = sorted(df["Relatório"].astype(str).unique())
            saved_rels = s.get("last_sel_rels") or []
            default_rels = [str(r) for r in saved_rels if str(r) in rels]
            if not default_rels:
                default_rels = rels
            sel_rels = st.multiselect("Relatórios", rels, default=default_rels)

        # datas
        def _to_date(d):
            try:
                return datetime.strptime(str(d), "%d/%m/%Y").date()
            except Exception:
                return None

        df["_DataObj"] = df["Data Certificado"].apply(_to_date)
        valid_dates = [d for d in df["_DataObj"] if d is not None]

        with fc2:
            if valid_dates:
                dmin, dmax = min(valid_dates), max(valid_dates)
                last_range = s.get("last_date_range") or (dmin, dmax)
                # se range salvo ficou fora, volta
                if last_range[0] < dmin or last_range[1] > dmax:
                    last_range = (dmin, dmax)
                dini, dfim = st.date_input("Intervalo de data do certificado", last_range)
            else:
                dini, dfim = None, None

        with fc3:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Limpar filtros / Novo upload", use_container_width=True):
                s["uploader_key"] += 1
                st.rerun()

        s["last_sel_rels"] = sel_rels
        if dini and dfim:
            s["last_date_range"] = (dini, dfim)

        mask = df["Relatório"].astype(str).isin(sel_rels) if sel_rels else df["Relatório"].astype(str).isin(rels)
        if valid_dates and dini and dfim:
            mask = mask & df["_DataObj"].apply(lambda d: d is not None and dini <= d <= dfim)

        df_view = df.loc[mask].drop(columns=["_DataObj"]).copy()

        # ====== tratar múltiplos fck ======
        df_view["_FckLabel"] = df_view["Fck Projeto"].apply(_normalize_fck_label)
        fck_labels = list(dict.fromkeys(df_view["_FckLabel"]))
        multiple_fck_detected = len(fck_labels) > 1
        if multiple_fck_detected:
            st.warning("Detectamos múltiplos fck no conjunto selecionado. Escolha qual deseja analisar.")
            selected_fck_label = st.selectbox(
                "fck para análise", fck_labels, format_func=lambda x: x if x != "—" else "Não informado"
            )
            df_view = df_view[df_view["_FckLabel"] == selected_fck_label].copy()
        df_view = df_view.drop(columns=["_FckLabel"], errors="ignore")

        if df_view.empty:
            st.info("Nenhum dado disponível com os filtros atuais.")
            st.stop()

        # ===== estatísticas por CP/idade =====
        stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
            .agg(Média="mean", Desvio_Padrão="std", n="count")
            .reset_index()
        )

        # ===== outliers simples por idade =====
        outliers_df = None
        try:
            sigma = float(s.get("OUTLIER_SIGMA", 3.0))
            outs = []
            for age, sub in df_view.groupby("Idade (dias)"):
                subM = pd.to_numeric(sub["Resistência (MPa)"], errors="coerce")
                m = subM.mean()
                sd = subM.std()
                if pd.isna(sd) or sd == 0:
                    continue
                z = (subM - m) / sd
                mask_out = z.abs() > sigma
                if mask_out.any():
                    tmp = sub.loc[mask_out, ["CP", "Idade (dias)", "Resistência (MPa)", "Relatório"]].copy()
                    tmp["z"] = z[mask_out]
                    outs.append(tmp)
            if outs:
                outliers_df = pd.concat(outs).sort_values(["Idade (dias)", "CP"])
        except Exception:
            pass

        # ===================== SEÇÃO 1 =====================
        with st.expander("1) 📦 Dados lidos / visão geral", expanded=True):
            st.success("✅ Certificados lidos com sucesso e dados estruturados.")
            render_overview_and_tables(df_view, stats_cp_idade, float(s["TOL_MP"]), outliers_df=outliers_df)

        # ===================== SEÇÃO 2 — GRÁFICOS =====================
        with st.expander("2) 📊 Análises e gráficos (4 gráficos)", expanded=True):
            # controle de CP focado
            st.sidebar.subheader("🎯 Foco nos gráficos")
            cp_foco_manual = st.sidebar.text_input("Digitar CP p/ gráficos (opcional)", "", key="cp_manual")
            cp_select = st.sidebar.selectbox(
                "CP para gráficos", ["(Todos)"] + sorted(df_view["CP"].astype(str).unique()), key="cp_select"
            )
            cp_focus = (cp_foco_manual.strip() or (cp_select if cp_select != "(Todos)" else "")).strip()

            # df que vai para os gráficos
            if cp_focus:
                df_plot = df_view[df_view["CP"].astype(str) == cp_focus].copy()
            else:
                df_plot = df_view.copy()

            # ----- curva estimada global (sempre puxada do conjunto visível) -----
            mean_by_age_global = (
                df_view.groupby("Idade (dias)")["Resistência (MPa)"].mean().sort_index()
            )  # global, não só do cp
            # tentar estimar 28d
            est_df = None
            fck28_global = mean_by_age_global.get(28, float("nan"))
            fck7_global = mean_by_age_global.get(7, float("nan"))
            if pd.notna(fck28_global):
                est_df = pd.DataFrame(
                    {
                        "Idade (dias)": [7, 28, 63],
                        "Resistência (MPa)": [fck28_global * 0.65, fck28_global, fck28_global * 1.15],
                    }
                )
            elif pd.notna(fck7_global):
                _f28 = fck7_global / 0.70
                est_df = pd.DataFrame(
                    {
                        "Idade (dias)": [7, 28, 63],
                        "Resistência (MPa)": [float(fck7_global), float(_f28), float(_f28) * 1.15],
                    }
                )

            # fck ativo (do conjunto visível)
            fck_series_all = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
            fck_active = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None

            # ===== Gráfico 1
            st.write("##### Gráfico 1 — Crescimento da Resistência (Real)")
            fig1, ax = plt.subplots(figsize=(9.4, 4.8))
            # linhas por CP (reais)
            for cp, sub in df_plot.groupby("CP"):
                sub = sub.sort_values("Idade (dias)")
                ax.plot(
                    sub["Idade (dias)"],
                    sub["Resistência (MPa)"],
                    marker="o",
                    linewidth=1.6,
                    label=f"CP {cp}",
                )
            # se tiver curva estimada global, desenha em cinza claro
            if est_df is not None:
                ax.plot(
                    est_df["Idade (dias)"],
                    est_df["Resistência (MPa)"],
                    linestyle="--",
                    linewidth=1.5,
                    color="#d1d5db",
                    label="Curva estimada (global)",
                )
            if fck_active is not None:
                ax.axhline(fck_active, linestyle=":", linewidth=2, color="#ef4444", label=f"fck projeto ({fck_active:.1f} MPa)")
            ax.set_xlabel("Idade (dias)")
            ax.set_ylabel("Resistência (MPa)")
            ax.set_title("Crescimento da resistência por corpo de prova")
            ax.grid(True, linestyle="--", alpha=0.35)
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            place_right_legend(ax)
            st.pyplot(fig1)

            # ===== Gráfico 2
            st.write("##### Gráfico 2 — Curva Estimada (referência técnica)")
            if est_df is not None:
                fig2, ax2 = plt.subplots(figsize=(7.6, 4.4))
                # curva estimada em cinza
                ax2.plot(
                    est_df["Idade (dias)"],
                    est_df["Resistência (MPa)"],
                    linestyle="--",
                    marker="o",
                    linewidth=2,
                    color="#d1d5db",
                    label="Curva estimada (global)",
                )
                # e sobrepõe médias reais globais (se houver)
                if not mean_by_age_global.empty:
                    ax2.plot(
                        mean_by_age_global.index,
                        mean_by_age_global.values,
                        marker="s",
                        linewidth=1.8,
                        label="Média real (global)",
                    )
                if fck_active is not None:
                    ax2.axhline(
                        fck_active,
                        linestyle=":",
                        linewidth=2,
                        color="#ef4444",
                        label=f"fck projeto ({fck_active:.1f} MPa)",
                    )
                ax2.set_xlabel("Idade (dias)")
                ax2.set_ylabel("Resistência (MPa)")
                ax2.set_title("Curva estimada (referência, não critério normativo)")
                ax2.grid(True, linestyle="--", alpha=0.45)
                place_right_legend(ax2)
                st.pyplot(fig2)
            else:
                st.info("Não foi possível calcular a curva estimada (sem médias em 7 ou 28 dias).")

            # ===== Gráfico 3 — comparação médias x estimado
            st.write("##### Gráfico 3 — Comparação Real × Estimado (médias)")
            cond_df = None
            if est_df is not None:
                fig3, ax3 = plt.subplots(figsize=(9.4, 4.8))
                # médias reais do df_plot (se CP único, fica só dele)
                stats_focus = (
                    df_plot.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index()
                )
                ax3.plot(
                    stats_focus["Idade (dias)"],
                    stats_focus["mean"],
                    marker="s",
                    linewidth=2,
                    label="Média real (filtro)",
                )
                # banda se tiver DP
                sf_dp = stats_focus.dropna(subset=["std"]).copy()
                if not sf_dp.empty:
                    ax3.fill_between(
                        sf_dp["Idade (dias)"],
                        sf_dp["mean"] - sf_dp["std"],
                        sf_dp["mean"] + sf_dp["std"],
                        alpha=0.2,
                        label="Real ±1 DP",
                    )
                # curva estimada global cinza
                ax3.plot(
                    est_df["Idade (dias)"],
                    est_df["Resistência (MPa)"],
                    linestyle="--",
                    marker="o",
                    linewidth=2,
                    color="#d1d5db",
                    label="Estimado (global)",
                )
                if fck_active is not None:
                    ax3.axhline(
                        fck_active,
                        linestyle=":",
                        linewidth=2,
                        color="#ef4444",
                        label=f"fck projeto ({fck_active:.1f} MPa)",
                    )
                ax3.set_xlabel("Idade (dias)")
                ax3.set_ylabel("Resistência (MPa)")
                ax3.set_title("Comparação Real × Estimado (médias)")
                ax3.grid(True, linestyle="--", alpha=0.45)
                place_right_legend(ax3)
                st.pyplot(fig3)

                # tabelinha de condição
                TOL = float(s["TOL_MP"])
                cond_rows = []
                for idade in [7, 28, 63]:
                    real_val = stats_focus.loc[stats_focus["Idade (dias)"] == idade, "mean"]
                    real_val = float(real_val.iloc[0]) if not real_val.empty else float("nan")
                    est_val = est_df.set_index("Idade (dias)")["Resistência (MPa)"].get(idade, float("nan"))
                    if pd.isna(real_val) or pd.isna(est_val):
                        delta = float("nan")
                        status = "⚪ Sem dados"
                    else:
                        delta = real_val - est_val
                        if abs(delta) <= TOL:
                            status = "✅ Dentro"
                        else:
                            status = "🔵 Acima" if delta > 0 else "🔴 Abaixo"
                    cond_rows.append(
                        {
                            "Idade (dias)": idade,
                            "Média Real (MPa)": real_val,
                            "Estimado (MPa)": est_val,
                            "Δ (Real-Est.)": delta,
                            "Status": status,
                        }
                    )
                cond_df = pd.DataFrame(cond_rows)
                st.write("#### 📊 Condição Real × Estimado (médias)")
                st.dataframe(cond_df, use_container_width=True)
            else:
                st.info("Sem curva estimada → não é possível comparar médias (Gráfico 3).")

            # ===== Gráfico 4 — pareamento ponto-a-ponto com curva única cinza
            st.write("##### Gráfico 4 — Real × Estimado ponto-a-ponto (por CP)")
            if est_df is not None:
                est_map = dict(zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]))
                fig4, ax4 = plt.subplots(figsize=(10.2, 4.9))
                pares = []
                for cp, sub in df_plot.groupby("CP"):
                    sub = sub.sort_values("Idade (dias)")
                    # linha real do CP
                    ax4.plot(
                        sub["Idade (dias)"],
                        sub["Resistência (MPa)"],
                        marker="o",
                        linewidth=1.6,
                        label=f"CP {cp} — Real",
                    )
                    # pareamento com a curva única cinza
                    x_est = []
                    y_est = []
                    for _, r in sub.iterrows():
                        idade = int(r["Idade (dias)"])
                        if idade in est_map:
                            x_est.append(idade)
                            y_est.append(float(est_map[idade]))
                            real_v = float(r["Resistência (MPa)"])
                            est_v = float(est_map[idade])
                            delta = real_v - est_v
                            TOL = float(s["TOL_MP"])
                            status = "✅ OK" if abs(delta) <= TOL else ("🔵 Acima" if delta > 0 else "🔴 Abaixo")
                            pares.append([str(cp), idade, real_v, est_v, delta, status])
                            ax4.vlines(idade, min(real_v, est_v), max(real_v, est_v), linestyles=":", linewidth=1)
                    # desenha estimado do CP usando a mesma curva cinza
                    if x_est:
                        ax4.plot(
                            x_est,
                            y_est,
                            marker="^",
                            linestyle="--",
                            linewidth=1.5,
                            color="#d1d5db",
                            label=f"CP {cp} — Est.",
                        )

                if fck_active is not None:
                    ax4.axhline(
                        fck_active,
                        linestyle=":",
                        linewidth=2,
                        color="#ef4444",
                        label=f"fck projeto ({fck_active:.1f} MPa)",
                    )
                ax4.set_xlabel("Idade (dias)")
                ax4.set_ylabel("Resistência (MPa)")
                ax4.set_title("Pareamento Real × Estimado por CP (curva única cinza)")
                ax4.grid(True, linestyle="--", alpha=0.4)
                place_right_legend(ax4)
                st.pyplot(fig4)

                pareamento_df = pd.DataFrame(
                    pares,
                    columns=["CP", "Idade (dias)", "Real (MPa)", "Estimado (MPa)", "Δ", "Status"],
                ).sort_values(["CP", "Idade (dias)"])
                st.write("#### 📑 Pareamento ponto-a-ponto (tela)")
                st.dataframe(pareamento_df, use_container_width=True)
            else:
                st.info("Sem curva estimada → não é possível parear pontos (Gráfico 4).")

        # ===================== SEÇÃO 3 — verificação fck =====================
        with st.expander("3) ✅ Verificação do fck / CP detalhado", expanded=True):
            st.write("#### ✅ Verificação do fck de Projeto (3, 7, 14, 28, 63 dias quando tiver)")
            fck_series_all2 = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
            fck_active2 = float(fck_series_all2.mode().iloc[0]) if not fck_series_all2.empty else None

            mean_by_age_plot = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].mean()
            m3 = mean_by_age_plot.get(3, float("nan"))
            m7 = mean_by_age_plot.get(7, float("nan"))
            m14 = mean_by_age_plot.get(14, float("nan"))
            m28 = mean_by_age_plot.get(28, float("nan"))
            m63 = mean_by_age_plot.get(63, float("nan"))

            verif_fck_df2 = pd.DataFrame(
                {
                    "Idade (dias)": [3, 7, 14, 28, 63],
                    "Média Real (MPa)": [m3, m7, m14, m28, m63],
                    "fck Projeto (MPa)": [
                        float("nan"),
                        (fck_active2 if fck_active2 is not None else float("nan")),
                        (fck_active2 if fck_active2 is not None else float("nan")),
                        (fck_active2 if fck_active2 is not None else float("nan")),
                        (fck_active2 if fck_active2 is not None else float("nan")),
                    ],
                }
            )
            resumo_status = []
            for idade, media, fckp in verif_fck_df2.itertuples(index=False):
                if pd.isna(media) or (pd.isna(fckp) and idade != 3):
                    resumo_status.append("⚪ Sem dados")
                else:
                    if idade in (3, 7, 14):
                        resumo_status.append("🟡 Analisando")
                    else:
                        resumo_status.append("🟢 Atingiu fck" if float(media) >= float(fckp) else "🔴 Não atingiu fck")
            verif_fck_df2["Status"] = resumo_status
            st.dataframe(verif_fck_df2, use_container_width=True)

            # tabela por CP
            idades_interesse = [3, 7, 14, 28, 63]
            tmp_v = df_view[df_view["Idade (dias)"].isin(idades_interesse)].copy()
            pv_cp_status = None
            if tmp_v.empty:
                st.info("Sem CPs de 3/7/14/28/63 dias no filtro atual.")
            else:
                tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
                tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1
                pv_multi = tmp_v.pivot_table(
                    index="CP", columns=["Idade (dias)", "rep"], values="MPa", aggfunc="first"
                ).sort_index(axis=1)

                # garante colunas
                for age in idades_interesse:
                    if age not in pv_multi.columns.get_level_values(0):
                        pv_multi[(age, 1)] = pd.NA

                def _flat(age, rep):
                    base = f"{age}d"
                    return f"{base} (MPa)" if rep == 1 else f"{base} #{rep} (MPa)"

                pv = pv_multi.copy()
                pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
                pv = pv.reset_index()

                try:
                    pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
                except Exception:
                    pv["__cp_sort__"] = range(len(pv))
                pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")

                # status por idade
                status_cols = {}
                for age in idades_interesse:
                    colname = f"Status {age}d"
                    colvals = []
                    for _, row in pv.iterrows():
                        # média simples desse CP para essa idade
                        vals_age = [row[c] for c in pv.columns if c.startswith(f"{age}d")]
                        vals_age = [v for v in vals_age if not pd.isna(v)]
                        if not vals_age or fck_active2 is None:
                            colvals.append("⚪ Sem dados")
                        else:
                            if age in (3, 7, 14):
                                colvals.append("🟡 Analisando")
                            else:
                                med_cp = float(pd.Series(vals_age).mean())
                                colvals.append("🟢 Atingiu fck" if med_cp >= float(fck_active2) else "🔴 Não atingiu fck")
                    status_cols[colname] = colvals

                for colname, colvals in status_cols.items():
                    pv[colname] = colvals

                pv_cp_status = pv.copy()
                st.dataframe(pv_cp_status, use_container_width=True)

        # ===================== SEÇÃO 4 — Exportações =====================
        with st.expander("4) ⬇️ Exportações", expanded=True):
            st.markdown("##### ✅ Checklist antes de exportar")
            checks = []
            checks.append(("✅ Dados disponíveis", not df_view.empty))
            checks.append(("✅ Sem falha de leitura", True))
            if has_nf_violation:
                checks.append(("⚠️ Há Nota Fiscal em mais de um relatório", False))
            if has_cp_violation:
                checks.append(("⚠️ Há CP em mais de um relatório", False))
            if multiple_fck_detected:
                checks.append(("⚠️ Múltiplos fck detectados — filtrado para 1", True))
            for label, ok in checks:
                cor = "#16a34a" if ok else "#f97316"
                st.markdown(f"<div style='color:{cor};font-size:13px;margin-bottom:3px;'>{label}</div>", unsafe_allow_html=True)

            report_mode = st.radio(
                "Modo do relatório PDF",
                [
                    "Relatório técnico completo",
                    "Relatório resumido (cliente)",
                    "Conferência rápida (tabelas)",
                ],
                index=0,
            )

            def gerar_pdf(df: pd.DataFrame, stats: pd.DataFrame, figs: dict, extra: dict) -> bytes:
                # reaproveita o gerador que você já tinha
                # para não estourar aqui, vou usar o mesmo que estava na sua versão anterior
                # copiando a lógica principal:
                from reportlab.lib import colors as _C

                obra_label = extra.get("obra_label", "—")
                data_label = extra.get("data_label", "—")
                fck_label = extra.get("fck_label", "—")
                qr_url = extra.get("qr_url", "")
                responsavel = extra.get("responsavel", "")
                cliente = extra.get("cliente", "")
                cidade = extra.get("cidade", "")
                verif_fck_df = extra.get("verif_fck_df")
                cond_df = extra.get("cond_df")
                pv_cp_status = extra.get("pv_cp_status")

                use_landscape = (len(df.columns) >= 8)
                pagesize = landscape(A4) if use_landscape else A4
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(
                    buffer,
                    pagesize=pagesize,
                    leftMargin=18,
                    rightMargin=18,
                    topMargin=26,
                    bottomMargin=56,
                )
                styles = getSampleStyleSheet()
                styles["Normal"].fontSize = 9
                story = []

                # cabeçalho
                story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles["Title"]))
                story.append(Paragraph("Relatório de Rompimento de Corpos de Prova", styles["Heading2"]))
                story.append(Paragraph(f"Obra: {obra_label}", styles["Normal"]))
                story.append(Paragraph(f"Período (datas dos certificados): {data_label}", styles["Normal"]))
                story.append(Paragraph(f"fck de projeto: {fck_label}", styles["Normal"]))
                if cliente:
                    story.append(Paragraph(f"Cliente / Empreendimento: {cliente}", styles["Normal"]))
                if cidade:
                    story.append(Paragraph(f"Cidade / UF: {cidade}", styles["Normal"]))
                if responsavel:
                    story.append(Paragraph(f"Responsável técnico: {responsavel}", styles["Normal"]))
                if qr_url:
                    story.append(Paragraph(f"Resumo/QR: {qr_url}", styles["Normal"]))
                story.append(Spacer(1, 8))

                # tabela principal
                headers = [
                    "Relatório",
                    "CP",
                    "Idade (dias)",
                    "Resistência (MPa)",
                    "Nota Fiscal",
                    "Local",
                    "Usina",
                    "Abatimento NF (mm)",
                    "Abatimento Obra (mm)",
                    "Arquivo",
                ]
                rows = df[headers].values.tolist()
                table = Table([headers] + rows, repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), _C.lightgrey),
                            ("GRID", (0, 0), (-1, -1), 0.5, _C.black),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                        ]
                    )
                )
                story.append(table)
                story.append(Spacer(1, 8))

                # gráficos (se modo completo)
                if report_mode in ("Relatório técnico completo", "Relatório resumido (cliente)"):
                    for key in ["fig1", "fig2", "fig3", "fig4"]:
                        if figs.get(key) is not None:
                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            figs[key].savefig(tmp.name, dpi=200, bbox_inches="tight")
                            story.append(RLImage(tmp.name, width=620, height=420))
                            story.append(Spacer(1, 8))

                # verificação fck
                if report_mode == "Relatório técnico completo":
                    if verif_fck_df is not None and not verif_fck_df.empty:
                        story.append(Paragraph("Verificação do fck de Projeto", styles["Heading3"]))
                        rows_v = [["Idade (dias)", "Média Real (MPa)", "fck Projeto (MPa)", "Status"]]
                        for _, r in verif_fck_df.iterrows():
                            rows_v.append(
                                [
                                    r["Idade (dias)"],
                                    f"{r['Média Real (MPa)']:.3f}" if pd.notna(r["Média Real (MPa)"]) else "—",
                                    f"{r['fck Projeto (MPa)']:.3f}" if pd.notna(r["fck Projeto (MPa)"]) else "—",
                                    r["Status"],
                                ]
                            )
                        tv = Table(rows_v, repeatRows=1)
                        tv.setStyle(
                            TableStyle(
                                [
                                    ("BACKGROUND", (0, 0), (-1, 0), _C.lightgrey),
                                    ("GRID", (0, 0), (-1, -1), 0.5, _C.black),
                                    ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                                ]
                            )
                        )
                        story.append(tv)
                        story.append(Spacer(1, 6))

                    if cond_df is not None and not cond_df.empty:
                        story.append(Paragraph("Condição Real × Estimado (médias)", styles["Heading3"]))
                        rows_c = [list(cond_df.columns)] + cond_df.values.tolist()
                        tc = Table(rows_c, repeatRows=1)
                        tc.setStyle(
                            TableStyle(
                                [
                                    ("BACKGROUND", (0, 0), (-1, 0), _C.lightgrey),
                                    ("GRID", (0, 0), (-1, -1), 0.5, _C.black),
                                    ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                                ]
                            )
                        )
                        story.append(tc)
                        story.append(Spacer(1, 6))

                    if pv_cp_status is not None and not pv_cp_status.empty:
                        story.append(Paragraph("Verificação detalhada por CP", styles["Heading3"]))
                        cols = list(pv_cp_status.columns)
                        t2 = Table([cols] + pv_cp_status.values.tolist(), repeatRows=1)
                        t2.setStyle(
                            TableStyle(
                                [
                                    ("BACKGROUND", (0, 0), (-1, 0), _C.lightgrey),
                                    ("GRID", (0, 0), (-1, -1), 0.4, _C.black),
                                    ("FONTSIZE", (0, 0), (-1, -1), 7.6),
                                ]
                            )
                        )
                        story.append(t2)

                doc.build(story, canvasmaker=NumberedCanvas)
                pdf = buffer.getvalue()
                buffer.close()
                return pdf

            # só exporta se tiver dado
            if not df_view.empty:
                # prepara datas e labels
                datas_validas = [x for x in df_view["Data Certificado"].dropna().tolist()]
                d_label = "—"
                if datas_validas:
                    d_objs = [_to_date_obj(x) for x in datas_validas]
                    d_objs = [d for d in d_objs if d is not None]
                    if d_objs:
                        di, df_ = min(d_objs), max(d_objs)
                        d_label = di.strftime("%d/%m/%Y") if di == df_ else f"{di.strftime('%d/%m/%Y')} — {df_.strftime('%d/%m/%Y')}"
                obra_label = (
                    str(df_view["Obra"].mode().iat[0])
                    if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty
                    else "—"
                )
                fck_label = _format_float_label(fck_active)

                figs_to_pdf = {"fig1": fig1, "fig2": (fig2 if "fig2" in locals() else None), "fig3": (None), "fig4": (None)}
                if "fig3" in locals():
                    figs_to_pdf["fig3"] = fig3
                if "fig4" in locals():
                    figs_to_pdf["fig4"] = fig4

                pdf_bytes = gerar_pdf(
                    df_view,
                    stats_cp_idade,
                    figs_to_pdf,
                    {
                        "obra_label": obra_label,
                        "data_label": d_label,
                        "fck_label": fck_label,
                        "qr_url": s.get("qr_url", ""),
                        "responsavel": s.get("rt_responsavel", ""),
                        "cliente": s.get("rt_cliente", ""),
                        "cidade": s.get("rt_cidade", ""),
                        "verif_fck_df": verif_fck_df2,
                        "cond_df": cond_df,
                        "pv_cp_status": (pv_cp_status if "pv_cp_status" in locals() else None),
                    },
                )

                file_name_pdf = build_pdf_filename(df_view, uploaded_files)
                st.download_button(
                    "📄 Baixar Relatório (PDF)",
                    data=pdf_bytes,
                    file_name=file_name_pdf,
                    mime="application/pdf",
                    use_container_width=True,
                )

                # Excel
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_view.to_excel(writer, sheet_name="Individuais", index=False)
                    stats_cp_idade.to_excel(writer, sheet_name="Medias_DP", index=False)
                    if cond_df is not None:
                        cond_df.to_excel(writer, sheet_name="Condicao", index=False)
                st.download_button(
                    "📊 Baixar Excel (XLSX)",
                    data=excel_buffer.getvalue(),
                    file_name="Relatorio_certificados.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

                # CSVs ZIP
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
                    z.writestr("Medias_DP.csv", stats_cp_idade.to_csv(index=False, sep=";"))
                    if cond_df is not None:
                        z.writestr("Condicao.csv", cond_df.to_csv(index=False, sep=";"))
                st.download_button(
                    "🗃️ Baixar CSVs (ZIP)",
                    data=zip_buf.getvalue(),
                    file_name="Relatorio_certificados_csv.zip",
                    mime="application/zip",
                    use_container_width=True,
                )

        # botão de recomeçar
        if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
            s["uploader_key"] += 1
            st.rerun()

else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# rodapé
st.markdown("---")
st.subheader("📘 Normas de Referência")
st.markdown(
    """
- **NBR 5738** – Concreto: Procedimento para moldagem e cura de corpos de prova  
- **NBR 5739** – Concreto: Ensaio de compressão de corpos de prova cilíndricos  
- **NBR 12655** – Concreto de cimento Portland: Preparo, controle e recebimento  
- **NBR 7215** – Cimento Portland: Determinação da resistência à compressão
"""
)
st.markdown(
    """
<div style="text-align:center; font-size:18px; font-weight:600; opacity:.9; margin-top:10px;">
  Sistema desenvolvido por IA e pela Habisolute Engenharia
</div>
""",
    unsafe_allow_html=True,
)
