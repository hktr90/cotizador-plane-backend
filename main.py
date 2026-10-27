import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import google.generativeai as genai

app = FastAPI(title="Cotizador PLANÉ API")

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexiones externas
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


def obtener_modelo_valido():
    """Instancia el modelo actualizado gemini-3.8-flash con fallbacks"""
    candidatos = [
        'gemini-3.8-flash',
        'models/gemini-3.8-flash',
        'gemini-1.5-flash',
        'models/gemini-1.5-flash',
        'gemini-pro'
    ]
    
    for name in candidatos:
        try:
            return genai.GenerativeModel(name)
        except Exception as e:
            print(f"No se pudo cargar modelo {name}: {e}")

    raise Exception("No se encontró ningún modelo de Gemini disponible con la API Key configurada.")


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend PLANÉ Activo"}


# ------------------------------------------------------------------
# 1. RUTA PRINCIPAL DE BÚSQUEDA DEL FRONTEND (GET /api/buscar-productos)
# ------------------------------------------------------------------
@app.get("/api/buscar-productos")
async def buscar_productos(query: str = Query("")):
    if not query:
        return {"exito": True, "datos": {"items_seleccionados": [], "resumen_agente": "Solicitud vacía."}}

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        # Consultar inventario disponible en la base de datos
        res = supabase.table("productos_inventario").select("*").execute()
        raw_inventario = res.data or []

        if not raw_inventario:
            return {
                "exito": False,
                "mensaje": "El inventario está vacío.",
                "items": []
            }

        # Filtrar campos estrictamente necesarios para acelerar la respuesta de la IA
        inventario_resumido = []
        for p in raw_inventario:
            precio = p.get("precio_unitario") or p.get("precio_base") or 0.0
            prov_id = p.get("id_proveedor") or p.get("proveedor_id") or ""

            if precio > 0 and p.get("nombre_producto"):
                inventario_resumido.append({
                    "id": str(p.get("id")),
                    "nombre": str(p.get("nombre_producto")),
                    "precio": float(precio)
                })

        if not inventario_resumido:
            return {
                "exito": False,
                "mensaje": "No hay productos con precio válido en la base de datos.",
                "items": []
            }

        prompt_sistema = f"""
        Eres el Agente Comprador de PLANÉ (eventos B2B).
        SOLICITUD: "{query}"
        INVENTARIO DISPONIBLE: {json.dumps(inventario_resumido, ensure_ascii=False)}

        INSTRUCCIONES:
        1. Selecciona los ítems que respondan a la solicitud.
        2. Calcula subtotales (cantidad * precio).
        3. Responde ÚNICAMENTE en JSON válido con este formato exacto (sin marcas markdown ```json):

        {{
            "items_seleccionados": [
                {{
                    "id_producto": "ID",
                    "nombre_producto": "Nombre del producto",
                    "proveedor": "Proveedor PLANÉ",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 45.00,
                    "subtotal": 450.00,
                    "nota_justificacion": "Explicación breve"
                }}
            ],
            "resumen_agente": "Resumen ejecutivo de la propuesta"
        }}
        """

        model = obtener_modelo_valido()
        
        # Generación ultrarrápida con temperatura baja y límites de tiempo
        response = model.generate_content(
            prompt_sistema,
            generation_config={"temperature": 0.1, "max_output_tokens": 800},
            request_options={"timeout": 12.0}
        )
        
        texto_respuesta = response.text.strip()

        # Limpiar formato Markdown
        if texto_respuesta.startswith("```json"):
            texto_respuesta = texto_respuesta.replace("```json", "", 1)
        if texto_respuesta.startswith("```"):
            texto_respuesta = texto_respuesta.replace("```", "", 1)
        if texto_respuesta.endswith("```"):
            texto_respuesta = texto_respuesta[:-3]

        return {"exito": True, "datos": json.loads(texto_respuesta.strip())}

    except Exception as e:
        print(f"Error en motor Gemini: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error procesando cotización: {str(e)}")


# ------------------------------------------------------------------
# 2. SUBIDA DE CATÁLOGOS
# ------------------------------------------------------------------
@app.post("/upload-catalog")
@app.post("/api/subir-catalogo")
async def subir_catalogo(
    nombre_empresa: str = Form(""),
    telefono: str = Form(""),
    correo: str = Form(""),
    archivo: UploadFile = File(None)
):
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        nombre_prov = nombre_empresa.strip() if nombre_empresa.strip() else "Proveedor General"
        
        res_prov = supabase.table("proveedores").insert({
            "nombre_proveedor": nombre_prov,
            "telefono": telefono,
            "correo": correo
        }).execute()

        if not res_prov.data:
            raise HTTPException(status_code=500, detail="Error al registrar proveedor.")

        id_proveedor = res_prov.data[0]["id"]
        nombre_archivo = archivo.filename if archivo else "catalogo.pdf"

        supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": nombre_archivo
        }).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{nombre_archivo}' guardado exitosamente.",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")
