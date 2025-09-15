import os
import re
import json
import textwrap
import unicodedata
from typing import List, Dict, Any
from langextract.resolver import ResolverParsingError
from collections import Counter

# ====== CONFIGURACIÓN DE LA API KEY ======
os.environ["LANGEXTRACT_API_KEY"] = "API_KEY_AQUI"  
import langextract as lx

# ====== RUTAS ======
INPUT_DIR = "textos"
OUTPUT_DIR = "documentos procesados 2"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ====== PATRONES GLOBALES ======
ORG_PAT = re.compile(
    r"\b("
    r"Unidad Judicial|Juzgado|Tribunal(?:\s+(?:Penal|Constitucional|Contencioso))?"
    r"|Corte(?:\s+(?:Nacional|Suprema))?"
    r"|Fiscal[ií]a|Ministerio|Consejo de la Judicatura|Defensor[ií]a P[úu]blica"
    r"|Instituto|Seguridad Social|Direcci[oó]n|Secretar[ií]a|Universidad|Procuradur[ií]a"
    r"|Contralor[ií]a|Asamblea|Gobernaci[oó]n|Municipio|Alcald[ií]a|Comit[eé]|Comisi[oó]n|Agencia|Oficina"
    r")\b",
    flags=re.IGNORECASE
)

# Artículos legales tolerantes a saltos/literal '/n'
ARTI_PAT = re.compile(
    r"\bArt(?:[íi]culo|\.)\s*(?:/n|\s)*(\d+)(?:\s*(?:del\s+COIP))?\b",
    flags=re.IGNORECASE
)


