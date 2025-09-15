import os
import re
import json
import glob
import pathlib
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Clave directa para LangExtract  ---
os.environ["LANGEXTRACT_API_KEY"] = "API_KEY_AQUI"
os.environ["GEMINI_API_KEY"] = "API_KEY_AQUI" 

# ---------- Configuración de modelo ----------
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ---------- Esquema destino ----------
# Igualamos el formato del archivo "Sentencia 1 – Ataque Informático.txt":
# Secciones y etiquetas en el orden exacto.
SECCIONES = [
    ("pregunta_del_problema", "PREGUNTA DEL PROBLEMA"),
    ("cuestion_normativa", "CUESTIÓN NORMATIVA"),
    ("respuestas_razones_normativa", "RESPUESTAS O RAZONES"),
    ("cuestion_factica", "CUESTION FÁCTICA"),  # Coincide con el texto del ejemplo (sin tilde en "CUestion").
    ("respuestas_razones_factica", "RESPUESTAS O RAZONES"),
    ("decision", "DECISIÓN"),
]

SYSTEM_INSTRUCTION = """Eres un asistente jurídico que extrae y estructura información de SENTENCIAS penales.
Debes devolver SIEMPRE un JSON con la siguiente forma EXACTA y en español (sin texto extra):
{
 "pregunta_del_problema": "...",
 "cuestion_normativa": "...",
 "respuestas_razones_normativa": "...",
 "cuestion_factica": "...",
 "respuestas_razones_factica": "...",
 "decision": "..."
}
Reglas:
- Lee TODA la sentencia base y sintetiza con precisión legal.
- No inventes. Si no hay dato, deja una frase breve que lo indique.
- No cambies los nombres de las secciones JSON.
- Estilo y nivel de detalle coherente con los ejemplos.
"""

PROMPT_USER_TEMPLATE = """TEXTO DE SENTENCIA (CRUDO):
---
{texto_crudo}
---

TAREA:
1) Analiza la sentencia cruda.
2) Devuelve SOLO el JSON solicitado, sin comentarios.
"""

@dataclass
class Ejemplo:
    crudo: str
    estructurado: str

def leer_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()

def cargar_ejemplos(base_dir: str = "textos/ejemplos") -> List[Ejemplo]:
    """
    Espera subcarpetas tipo textos/ejemplos/ejX/ con crudo.txt y estructurado.txt
    """
    ejemplos: List[Ejemplo] = []
    for ej_dir in sorted(glob.glob(os.path.join(base_dir, "*"))):
        crudo = os.path.join(ej_dir, "crudo.txt")
        estruct = os.path.join(ej_dir, "estructurado.txt")
        if os.path.isfile(crudo) and os.path.isfile(estruct):
            ejemplos.append(Ejemplo(crudo=leer_txt(crudo), estructurado=leer_txt(estruct)))
    return ejemplos

