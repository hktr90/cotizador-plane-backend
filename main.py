import os
import json
import io
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import google.generativeai as genai
import pypdf

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


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend PLANÉ con Inteligencia Activa"}


# ------------------------------------------------------------------
# 1. RUTA REQUERIDA POR EL FRONTEND: GET /api/buscar-productos
# ------------------------------------------------------------------
@app.get("/api/buscar-productos")
async def buscar_productos(query: str = Query("")):
    if not query:
        return {"exito": True, "datos": {"items_seleccionados": [], "resumen_agente": "Solicitud vacía."}}

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no conectada.")

        # Consultar inventario disponible
        res = supabase.table("productos_inventario").select("*").execute()
        raw_inventario = res.data or []

        if not raw_inventario:
            return {
                "exito": False,
                "mensaje": "El inventario está vacío.",
                "items": []
            }

        inventario_limpio = []
        for p in raw_inventario:
            precio = p.get("precio_unitario") or p.get("precio_base") or 0.0
            prov_id = p.get("id_proveedor") or p.get("proveedor_id") or ""

            if precio > 0 and p.get("nombre_producto"):
                inventario_limpio.append({
                    "id_producto": str(p.get("id")),
                    "id_proveedor": str(prov_id),
                    "nombre_producto": str(p.get("nombre_producto")),
                    "categoria": p.get("categoria") or "General",
                    "precio_unitario": float(precio),
                    "stock_disponible": p.get("stock_disponible") or p.get("stock") or 100,
                    "descripcion": p.get("descripcion") or p.get("nombre_producto")
                })

        prompt_sistema = f"""
        Eres el Agente Comprador Inteligente de PLANÉ (plataforma B2B para eventos).
        SOLICITUD DEL CLIENTE: "{query}"
        INVENTARIO DISPONIBLE EN BD: {json.dumps(inventario_limpio, ensure_ascii=False)}

        INSTRUCCIONES:
        1. Identifica en el inventario los productos que respondan a la solicitud del cliente.
        2. Si pide mobiliario o artículos para N personas, calcula las cantidades adecuadas.
        3. Multiplica cantidad por precio_unitario para obtener subtotales.
        4. Responde ÚNICAMENTE en formato JSON válido (sin etiquetas markdown ```json):

        {{
            "items_seleccionados": [
                {{
                    "id_producto": "ID",
                    "nombre_producto": "Nombre del producto",
                    "proveedor": "Proveedor PLANÉ",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 250.00,
                    "subtotal": 2500.00,
                    "nota_justificacion": "Razón de selección"
                }}
            ],
            "resumen_agente": "Explicación breve de la propuesta"
        }}
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
        texto_respuesta = response.text.strip()

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