# ====== PROMPT (frases/spans) ======
PROMPT = textwrap.dedent("""
TAREA
Extrae FRASES relevantes (spans) del texto jurídico y clasifícalas en:
PERSONA, ORG, ARTICULO_LEGAL, FECHA, OBRA_JURIDICA, DELITO, NUMERO_CAUSA, LUGAR u OTRO.

SALIDA (obligatoria)
Devuelve SOLO un JSON válido que sea un ARREGLO de objetos, sin texto extra:
[
  {"text":"...", "type":"...", "start":0, "end":10, "normalized":"..."},
  ...
]
- "start" y "end" son índices 0-based sobre el TEXTO ORIGINAL (start inclusivo, end exclusivo).
- "text" es el span EXACTO del texto original (sin modificar).
- "normalized": minúsculas, sin tildes, espacios colapsados a uno, sin saltos de línea.

PROCESO (seguir este orden):
1) Preprocesa SOLO para buscar spans: trata '\n' y '/n' como espacio; colapsa espacios.
2) Detecta candidatos y clasifícalos aplicando la SIGUIENTE PRECEDENCIA (si entra en una clase de mayor prioridad, NO la reetiquetes como otra):
   OBRA_JURIDICA > ARTICULO_LEGAL > ORG > FECHA > NUMERO_CAUSA > LUGAR > PERSONA > DELITO > OTRO
3) La decisión de clase se hace usando el texto ORIGINAL; la normalización se usa SOLO para "normalized".
4) No devuelvas spans superpuestos salvo que el de mayor precedencia contenga al de menor (elige el de mayor precedencia).
5) Deduplica por (normalized, type). No repitas entidades idénticas.

CRITERIOS POR CLASE

OBRA_JURIDICA (máxima precedencia)
- Nombres de códigos, leyes, reglamentos, constituciones, estatutos, manuales, guías, tratados, doctrinas, índices, procedimientos.
- Señales iniciales: ^(Código|Ley|Reglamento|Constitución|Estatuto|Manual|Guía|Tratado|Doctrina|Índice|Procedimiento)\b
- También títulos compuestos que terminen en “... Penal|... Civil|... Administrativo|... Laboral|... Tributario”.
- Ej.: "Código Orgánico Integral Penal", "Procedimiento Abreviado", "Índice Analítico".
- Nunca clasificar como PERSONA.

ARTICULO_LEGAL
- “Art.” o “Artículo” seguido de número (tolerar espacios/saltos de línea y sufijos: bis, ter, letras).
- Normaliza a “art. N” (ej.: "Art. 232" → normalized: "art. 232").
- Acepta rangos “Art. 232-234” como un único span.

ORG
- Instituciones, órganos, dependencias, empresas o entidades colectivas.
- Señales como PALABRAS COMPLETAS (no subcadenas): \b(Unidad|Juzgado|Tribunal|Corte|Ministerio|Consejo|Fiscalía|Defensoría|Instituto|Universidad|Sala|Dirección|Secretaría|Procuraduría|Policía|Municipio|Asamblea|Cámara)\b
- Sufijos corporativos: \b(S\.A\.|C\.A\.|E\.P\.|Cía\.|Ltda\.)\b
- Nombres compuestos con “de|del|de la|de los” que denoten entidad: “Ministerio de…”, “Consejo de…”.
- Estructuras “cargo + de + institución” (p.ej., “Director de la Fiscalía…”) → ORG.
- No hagas match por subcadena (ej.: “Cortez” ≠ “Corte”, “Salas” ≠ “Sala”).

FECHA
- Detecta fechas en formatos comunes (DD/MM/AAAA, DD-MM-AAAA, “1 de enero de 2020”, “enero 2020”, etc.).
- Normaliza a YYYY-MM-DD si posible; si no, a “YYYY-MM” o “YYYY”.

NUMERO_CAUSA
- Patrones: \b(No\.|N°|Nº)\s*\d+([-/]\d+)?\b, o “Causa/Caso” + número similar.
- Incluir guión/año si aparece: “N° 12345-2022”.

LUGAR
- Ciudades, provincias, países y divisiones geográficas.
- Si el nombre del lugar aparece dentro de una ORG (p.ej., “Corte Provincial de Pichincha”), etiqueta SOLO la ORG (no extraigas el lugar por separado).

PERSONA (penúltima precedencia)
- Nombres de personas reales con forma “Nombre(s) Apellido(s)” (2–4 tokens Capitalizados).
- Requiere al menos una SEÑAL POSITIVA: que uno de los tokens sea un nombre de pila común (ej.: ana, andrea, camila, carlos, diana, diego, jorge, jose, juan, luis, maria, pablo, paola, sofia, leonardo, pedro; sin tildes para comparar).
- EXCLUSIONES ESTRICTAS (si se cumple, NO es PERSONA):
  a) Si el span empieza por: Código, Ley, Reglamento, Constitución, Estatuto, Manual, Guía, Tratado, Doctrina, Índice, Procedimiento, Convenio, Sentencia, Recurso, Casación, Apelación, Audiencia, Incidente.
  b) Si contiene un SUSTANTIVO INSTITUCIONAL (los de ORG) sin un nombre claro.
  c) Spans en MAYÚSCULAS SOSTENIDAS (FULL CAPS).
- Reglas con cargos:
  - “cargo + nombre” (p.ej., “Juez María López”) → PERSONA.
  - “cargo + de + institución” sin nombre (p.ej., “Director de la Fiscalía…”) → ORG.
  - Solo el cargo (“El Juez”, “La Fiscal”) → OTRO.

DELITO
- Tipos delictivos o descripciones de conducta típicas: homicidio, asesinato, robo, hurto, estafa, peculado, cohecho, concusión, violación, abuso sexual, violencia intrafamiliar, tráfico de drogas, lavado de activos, lesiones, tentativa, etc.
- Acepta variaciones con adjetivos o complementos (“robo agravado”, “tráfico ilícito de sustancias…”).

OTRO
- Todo lo que no encaje en las categorías anteriores.

REGLAS DE CALIDAD (obligatorias)
1) Precedencia fija: OBRA_JURIDICA > ARTICULO_LEGAL > ORG > FECHA > NUMERO_CAUSA > LUGAR > PERSONA > DELITO > OTRO.
2) ORG usa límites de palabra (\b ... \b). Nunca clasificar por subcadenas.
3) No etiquetar PERSONA si el span es FULL CAPS o si coincide con disparadores de OBRA_JURIDICA/ORG.
4) Roles/cargos: “cargo + nombre” → PERSONA; “cargo + de + institución” → ORG; “solo cargo” → OTRO.
5) “text” = original; “normalized” = minúsculas, sin tildes, espacios simples.
6) No repitas entidades idénticas (deduplicar por normalized+type).
7) Devuelve SOLO el JSON (sin comentarios ni explicaciones).
""")

# ====== EJEMPLOS FEW-SHOT ======
ExampleData = lx.data.ExampleData
Extraction = lx.data.Extraction

def _clean_ws(s: str) -> str:
    # Unifica saltos de línea y la secuencia literal "/n" a un espacio
    s = s.replace("\r\n", " ").replace("\n", " ").replace("/n", " ")
    # Colapsa espacios múltiples
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _norm(s: str) -> str:
    s = _clean_ws(s).lower()
    # Quita tildes
    s = ''.join(c for c in unicodedata.normalize('NFD', s)
                if unicodedata.category(c) != 'Mn')
    return s
