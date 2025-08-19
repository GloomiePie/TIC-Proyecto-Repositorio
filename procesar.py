import spacy
import os
import json

# Cargar modelo de spaCy en español
nlp = spacy.load("es_core_news_sm")

# Carpeta de entrada y salida
carpeta_textos = "textos"
carpeta_salida = "documentos procesados"

# Crear carpeta de salida si no existe
os.makedirs(carpeta_salida, exist_ok=True)

for archivo in os.listdir(carpeta_textos):
    if archivo.endswith(".txt"):
        ruta = os.path.join(carpeta_textos, archivo)
        with open(ruta, "r", encoding="utf-8") as f:
            texto = f.read()
        
        # Procesamiento con spaCy
        doc = nlp(texto)

        # Estructura del documento (ignorando espacios)
        tokens = []
        for token in doc:
            if not token.is_space:  # 🚫 Ignorar espacios
                tokens.append({
                    "texto": token.text,          # forma original
                    "lema": token.lemma_,         # forma normalizada
                    "pos": token.pos_,            # categoría gramatical
                    "is_stopword": token.is_stop  # si es palabra vacía
                })

        documento = {
            "archivo": archivo,
            "contenido": tokens
        }

        # Nombre del archivo de salida (mismo nombre pero con .json)
        nombre_salida = os.path.splitext(archivo)[0] + ".json"
        ruta_salida = os.path.join(carpeta_salida, nombre_salida)

        # Guardar JSON individual
        with open(ruta_salida, "w", encoding="utf-8") as f:
            json.dump(documento, f, ensure_ascii=False, indent=4)

        print(f"Procesado: {archivo} -> {ruta_salida}")

print("✅ Procesamiento completado. Archivos guardados en 'documentos procesados'")

