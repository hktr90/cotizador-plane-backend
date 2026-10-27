import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import google.generativeai as genai

app = FastAPI(title="Cotizador PLANÉ API")

# Habilitar CORS completo para Netlify
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Configuración de Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend de PLANÉ activo"}


# ------------------------------------------------------------------
# SUBIDA DE CATÁLOGOS (Acepta ambas rutas para evitar errores 404)
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

        # 1. Registrar o actualizar proveedor
        res_proveedor = supabase.table("proveedores").insert({
            "nombre_proveedor": nombre_empresa if nombre_empresa else "Proveedor Sin Nombre",
            "telefono": telefono,
            "correo": correo
        }).execute()

        id_proveedor = res_proveedor.data[0]["id"] if res_proveedor.data else None
        nombre_archivo = archivo.filename if archivo else "catalogo_sin_nombre.pdf"

        # 2. Registrar el archivo en la BD
        supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": nombre_archivo
        }).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{nombre_archivo}' subido con éxito para {nombre_empresa}.",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")


# ------------------------------------------------------------------
# CONSULTA DE PROVEEDORES
# ------------------------------------------------------------------
@app.get("/api/proveedores")
@app.get("/proveedores")
async def obtener_proveedores():
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        res = supabase.table("proveedores").select("*").execute()
        return {"exito": True, "proveedores": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar proveedores: {str(e)}")


# ------------------------------------------------------------------
# MOTOR DE COTIZACIÓN INTELEGENTE (GEMINI)
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        res = supabase.table("productos_inventario").select("*, proveedores(nombre_proveedor)").execute()
        inventario_disponible = res.data

        if not inventario_disponible:
            return {
                "exito": False,
                "mensaje": "No hay productos registrados en el inventario.",
                "items": []
            }

        prompt_sistema = f"""
        Eres el Agente Comprador de PLANÉ (empresa de eventos B2B).
        SOLICITUD: "{solicitud_usuario}"
        INVENTARIO: {json.dumps(inventario_disponible, ensure_ascii=False)}

        Devuelve UNICAMENTE un JSON válido (sin formato markdown):
        {{
            "items_seleccionados": [
                {{
                    "id_producto": 123,
                    "nombre_producto": "Nombre",
                    "proveedor": "Proveedor",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 100.00,
                    "subtotal": 1000.00,
                    "nota_justificacion": "Explicación de selección"
                }}
            ],
            "resumen_agente": "Explicación ejecutiva"
        }}
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
        texto_respuesta = response.text.strip()

        if texto_respuesta.startswith("```json"):
            texto_respuesta = texto_respuesta.replace("```json", "", 1)
        if texto_respuesta.endswith("```"):
            texto_respuesta = texto_respuesta[:-3]

        return {"exito": True, "datos": json.loads(texto_respuesta.strip())}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en motor Gemini: {str(e)}")
