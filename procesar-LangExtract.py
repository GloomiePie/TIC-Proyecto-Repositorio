import os
import re
import json
from langextract.resolver import ResolverParsingError
import langextract as lx

os.environ["LANGEXTRACT_API_KEY"] = "AIzaSyCpkeB9wMIuTmx0h5E0cI_Vr9hMOeDWcaw"
USE_OLLAMA = False

# Carpetas
INPUT_DIR = "textos"
OUTPUT_DIR = "documentos procesados 2"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Prompt: pedimos tokenización y normalización con posición
PROMPT = """
Divide el texto en tokens y responde ÚNICAMENTE con un JSON VÁLIDO:
[
  {"token":"...", "normalized":"...", "position":0},
  ...
]

Reglas:
- Sin texto adicional fuera del JSON.
- Sin comentarios.
- Sin claves extra.
- "normalized": en minúsculas y sin tildes.
- "position": índice de inicio del token en el texto (entero).
"""


# Un ejemplo few-shot para anclar el formato de salida
ExampleData = lx.data.ExampleData
Extraction = lx.data.Extraction

EXAMPLES = [
    ExampleData(
        text="La Corte Suprema dictó sentencia.",
        extractions=[
            Extraction(extraction_class="Token", extraction_text="La",
                       attributes={"normalized": "la", "position": 0}),
            Extraction(extraction_class="Token", extraction_text="Corte",
                       attributes={"normalized": "corte", "position": 3}),
            Extraction(extraction_class="Token", extraction_text="Suprema",
                       attributes={"normalized": "suprema", "position": 9}),
            Extraction(extraction_class="Token", extraction_text="dictó",
                       attributes={"normalized": "dicto", "position": 17}),
            Extraction(extraction_class="Token", extraction_text="sentencia",
                       attributes={"normalized": "sentencia", "position": 23}),
            Extraction(extraction_class="Token", extraction_text=".",
                       attributes={"normalized": ".", "position": 32}),
        ],
    )
]

JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")  # captura el primer array JSON

def _extract_first_json_array(text: str):
    m = JSON_ARRAY_RE.search(text)
    if not m:
        raise ValueError("No se encontró un array JSON en la salida del modelo.")
    return m.group(0)

def _fallback_tokenize(text: str):
    # Tokenizador de emergencia si el LLM no devuelve JSON parseable
    # Separa por espacios/puntuación; normaliza a minúsculas y sin tildes simples
    import unicodedata
    def normalize(s):
        s = s.lower()
        s = ''.join(c for c in unicodedata.normalize('NFD', s)
                    if unicodedata.category(c) != 'Mn')
        return s

    tokens = []
    for m in re.finditer(r"\S+", text):
        tok = m.group(0)
        pos = m.start()
        tokens.append({"token": tok, "normalized": normalize(tok), "position": pos})
    return tokens

def extract_tokens(text: str):
    kwargs = {
        "text_or_documents": text,
        "prompt_description": PROMPT,
        "examples": EXAMPLES,
        "fence_output": True,
        "use_schema_constraints": False,
        "temperature": 0.0,
        "model_id": "gemini-2.5-flash",
    }

    try:
        result = lx.extract(**kwargs)

        # 1) Si hay salida estructurada, úsala
        if getattr(result, "structured_output", None):
            return result.structured_output

        # 2) Intenta mapear desde extractions (algunos providers)
        if getattr(result, "extractions", None):
            out = []
            for e in result.extractions:
                if getattr(e, "extraction_class", "") not in (None, "", "Token"):
                    # Si definiste la clase "Token" en los ejemplos, filtra por ella.
                    pass
                attrs = (getattr(e, "attributes", {}) or {})
                out.append({
                    "token": getattr(e, "extraction_text", ""),
                    "normalized": attrs.get("normalized") or getattr(e, "extraction_text", "").lower(),
                    "position": attrs.get("position"),
                })
            if out:
                return out

        # 3) Como último intento, parsea la salida en crudo si está disponible
        raw = getattr(result, "raw_text", None) or getattr(result, "model_response", None)
        if isinstance(raw, str) and raw.strip():
            json_str = _extract_first_json_array(raw)
            return json.loads(json_str)

        # Si no hay nada parseable, cae al fallback local
        return _fallback_tokenize(text)

    except ResolverParsingError:
        # El parser de LangExtract no pudo; limpiamos manualmente
        try:
            # Cuando hay fences, a veces result.raw_text no está; intenta rescatar de model_response
            raw = locals().get("result", None)
            raw_text = ""
            if raw is not None:
                raw_text = getattr(raw, "raw_text", "") or getattr(raw, "model_response", "")
            # Si no tenemos raw_text, no pasa nada: caemos a fallback
            if raw_text:
                json_str = _extract_first_json_array(raw_text)
                return json.loads(json_str)
        except Exception:
            pass
        # Último recurso: tokenizador local
        return _fallback_tokenize(text)
    except Exception:
        # Cualquier otro error inesperado → no te detiene el lote
        return _fallback_tokenize(text)

def process_file(input_path: str, output_base: str):
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    tokens = extract_tokens(text)

    # Guardar JSON
    out_json = f"{output_base}_procesado.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2, ensure_ascii=False)

    # Guardar TXT
    out_txt = f"{output_base}_procesado.txt"
    toks = [t.get("token", "") for t in tokens]
    norms = [t.get("normalized", "") for t in tokens]
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("Tokens:\n")
        f.write(" ".join(toks) + "\n\n")
        f.write("Normalizados:\n")
        f.write(" ".join(norms) + "\n")

    print(f"✅ Procesado: {os.path.basename(input_path)} → {out_txt}, {out_json}")

def main():
    if not os.path.isdir(INPUT_DIR):
        raise RuntimeError(f"No existe la carpeta de entrada: {INPUT_DIR}")

    for name in os.listdir(INPUT_DIR):
        if not name.lower().endswith(".txt"):
            continue
        in_path = os.path.join(INPUT_DIR, name)
        base_name = os.path.splitext(name)[0]
        out_base = os.path.join(OUTPUT_DIR, base_name)
        process_file(in_path, out_base)

if __name__ == "__main__":
    main()