def normalizar_ws(s: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\r\n?", "\n", s)).strip()

def render_txt_salida(datos: Dict[str, str]) -> str:
    """
    Convierte el JSON al .txt con las etiquetas idénticas al ejemplo.
    """
    bloques = []
    for key, etiqueta in SECCIONES:
        valor = (datos.get(key) or "").strip()
        bloques.append(f"{etiqueta}: {valor}")
    return "\n".join(bloques).strip() + "\n"

# ---------- Vía 1: LangExtract (si está instalado) ----------
def intentar_con_langextract(
    ejemplos: List[Ejemplo],
    texto_objetivo: str,
    model_name: str = DEFAULT_MODEL
) -> Optional[Dict]:
    try:
        # Estas APIs pueden variar según tu versión de LangExtract.
        # Se asume que LangExtract resuelve el LLM a partir del nombre (gemini-*).
        from langextract import FewShotExtractor, Example as LEExample, LanguageModel

        lm = LanguageModel.from_name(model_name)
        le_ejemplos = []
        # Cada ejemplo se provee como par input->output (el output es el TXT estructurado).
        # Pedimos que LangExtract aprenda el mapeo y nos retorne JSON.
        # Si tu LangExtract requiere "schema", cámbialo por el dict de SECCIONES.
        for e in ejemplos:
            # Forzamos a que el target del ejemplo esté en el formato .txt esperado
            # y pedimos internamente que LangExtract lo induzca.
            pair_input = PROMPT_USER_TEMPLATE.format(texto_crudo=e.crudo)
            pair_output = e.estructurado
            le_ejemplos.append(LEExample(input=pair_input, output=pair_output))

        extractor = FewShotExtractor(
            lm=lm,
            instruction=SYSTEM_INSTRUCTION + "\nDevuelve JSON.",
            examples=le_ejemplos,
            response_format="json"
        )

        prompt_actual = PROMPT_USER_TEMPLATE.format(texto_crudo=texto_objetivo)
        result = extractor.extract(prompt_actual)
        # Se espera un dict con las claves del schema. Si llega texto, intentamos parsear.
        if isinstance(result, dict):
            return result
        try:
            return json.loads(result)
        except Exception:
            return None
    except Exception:
        return None

# ---------- Vía 2: Gemini directa ----------
def llamar_gemini_directo(
    ejemplos: List[Ejemplo],
    texto_objetivo: str,
    model_name: str = DEFAULT_MODEL
) -> Dict:
    """
    Llama a Gemini para extraer JSON con el esquema indicado en SYSTEM_INSTRUCTION.
    - Construye un prompt few-shot a partir de `ejemplos` (cada ejemplo: crudo -> estructurado).
    - Lee GEMINI_API_KEY en tiempo de ejecución (no cachea).
    - Soporta tanto el SDK antiguo (`google-generativeai`) como el nuevo (`from google import genai`).
    """
    # 1) Clave en tiempo de ejecución
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY en el entorno (.env, variable del sistema o hardcode).")

    # 2) Construir few-shot a partir de los ejemplos
    shots: List[str] = []
    for e in ejemplos:
        shots.append(
            f"""### EJEMPLO
{PROMPT_USER_TEMPLATE.format(texto_crudo=e.crudo)}

SALIDA ESPERADA (TEXTO ESTRUCTURADO):
---
{e.estructurado}
---"""
        )
    fewshot_block = "\n\n".join(shots)
    prompt_user = PROMPT_USER_TEMPLATE.format(texto_crudo=texto_objetivo)

    full_prompt = (
        SYSTEM_INSTRUCTION
        + "\n\n"
        + (fewshot_block + "\n\n" if fewshot_block else "")
        + "### CASO A RESOLVER\n"
        + prompt_user
    )

    # 3) Invocación al modelo (SDK antiguo o nuevo)
    text: str
    try:
        # SDK antiguo: google-generativeai
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={
                "temperature": 0.1,
                "top_p": 0.9,
                "max_output_tokens": 4096,
                "response_mime_type": "application/json",
            },
        )
        resp = model.generate_content(full_prompt)
        # .text suele contener el agregado de todas las partes
        text = getattr(resp, "text", None) or str(resp)
    except Exception:
        # SDK nuevo: google-ai-python (from google import genai)
        from google import genai as genai_new
        client = genai_new.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model_name,
            contents=full_prompt,
            # El SDK nuevo acepta "config" con dict simple.
            config={
                "temperature": 0.1,
                "top_p": 0.9,
                "max_output_tokens": 4096,
                "response_mime_type": "application/json",
            },
        )
        # En el SDK nuevo, resp.text también suele existir; si no, intenta alternativas.
        text = getattr(resp, "text", None) or getattr(resp, "output_text", None) or str(resp)

    # 4) Parseo robusto de JSON
    try:
        data = json.loads(text)
    except Exception:
        # Intento extraer el último bloque JSON bien formado
        m = re.search(r"\{[\s\S]*\}$", text.strip())
        if not m:
            raise RuntimeError(
                "No se pudo parsear JSON de la respuesta del modelo. "
                "Respuesta cruda (truncada): " + text[:500]
            )
        data = json.loads(m.group(0))

    # 5) Asegurar dict
    if not isinstance(data, dict):
        raise RuntimeError("La respuesta del modelo no es un objeto JSON (dict).")

    return data


# ---------- Pipeline principal ----------
def procesar_archivo(path_in: str, ejemplos: List[Ejemplo], model_name: str = DEFAULT_MODEL) -> Tuple[str, str]:
    texto = leer_txt(path_in)
    texto = normalizar_ws(texto)

    # 1) Intentar con LangExtract (si está presente)
    data = intentar_con_langextract(ejemplos, texto, model_name=model_name)

    # 2) Fallback a Gemini directo
    if data is None:
        data = llamar_gemini_directo(ejemplos, texto, model_name=model_name)

    # Validación mínima de claves
    for k, _ in SECCIONES:
        data.setdefault(k, "")

    salida_txt = render_txt_salida(data)
    base = pathlib.Path(path_in).stem
    outdir = pathlib.Path("textos/salidas")
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"{base}_estructurado.txt"

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(salida_txt)

    return str(path_in), str(outpath)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Estructuración de sentencias con Gemini (+LangExtract opcional).")
    parser.add_argument("--modelo", default=DEFAULT_MODEL, help="Nombre del modelo Gemini (ej: gemini-2.0-flash).")
    parser.add_argument("--ejemplos_dir", default="textos/ejemplos", help="Carpeta con subcarpetas ejX/crudo.txt y ejX/estructurado.txt")
    parser.add_argument("--entrada_dir", default="textos/entrada", help="Carpeta con sentencias crudas a procesar")
    args = parser.parse_args()

    ejemplos = cargar_ejemplos(args.ejemplos_dir)
    if len(ejemplos) == 0:
        raise SystemExit("No se encontraron ejemplos en textos/ejemplos. Crea al menos uno (ej1/crudo.txt y ej1/estructurado.txt).")

    paths = sorted(glob.glob(os.path.join(args.entrada_dir, "*.txt")))
    if not paths:
        raise SystemExit("No hay archivos .txt en textos/entrada. Coloca ahí las sentencias crudas a procesar.")

    print(f"Usando modelo: {args.modelo}")
    print(f"Ejemplos cargados: {len(ejemplos)}")
    print(f"Archivos a procesar: {len(paths)}")

    for p in paths:
        pin, pout = procesar_archivo(p, ejemplos, model_name=args.modelo)
        print(f"✓ {pin} -> {pout}")

if __name__ == "__main__":
    main()