EXAMPLES = [
    # Fórmula judicial larga (como la que indicaste)
    ExampleData(
        text="ADMINISTRANDO JUSTICIA EN NOMBRE DEL PUEBLO SOBERANO DEL ECUADOR Y POR AUTORIDAD DE LA CONSTITUCIÓN Y LAS LEYES DE LA REPÚBLICA",
        extractions=[
            Extraction(
                extraction_class="FORMULA_JUDICIAL",
                extraction_text="ADMINISTRANDO JUSTICIA EN NOMBRE DEL PUEBLO SOBERANO DEL ECUADOR Y POR AUTORIDAD DE LA CONSTITUCIÓN Y LAS LEYES DE LA REPÚBLICA",
                attributes={"normalized": _norm("ADMINISTRANDO JUSTICIA EN NOMBRE DEL PUEBLO SOBERANO DEL ECUADOR Y POR AUTORIDAD DE LA CONSTITUCIÓN Y LAS LEYES DE LA REPÚBLICA"),
                            "start": 0, "end": 140}
            )
        ]
    ),
    # Fecha y artículo legal en contexto
    ExampleData(
        text="Lo resuelto el 12 de marzo de 2021 según el Artículo 123 del COIP.",
        extractions=[
            Extraction(extraction_class="FECHA", extraction_text="12 de marzo de 2021",
                       attributes={"normalized": "2021-03-12", "start": 13, "end": 32}),
            Extraction(extraction_class="ARTICULO_LEGAL", extraction_text="Artículo 123 del COIP",
                       attributes={"normalized": _norm("Artículo 123 del COIP"), "start": 43, "end": 63}),
        ]
    ),
    # Persona y delito
    ExampleData(
        text="El acusado Juan Pérez es culpable de homicidio agravado.",
        extractions=[
            Extraction(extraction_class="PERSONA", extraction_text="Juan Pérez",
                       attributes={"normalized": _norm("Juan Pérez"), "start": 11, "end": 21}),
            Extraction(extraction_class="DELITO", extraction_text="homicidio agravado",
                       attributes={"normalized": _norm("homicidio agravado"), "start": 37, "end": 55}),
        ]
    ),

     # PERSONAS reales
    ExampleData(
        text="Simón Valdivieso Vintimilla",
        extractions=[
            Extraction(extraction_class="PERSONA",
                       extraction_text="Simón Valdivieso Vintimilla",
                       attributes={"normalized": _norm("Simón Valdivieso Vintimilla"), "start": 0, "end": 27})
        ]
    ),
    ExampleData(
        text="Maribel Cortez Estrella",
        extractions=[
            Extraction(extraction_class="PERSONA",
                       extraction_text="Maribel Cortez Estrella",
                       attributes={"normalized": _norm("Maribel Cortez Estrella"), "start": 0, "end": 25})
        ]
    ),
    ExampleData(
        text="Edison German Vergara Brito",
        extractions=[
            Extraction(extraction_class="PERSONA",
                       extraction_text="Edison German Vergara Brito",
                       attributes={"normalized": _norm("Edison German Vergara Brito"), "start": 0, "end": 29})
        ]
    ),

    # INSTITUCIONES → ORG
    ExampleData(
        text="Instituto Ecuatoriano Seguridad Social",
        extractions=[
            Extraction(extraction_class="ORG",
                       extraction_text="Instituto Ecuatoriano Seguridad Social",
                       attributes={"normalized": _norm("Instituto Ecuatoriano Seguridad Social"), "start": 0, "end": 39})
        ]
    ),
    ExampleData(
        text="Unidad Judicial Penal Norte de Guayaquil",
        extractions=[
            Extraction(extraction_class="ORG",
                       extraction_text="Unidad Judicial Penal Norte de Guayaquil",
                       attributes={"normalized": _norm("Unidad Judicial Penal Norte de Guayaquil"), "start": 0, "end": 42})
        ]
    ),
    ExampleData(
        text="Función Judicial",
        extractions=[
            Extraction(extraction_class="ORG",
                       extraction_text="Función Judicial",
                       attributes={"normalized": _norm("Función Judicial"), "start": 0, "end": 16})
        ]
    ),
    ExampleData(
        text="Distrito Metropolitano",
        extractions=[
            Extraction(extraction_class="ORG",
                       extraction_text="Distrito Metropolitano",
                       attributes={"normalized": _norm("Distrito Metropolitano"), "start": 0, "end": 24})
        ]
    ),

    # OBRAS JURÍDICAS → OTRO
    ExampleData(
        text="Código Orgánico Integral Penal",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="Código Orgánico Integral Penal",
                       attributes={"normalized": _norm("Código Orgánico Integral Penal"), "start": 0, "end": 33})
        ]
    ),
    ExampleData(
        text="Código Orgánico",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="Código Orgánico",
                       attributes={"normalized": _norm("Código Orgánico"), "start": 0, "end": 18})
        ]
    ),
    ExampleData(
        text="Procedimiento Abreviado",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="Procedimiento Abreviado",
                       attributes={"normalized": _norm("Procedimiento Abreviado"), "start": 0, "end": 28})
        ]
    ),
    ExampleData(
        text="Índice Analítico",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="Índice Analítico",
                       attributes={"normalized": _norm("Índice Analítico"), "start": 0, "end": 22})
        ]
    ),
    ExampleData(
        text="El Jurista",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="El Jurista",
                       attributes={"normalized": _norm("El Jurista"), "start": 0, "end": 10})
        ]
    ),
    ExampleData(
        text="El Juzgador",
        extractions=[
            Extraction(extraction_class="OBRA_JURIDICA",
                       extraction_text="El Juzgador",
                       attributes={"normalized": _norm("El Juzgador"), "start": 0, "end": 12})
        ]
    ),

    # FECHAS
    ExampleData(
        text="Lo resuelto el 12 de marzo de 2021",
        extractions=[
            Extraction(extraction_class="FECHA",
                       extraction_text="12 de marzo de 2021",
                       attributes={"normalized": "2021-03-12", "start": 13, "end": 32})
        ]
    ),

    # ARTÍCULOS LEGALES
    ExampleData(
        text="Art. \n232 del COIP",
        extractions=[
            Extraction(extraction_class="ARTICULO_LEGAL",
                       extraction_text="Art. 232 del COIP",
                       attributes={"normalized": _norm("Art. 232 del COIP"), "start": 0, "end": 16})
        ]
    ),
]

