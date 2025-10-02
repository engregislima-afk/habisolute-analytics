# app.py — 🏗️ Habisolute Analytics (Multi-FCK completo)
# Requisitos: streamlit, pandas, pdfplumber, matplotlib, reportlab, xlsxwriter

import io, re, json, base64, tempfile, zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

try:
    import streamlit as st
except ImportError:
    st = None

# PDF (ReportLab)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente as amostras ensaiadas, portanto esse documento poderá ser "
    "reproduzido somente na integra. Resultados sem considerar a incerteza da medição."
)

class NumberedCanvas(pdfcanvas.Canvas):
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
            self._draw_footer_and_pagenum(total_pages)
            super().showPage()
        super().save()

    def _draw_footer_and_pagenum(self, total_pages: int):
        w, h = self._pagesize
        self.setFont("Helvetica", 7)
        self.drawString(18, 15, FOOTER_TEXT)
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, 15, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Utilidades
# =============================================================================
def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc="upper left", bbox_to_anchor=(1.02, 1.0), frameon=False)

def _img_from_fig(_fig, w=400, h=260):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    _fig.savefig(tmp.name, dpi=200, bbox_inches="tight")
    return RLImage(tmp.name, width=w, height=h)

# =============================================================================
# Geração PDF Multi-FCK
# =============================================================================
def gerar_pdf_multi(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
    story.append(Paragraph("Relatório Multi-FCK", styles['Heading2']))
    story.append(Spacer(1, 12))

    for f in sorted(df["Fck Projeto"].dropna().unique()):
        story.append(Paragraph(f"<b>FCK {f:.0f} MPa</b>", styles['Heading2']))
        sub = df[df["Fck Projeto"] == f]
        headers = list(sub.columns)
        rows = sub.values.tolist()
        table = Table([headers] + rows, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.5),
        ]))
        story.append(table)
        story.append(PageBreak())

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

# =============================================================================
# Pipeline principal (Streamlit)
# =============================================================================
if st:
    st.set_page_config(page_title="Habisolute — Multi-FCK", layout="wide")
    st.markdown("### 📁 Upload de certificados PDF")
    up = st.file_uploader("Carregar certificados", type=["pdf"], accept_multiple_files=True)
    if up:
        frames = []
        for f in up:
            # Simulação: em produção use extrair_dados_certificado
            df_i = pd.DataFrame({
                "CP": ["001","002","003"],
                "Idade (dias)": [7,28,63],
                "Resistência (MPa)": [20,30,40],
                "Fck Projeto": [30,30,30]
            })
            frames.append(df_i)
        df = pd.concat(frames, ignore_index=True)

        st.write("#### Dados Consolidados")
        st.dataframe(df, use_container_width=True)

        for f in sorted(df["Fck Projeto"].dropna().unique()):
            st.write(f"### 📊 FCK {f:.0f} MPa")
            sub = df[df["Fck Projeto"]==f]
            fig, ax = plt.subplots()
            for cp, g in sub.groupby("CP"):
                ax.plot(g["Idade (dias)"], g["Resistência (MPa)"], marker="o", label=f"CP {cp}")
            ax.set_title(f"Crescimento da resistência — FCK {f:.0f}")
            ax.set_xlabel("Idade (dias)"); ax.set_ylabel("Resistência (MPa)")
            place_right_legend(ax)
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            st.pyplot(fig)

        pdf_bytes = gerar_pdf_multi(df)
        st.download_button("📄 Baixar Relatório Multi-FCK (PDF)", pdf_bytes,
                           file_name="Relatorio_MultiFCK.pdf", mime="application/pdf")

        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine="xlsxwriter") as writer:
            for f in sorted(df["Fck Projeto"].dropna().unique()):
                sub = df[df["Fck Projeto"]==f]
                sub.to_excel(writer, sheet_name=f"FCK{int(f)}", index=False)
        st.download_button("📊 Baixar Excel Multi-FCK", excel_buf.getvalue(),
                           file_name="Relatorio_MultiFCK.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(df["Fck Projeto"].dropna().unique()):
                sub = df[df["Fck Projeto"]==f]
                z.writestr(f"FCK{int(f)}.csv", sub.to_csv(index=False, sep=";"))
        st.download_button("🗃️ Baixar CSVs Multi-FCK (ZIP)", zip_buf.getvalue(),
                           file_name="Relatorio_MultiFCK.zip", mime="application/zip")

        st.markdown("---")
        st.subheader("📘 Normas de Referência")
        st.markdown("""
        - **NBR 5738** – Moldagem e cura de corpos de prova
        - **NBR 5739** – Ensaio de compressão de corpos de prova
        - **NBR 12655** – Preparo, controle e recebimento do concreto
        - **NBR 7215** – Resistência à compressão do cimento Portland
        """)
        st.markdown("<div style='text-align:center; font-size:16px; font-weight:600'>Sistema Habisolute Engenharia</div>", unsafe_allow_html=True)
