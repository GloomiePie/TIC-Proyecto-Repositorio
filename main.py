import requests
import fitz  # PyMuPDF
from io import BytesIO
import os

# === CONFIGURACIÓN ===
# Repositorio: usuario/repositorio
USER = "GloomiePie"
REPO = "TIC-Proyecto-Repositorio"
BRANCH = "corpus-juridico"
PATH = "corpus juridico/Sentencias Penales"  # Carpeta dentro del repo

# Carpeta donde se guardarán los textos procesados
CARPETA_TEXTOS = "textos"
os.makedirs(CARPETA_TEXTOS, exist_ok=True)

def listar_pdfs_en_github(user: str, repo: str, branch: str, path: str):
    """
    Lista los archivos PDF en una carpeta de un repositorio GitHub usando la API.
    """
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{path}?ref={branch}"
    resp = requests.get(url)
    resp.raise_for_status()
    archivos = resp.json()

    # Filtrar solo PDFs
    return [f["download_url"] for f in archivos if f["name"].lower().endswith(".pdf")]

def pdf_a_texto_desde_url(url_pdf: str) -> str:
    """
    Descarga un PDF desde una URL y lo convierte en texto limpio.
    """
    resp = requests.get(url_pdf)
    resp.raise_for_status()

    texto_completo = []
    with fitz.open(stream=BytesIO(resp.content), filetype="pdf") as pdf:
        for num_pagina, pagina in enumerate(pdf, start=1):
            texto = pagina.get_text("text")
            if texto.strip():
                texto_completo.append(texto)

    return "\n".join(texto_completo).strip()


if __name__ == "__main__":
    print("📥 Listando PDFs en GitHub...")
    pdf_urls = listar_pdfs_en_github(USER, REPO, BRANCH, PATH)

    print(f"Encontrados {len(pdf_urls)} PDFs en la carpeta.\n")

    for url in pdf_urls:
        print(f"Procesando: {url}")
        texto = pdf_a_texto_desde_url(url)

        # Guardar en la carpeta textos con el mismo nombre del PDF
        nombre_archivo = url.split("/")[-1].replace(".pdf", ".txt")
        ruta_salida = os.path.join(CARPETA_TEXTOS, nombre_archivo)
        with open(ruta_salida, "w", encoding="utf-8") as f:
            f.write(texto)

        print(f"✅ Guardado texto en {ruta_salida}\n")