EXAMPLES.extend([
    ExampleData(
        text="Unidad Judicial Penal Norte 1 de Guayaquil",
        extractions=[
            Extraction(
                extraction_class="ORG",
                extraction_text="Unidad Judicial Penal Norte 1 de Guayaquil",
                attributes={"normalized": _norm("Unidad Judicial Penal Norte 1 de Guayaquil"),
                            "start": 0, "end": 43}
            )
        ]
    ),
    ExampleData(
        text="Art. \n232 del COIP",
        extractions=[
            Extraction(
                extraction_class="ARTICULO_LEGAL",
                extraction_text="Art. 232 del COIP",
                attributes={"normalized": _norm("Art. 232 del COIP"),
                            "start": 0, "end": 16}
            )
        ]
    )
])
# ====== GAZETTEERS / PISTAS =====
# Palabras que indican que NO es PERSONA (instituciones, obras legales, etc)

ORG_HINTS = [
    "instituto", "ministerio", "consejo", "defensoría", "defensoria",
    "fiscalía", "fiscalia", "corte", "tribunal", "juzgado", "función judicial",
    "unidad judicial", "distrito metropolitano", "seguridad social",
    "estado", "estados parte", "parte informativo", "sub director", "subdirectora",
    "sub director nacional", "coip", "iess", "poder judicial"
]

LEGAL_WORKS_HINTS = [
    "código orgánico", "código orgánico integral penal", "procedimiento penal",
    "procedimiento abreviado", "índice analítico", "el juzgador", "el jurista"
]

# Si el span coincide con estos patrones, casi seguro NO es persona
NOT_PERSON_EXACT = {
    "instituto ecuatoriano",
    "instituto ecuatoriano seguridad social",
    "función judicial",
    "codigo organico integral penal",
    "codigo organico",
    "distrito metropolitano",
    "infracciones flagrantes",
    "estados parte", "los estados parte", "los estados",
    "parte informativo",
}

# Palabras funcionales que invalidan PERSONA si aparecen solas o encabezando
BAD_PERSON_LEADS = {"el", "la", "los", "las", "en", "de", "del", "y"}

def _is_likely_org_or_legal(text_norm: str) -> bool:
    if text_norm in NOT_PERSON_EXACT:
        return True
    if any(h in text_norm for h in ORG_HINTS):
        return True
    if any(h in text_norm for h in LEGAL_WORKS_HINTS):
        return True
    return False

