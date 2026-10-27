import os
import json
import asyncio
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from google import genai
from google.genai import types

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
client_ai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend PLANÉ con SDK Actualizada"}


# ------------------------------------------------------------------
# 1. BUSCADOR INTELIGENTE Y COTIZADOR (GET /api/buscar-productos)
# ------------------------------------------------------------------
@app.get("/api/buscar-productos")
async def buscar_productos(query: str = Query("")):
    if not query:
        return {"exito": True, "datos": {"items_seleccionados": [], "resumen_agente": "Solicitud vacía."}}

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        # Obtener productos del inventario
        res = supabase.table("productos_inventario").select("*").execute()
        raw_inventario = res.data or []

        if not raw_inventario:
            return {
                "exito": False,
                "mensaje": "El inventario está vacío.",
                "items": []
            }

        # Filtrar únicamente campos indispensables para respuesta ultra rápida
        inventario_optimizado = []
        for p in raw_inventario:
            precio = p.get("precio_unitario") or p.get("precio_base") or 0.0
            if precio > 0 and p.get("nombre_producto"):
                inventario_optimizado.append({
                    "id": str(p.get("id")),
                    "nombre": str(p.get("nombre_producto")),
                    "precio": float(precio)
                })

        if not inventario_optimizado:
            return {
                "exito": False,
                "mensaje": "No hay productos con precio válido.",
                "items": []
            }

        prompt_sistema = f"""
        Eres el Agente Comprador de PLANÉ (eventos B2B).
        SOLICITUD CLIENTE: "{query}"
        INVENTARIO DISPONIBLE: {json.dumps(inventario_optimizado, ensure_ascii=False)}

        INSTRUCCIONES:
        1. Selecciona los ítems que respondan a la solicitud.
        2. Calcula subtotales (cantidad * precio).
        3. Responde ÚNICAMENTE en JSON válido con esta estructura (sin formato Markdown):

        {{
            "items_seleccionados": [
                {{
                    "id_producto": "ID",
                    "nombre_producto": "Nombre exacto",
                    "proveedor": "Proveedor PLANÉ",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 45.00,
                    "subtotal": 450.00,
                    "nota_justificacion": "Razón breve de selección"
                }}
            ],
            "resumen_agente": "Propuesta rápida"
        }}
        """

        if not client_ai:
            raise HTTPException(status_code=500, detail="GEMINI_API_KEY no configurada.")

        # Invocación no bloqueante con timeout estricto de 8 segundos
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client_ai.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt_sistema,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=600
                    )
                )
            ),
            timeout=8.0
        )

        texto_respuesta = response.text.strip()

        # Limpiar etiquetas markdown si existen
        if texto_respuesta.startswith("```json"):
            texto_respuesta = texto_respuesta.replace("```json", "", 1)
        if texto_respuesta.startswith("```"):
            texto_respuesta = texto_respuesta.replace("```", "", 1)
        if texto_respuesta.endswith("```"):
            texto_respuesta = texto_respuesta[:-3]

        return {"exito": True, "datos": json.loads(texto_respuesta.strip())}

    except asyncio.TimeoutError:
        print("Timeout consultando Gemini")
        raise HTTPException(status_code=504, detail="El tiempo de respuesta de la IA excedió el límite.")
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
