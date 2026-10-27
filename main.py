import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import google.generativeai as genai

app = FastAPI(title="Cotizador PLANÉ API")

# Configurar CORS habilitando todos los orígenes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializar Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Configurar Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


@app.get("/")
def home():
    return {"message": "API Cotizador PLANÉ funcionando correctamente"}


# ------------------------------------------------------------------
# RUTA DE CARGA DE CATÁLOGOS (Acepta /upload-catalog y /api/subir-catalogo)
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
            raise HTTPException(status_code=500, detail="Base de datos Supabase no configurada.")

        # Registrar el proveedor en la tabla 'proveedores'
        res_proveedor = supabase.table("proveedores").insert({
            "nombre_proveedor": nombre_empresa if nombre_empresa else "Proveedor General",
            "telefono": telefono,
            "correo": correo
        }).execute()

        id_proveedor = res_proveedor.data[0]["id"] if res_proveedor.data else None
        nombre_archivo = archivo.filename if archivo else "catalogo_sin_nombre"

        # Registrar la referencia del catálogo en 'catalogos_archivos'
        supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": nombre_archivo
        }).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{nombre_archivo}' registrado correctamente para {nombre_empresa}.",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en servidor: {str(e)}")


# ------------------------------------------------------------------
# RUTA CONSULTA DE PROVEEDORES
# ------------------------------------------------------------------
@app.get("/api/proveedores")
@app.get("/proveedores")
async def obtener_proveedores():
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no configurada.")

        res = supabase.table("proveedores").select("*").execute()
        return {"exito": True, "proveedores": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en servidor: {str(e)}")


# ------------------------------------------------------------------
# RUTA BÚSQUEDA INTELIGENTE CON GEMINI
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no configurada.")

        res = supabase.table("productos_inventario").select("*, proveedores(nombre_proveedor)").execute()
        inventario_disponible = res.data

        if not inventario_disponible:
            return {
                "exito": False,
                "mensaje": "No hay productos registrados en el inventario de Supabase.",
                "items": []
            }

        prompt_sistema = f"""
        Eres el Agente Comprador y Cotizador Experto para PLANÉ (empresa de eventos B2B).
        
        SOLICITUD DEL CLIENTE:
        "{solicitud_usuario}"

        INVENTARIO DISPONIBLE DE PROVEEDORES (JSON):
        {json.dumps(inventario_disponible, ensure_ascii=False)}

        INSTRUCCIONES DE SELECCIÓN Y JUICIO:
        1. Analiza el requerimiento del cliente (número de personas, tipo de evento, estilo, materiales).
        2. Deduce la cantidad lógica necesaria si el usuario no especifica exactamente.
        3. Busca coincidencias exactas o los mejores sustitutos por color, textura o material.
        4. Agrupa en lo posible con los mismos proveedores para minimizar costos de flete.
        
        FORMATO DE RESPUESTA REQUERIDO:
        Devuelve EXCLUSIVAMENTE un objeto JSON válido (sin marcas de markdown) con esta estructura:
        {{
            "items_seleccionados": [
                {{
                    "id_producto": 123,
                    "nombre_producto": "Nombre del producto",
                    "proveedor": "Nombre del Proveedor",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 150.00,
                    "subtotal": 1500.00,
                    "nota_justificacion": "Explicación breve de por qué se eligió este producto"
                }}
            ],
            "resumen_agente": "Explicación ejecutiva de la propuesta para PLANÉ."
        }}
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
        texto_respuesta = response.text.strip()

        if texto_respuesta.startswith("```json"):
            texto_respuesta = texto_respuesta.replace("```json", "", 1)
        if texto_respuesta.endswith("```"):
            texto_respuesta = texto_respuesta[:-3]

        resultado_json = json.loads(texto_respuesta.strip())

        return {
            "exito": True,
            "datos": resultado_json
        }

    except Exception as e:
        print(f"Error en motor de cotización: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en motor de cotización: {str(e)}")