def _looks_like_real_name(text: str) -> bool:
    # Heurística: 2–4 palabras tipo Nombre Apellido (no todo mayúsculas),
    # sin empezar por artículos/preps y sin pistas de ORG/obra
    parts = text.strip().split()
    if len(parts) < 2 or len(parts) > 4:
        return False
    if text.upper() == text and len(text) > 3:
        return False
    if parts[0].lower() in BAD_PERSON_LEADS:
        return False
    if _is_likely_org_or_legal(_norm(text)):
        return False
    # Requiere al menos dos “palabras capitalizadas” con minúsculas
    cap_like = sum(1 for p in parts if re.match(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+$", p))
    return cap_like >= 2

def _repair_person_mislabels(sp):
    text = _clean_ws(sp.get("text",""))
    norm = _norm(text)
    ttype = sp.get("type","OTRO")
    if ttype == "PERSONA":
        if _is_likely_org_or_legal(norm) or not _looks_like_real_name(text):
            # Si contiene pistas de institución/obra → ORG; si no, OTRO
            sp["type"] = "ORG" if _is_likely_org_or_legal(norm) else "OTRO"
    sp["text"] = text
    sp["normalized"] = norm
    return sp

INSTITUTION_HEADS = {
    "unidad", "juzgado", "tribunal", "corte", "ministerio", "consejo",
    "fiscalia", "defensoria", "instituto", "universidad", "sala", "direccion",
    "secretaria", "procuraduria", "contraloria", "asamblea", "gobernacion",
    "municipio", "alcaldia", "comite", "comision", "agencia", "oficina"
}

CORP_SUFFIXES = {"s.a.", "c.a.", "e.p.", "cia.", "cía.", "ltda.", "s.r.l.", "s.a.s.", "cía."}

ROLE_TITLES = {  # si aparece, rara vez es un nombre de persona aislado
    "subdirector", "sub directora", "sub-director", "director", "directora",
    "presidente", "presidenta", "secretario", "secretaria", "procurador", "procuradora",
    "fiscal", "juez", "jueza", "magistrado", "magistrada", "ministro", "ministra"
}

def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')

def _clean_ws(s: str) -> str:
    s = s.replace("\r\n", " ").replace("\n", " ").replace("/n", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _norm(s: str) -> str:
    s = _clean_ws(s).lower()
    s = _strip_accents(s)
    return s

UPPER_RE = re.compile(r"^[^a-záéíóúñ]*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\.\-]*$")  # casi todo mayúsculas

def looks_like_org(text_norm: str) -> bool:
    if any(_contains_word(text_norm, h) for h in INSTITUTION_HEADS):
        return True
    if any(text_norm.endswith(suf) or f" {suf} " in text_norm for suf in CORP_SUFFIXES):
        return True
    if re.search(r"\b(de|del|de la|de los|de las)\b", text_norm) and any(_contains_word(text_norm, h) for h in INSTITUTION_HEADS):
        return True
    if any(t in text_norm for t in ROLE_TITLES) and any(_contains_word(text_norm, h) for h in INSTITUTION_HEADS):
        return True
    if UPPER_RE.match(_clean_ws(sp:=text_norm).replace(".","")) and len(sp) > 4:
        return True
    return False


NAME_TOKEN = re.compile(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+$")

def looks_like_person(text: str) -> bool:
    tokens = text.strip().split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    if text.upper() == text and len(text) > 3:
        return False
    if tokens and _norm(tokens[0]) in {"el","la","los","las","en","de","del"}:
        return False
    # si contiene cabezas institucionales, no es persona
    if any(_contains_word(_norm(text), h) for h in INSTITUTION_HEADS):
        return False
    cap_like = sum(1 for t in tokens if NAME_TOKEN.match(t))
    has_given = any(_norm(t) in FIRST_NAMES for t in tokens)
    return cap_like >= 2 and has_given


def is_legal_work(norm: str) -> bool:
    # Si contiene vocabulario típico de códigos/obras → no es PERSONA
    return any(tok in norm for tok in LEGAL_WORK_TOKENS)

def repair_person_vs_org(span):
    text = _clean_ws(span.get("text", ""))
    norm = _norm(text)
    t = span.get("type", "OTRO")
    if t == "PERSONA":
        if looks_like_org(norm) or not looks_like_person(text):
            span["type"] = "ORG" if looks_like_org(norm) else "OTRO"
    span["text"] = text
    span["normalized"] = norm
    return span

def _refine_type(span: dict) -> dict:
    text = _clean_ws(span.get("text",""))
    norm = _norm(text)
    t    = span.get("type","OTRO")

    # 0) Reglas duras: ART/FECHA/CAUSA/LUGAR
    if RE_ART.search(text):
        t = "ARTICULO_LEGAL"
    elif RE_DATE_LONG.search(text) or RE_DATE_NUM.search(text):
        t = "FECHA"
    elif RE_CAUSA_HDR.search(text):
        t = "NUMERO_CAUSA" if RE_NO_TOKEN.search(text) and re.search(r"\d", text) else "OTRO"
    elif RE_LUGAR_HEUR.search(text):
        t = "LUGAR"

    # 0.1) Si parece OBRA_JURIDICA, dalo por hecho
    if re.match(r"^(codigo|ley|reglamento|constitucion|estatuto|manual|guia|tratado|doctrina|indice|procedimiento)\b", norm) or is_legal_work(norm):
        t = "OBRA_JURIDICA"

    # 1) Si vino como PERSONA pero luce a 'concepto/obra', corrige
    if t == "PERSONA" and is_concept_phrase(norm):
        t = "OBRA_JURIDICA" if is_legal_work(norm) else "OTRO"

    # 2) Roles sin nombre -> no PERSONA
    if t == "PERSONA" and contains_role_without_name(text, norm):
        t = "ORG" if looks_like_org(norm) else "OTRO"

    # 3) Si parece organización -> ORG
    if t == "PERSONA" and looks_like_org(norm):
        t = "ORG"

    # 4) Check final: ¿realmente parece nombre?
    if t == "PERSONA" and not looks_like_person(text):
        t = "ORG" if looks_like_org(norm) else ("OBRA_JURIDICA" if is_legal_work(norm) else "OTRO")

    # 5) Clases no previstas (p.ej. FORMULA_JUDICIAL de los examples) -> OTRO
    allowed = {"PERSONA","ORG","ARTICULO_LEGAL","FECHA","OBRA_JURIDICA","DELITO","NUMERO_CAUSA","LUGAR","OTRO"}
    if t not in allowed:
        t = "OTRO"

    span["text"] = text
    span["normalized"] = norm
    span["type"] = t
    return span

def _postprocess_spans(spans: list[dict]) -> list[dict]:
    out, seen = [], set()
    for sp in spans:
        sp = _refine_type(sp)
        # Asegura limpieza extra en artículos (p. ej., "Art.\n232" -> "Art. 232")
        if sp["type"] == "ARTICULO_LEGAL":
            sp["text"] = _clean_ws(sp["text"])
            sp["normalized"] = _norm(sp["text"])

        key = (sp["type"], sp["normalized"])
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out

# ====== UTILIDADES DE PARSEO ROBUSTO ======
JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")

def _extract_first_json_array(text: str):
    m = JSON_ARRAY_RE.search(text or "")
    if not m:
        raise ValueError("No se encontró un array JSON en la salida del modelo.")
    return m.group(0)

# ====== HELPERS Y PATRONES ======
# --- Limpieza / normalización consistentes ---
def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')

def _clean_ws(s: str) -> str:
    s = s.replace("\r\n", " ").replace("\n", " ").replace("/n", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _norm(s: str) -> str:
    s = _clean_ws(s).lower()
    return _strip_accents(s)

# --- Patrones muy frecuentes en judicial ---
RE_DATE_LONG   = re.compile(r"\b\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚáéíóúñ]+\s+de\s+\d{4}\b", re.I)
RE_DATE_NUM    = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
RE_ART         = re.compile(r"\bArt(?:[íi]culo|\.)\s*(?:/n|\s)*\d+\b", re.I)
RE_CAUSA_HDR   = re.compile(r"\b(Juicio|Causa\s+Penal|Resoluci[oó]n|Registro\s+Oficial(?:\s+Suplemento)?)\b", re.I)
RE_NO_TOKEN    = re.compile(r"\b(No\.?|N[º°])\b")
RE_LUGAR_HEUR  = re.compile(r"^(Parque\s+[A-ZÁÉÍÓÚÑ]|San\s+[A-ZÁÉÍÓÚÑ])")

# === Conceptos/etiquetas jurídicas (genérico) ===
LEGAL_CONCEPT_TOKENS = {
    # conceptos típicos
    "infracciones","flagrantes","audiencia","publica","estado","constitucional",
    "parte","informativo","garantias","penales","medida","cautelar","sentencia",
    "resolucion","auto","certifico","convenio","competencia","acusacion",
    "acusado","procesado","imputado","acusadora","defensa","victima","reparacion",
    "apelacion","absolucion","inocencia","culpabilidad","flagrancia","incautacion",
    "allanamiento","indagacion","instruccion","juicio","tribunal","corte","juzgado"
}

def is_concept_phrase(norm: str) -> bool:
    toks = [t for t in norm.split() if t not in {"de","del","la","las","los","y","en","el","al"}]
    if not toks:
        return False

    known = sum(1 for t in toks if t in ALL_LEGAL_TOKENS)
    if known >= max(1, len(toks) - 1):
        return True

    # Cabeceras típicas de obras jurídicas => no persona
    if re.match(r"^(codigo|ley|reglamento|constitucion|estatuto|manual|guia|tratado|doctrina|indice|procedimiento)\b", norm):
        return True

    # Morfología nominal/adjetival frecuente en conceptos
    if all(t.endswith(("cion","ciones","dad","ales","aria","arias","ario","arios")) for t in toks):
        return True

    return False


def contains_role_without_name(text: str, norm: str) -> bool:
    """
    True si hay un 'rol/cargo' pero NO hay una forma clara de nombre (Nombre Apellido...).
    Ej.: 'Sub Director Nacional', 'Fiscal Ab', 'Ayudante Judicial'.
    """
    # usa tu ROLE_TITLES ya definido
    has_role = any(rt in norm for rt in ROLE_TITLES)
    return has_role and not looks_like_person(text)


# Cabezas institucionales y rasgos de ORG (genéricas)
INSTITUTION_HEADS = {
    "unidad","juzgado","tribunal","corte","ministerio","consejo","fiscalia","defensoria",
    "instituto","universidad","sala","direccion","secretaria","procuraduria","contraloria",
    "asamblea","gobernacion","municipio","alcaldia","comite","comision","agencia","oficina",
    "funcion judicial","distrito metropolitano","seguridad social","judicatura"
}
CORP_SUFFIXES = {"s.a.","c.a.","e.p.","cia.","cía.","ltda.","s.r.l.","s.a.s."}
ROLE_TITLES   = {"fiscal","juez","jueza","magistrado","magistrada","director","directora","subdirector",
                 "sub directora","sub-director","secretario","secretaria","procurador","procuradora","ministro","ministra","ab","dr","dra"}
UPPER_RE      = re.compile(r"^[^a-záéíóúñ]*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\.\-]*$")  # casi todo caps
NAME_TOKEN    = re.compile(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+$")

# Obras / doctrinas / códigos (genérico, no “lista cerrada”)
LEGAL_WORK_TOKENS = {
    "codigo","organico","integral","penal","procedimiento","indice","analitico",
    "jurista","juzgador","doctrina","manual","tratado","resolucion","registro","oficial","suplemento","garantias"
}

# === Helpers de coincidencia por palabra completa ===
def _contains_word(haystack: str, word: str) -> bool:
    return re.search(r'\b' + re.escape(word) + r'\b', haystack) is not None

# === Lista mínima de nombres de pila comunes (puedes ampliarla) ===
FIRST_NAMES = {
    "ana","andrea","camila","carla","carlos","daniel","diana","diego","eduardo","elena",
    "fernando","francisco","gabriela","javier","jorge","jose","juan","karla","laura",
    "luis","manuel","maria","mariana","martin","miguel","natalia","paola","pablo",
    "pedro","ricardo","roberto","rodrigo","sofia","susana","valeria","victor","leonardo"
}

# Une conceptos legales generales y obras jurídicas
ALL_LEGAL_TOKENS = set(LEGAL_CONCEPT_TOKENS) | set(LEGAL_WORK_TOKENS)


# ====== FALLBACK LOCAL (regex/heurísticas) ======
MONTHS = {
    "enero": "01","febrero":"02","marzo":"03","abril":"04","mayo":"05","junio":"06",
    "julio":"07","agosto":"08","septiembre":"09","setiembre":"09","octubre":"10","noviembre":"11","diciembre":"12"
}

def _to_iso_date(d: str) -> str:
    # "12 de marzo de 2021" -> "2021-03-12"
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñ]+)\s+de\s+(\d{4})", d, flags=re.IGNORECASE)
    if m:
        day = int(m.group(1))
        mon = MONTHS.get(_norm(m.group(2)), None)
        year = m.group(3)
        if mon:
            return f"{year}-{mon}-{day:02d}"
    # "12/03/2021" o "12-03-2021"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", d)
    if m:
        dd, mm, yyyy = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{yyyy}-{mm:02d}-{dd:02d}"
    return _norm(d)

def _fallback_mine_phrases(text: str):
    text = _clean_ws(text)
    spans = []
    lower = text.lower()

    # Fórmula judicial (igual)
    if "administrando justicia en nombre del pueblo soberano del ecuador" in lower:
        i = lower.find("administrando justicia en nombre del pueblo soberano del ecuador")
        end_phrase = "las leyes de la república"
        j = lower.find(end_phrase, i)
        j = (j + len(end_phrase)) if j != -1 else min(len(text), i + 180)
        spans.append({"text": text[i:j], "type": "FORMULA_JUDICIAL",
                      "start": i, "end": j, "normalized": _norm(text[i:j])})

    # Fechas (igual que antes)
    for m in re.finditer(r"\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚáéíóúñ]+\s+de\s+\d{4}", text, flags=re.IGNORECASE):
        s,e=m.span(); frag=text[s:e]
        spans.append({"text": frag, "type":"FECHA","start":s,"end":e,"normalized": _to_iso_date(frag)})
    for m in re.finditer(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", text):
        s,e=m.span(); frag=text[s:e]
        spans.append({"text": frag, "type":"FECHA","start":s,"end":e,"normalized": _to_iso_date(frag)})

    # Artículos legales (permite '\n' o '/n' ya normalizado a espacio)
    for m in ARTI_PAT.finditer(text):
        s,e=m.span(); frag=text[s:e]
        spans.append({"text": frag, "type":"ARTICULO_LEGAL","start":s,"end":e,"normalized": _norm(frag)})

    # Nº de causa (igual)
    for m in re.finditer(r"\b(?:No\.?|Nº|N°)\s*\d{2,6}(?:-\d{2,4})?\b", text, flags=re.IGNORECASE):
        s,e=m.span(); frag=text[s:e]
        spans.append({"text": frag, "type":"NUMERO_CAUSA","start":s,"end":e,"normalized": _norm(frag)})

    # ORGs primero (para que PERSONA no se “robe” estas frases)
    for m in ORG_PAT.finditer(text):
        s,e=m.span(); frag=text[s:e]
        spans.append({"text": frag, "type":"ORG","start":s,"end":e,"normalized": _norm(frag)})

    # Personas (heurística endurecida)
    person_re = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})\b")
    for m in person_re.finditer(text):
        s, e = m.span()
        frag = text[s:e]
        norm_frag = _norm(frag)
        if ORG_PAT.search(frag):
            continue
        if frag.upper() == frag and len(frag) > 3:
            continue
        if is_legal_work(norm_frag) or is_concept_phrase(norm_frag):
            continue
        if not looks_like_person(frag):
            continue
        spans.append({"text": frag, "type": "PERSONA", "start": s, "end": e, "normalized": norm_frag})

    return spans


# ====== EXTRACCIÓN PRINCIPAL (LLM + fallback robusto) ======
def extract_spans(text: str) -> list[dict]:
    kwargs = {
        "text_or_documents": text,
        "prompt_description": PROMPT,
        "examples": EXAMPLES,
        "fence_output": True,
        "use_schema_constraints": False,
        "temperature": 0.0,
        #"model_id": "gpt-5",
        "model_id": "gemini-2.5-flash",
    }

    try:
        result = lx.extract(**kwargs)

        # 1) Salida estructurada directa del provider
        if getattr(result, "structured_output", None):
            spans = result.structured_output
            return _postprocess_spans(spans)

        # 2) Mapeo desde result.extractions (si el provider lo usa)
        if getattr(result, "extractions", None):
            spans = []
            for e in result.extractions:
                attrs = getattr(e, "attributes", {}) or {}
                spans.append({
                    "text": getattr(e, "extraction_text", ""),
                    "type": (getattr(e, "extraction_class", "OTRO") or "OTRO"),
                    "start": attrs.get("start"),
                    "end": attrs.get("end"),
                    "normalized": attrs.get("normalized"),  # se normaliza en _postprocess_spans
                })
            if spans:
                return _postprocess_spans(spans)

        # 3) Parseo manual del JSON crudo (con fences o adornos)
        raw = getattr(result, "raw_text", None) or getattr(result, "model_response", None)
        if isinstance(raw, str) and raw.strip():
            try:
                json_str = _extract_first_json_array(raw)
                spans = json.loads(json_str)
                return _postprocess_spans(spans)
            except Exception:
                pass

        # 4) Fallback local (regex/heurísticas)
        spans = _fallback_mine_phrases(text)
        return _postprocess_spans(spans)

    except ResolverParsingError:
        # El parser interno falló → intentar extraer el primer array JSON de la respuesta cruda
        try:
            raw = locals().get("result", None)
            raw_text = ""
            if raw is not None:
                raw_text = getattr(raw, "raw_text", "") or getattr(raw, "model_response", "")
            if raw_text:
                json_str = _extract_first_json_array(raw_text)
                spans = json.loads(json_str)
                return _postprocess_spans(spans)
        except Exception:
            pass
        spans = _fallback_mine_phrases(text)
        return _postprocess_spans(spans)

    except Exception:
        # Cualquier otro error: no detener el lote
        spans = _fallback_mine_phrases(text)
        return _postprocess_spans(spans)


# ====== PROCESAR ARCHIVOS ======
def process_file(input_path: str, output_base: str):
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    spans = extract_spans(text)

    # ── TIP DE VERIFICACIÓN RÁPIDA ─────────────────────────────────
    counts = Counter(sp["type"] for sp in spans)
    print(f"Tipos en {os.path.basename(input_path)} → {dict(counts)}")
    # (Opcional) Muestra algunos ejemplos de PERSONA para auditar falsos positivos:
    if counts.get("PERSONA"):
        ejemplos_persona = [sp["text"] for sp in spans if sp["type"] == "PERSONA"][:10]
        print("Ejemplos PERSONA:", ejemplos_persona)
    # ───────────────────────────────────────────────────────────────

    # Guarda JSON (lista de frases con tipo y offsets)
    out_json = f"{output_base}_frases.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(spans, f, indent=2, ensure_ascii=False)

    # Guarda TXT resumido por tipo
    out_txt = f"{output_base}_frases.txt"
    by_type = {}
    for sp in spans:
        by_type.setdefault(sp["type"], []).append(sp["text"])
    with open(out_txt, "w", encoding="utf-8") as f:
        for t, items in by_type.items():
            f.write(f"[{t}]\n")
            for it in items[:50]:
                f.write(f"- {it}\n")
            f.write("\n")

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
