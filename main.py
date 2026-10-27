import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import google.generativeai as genai

app = FastAPI(title="Cotizador PLANÉ API")

# Configurar CORS para permitir peticiones desde Netlify / Frontend
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

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ADVERTENCIA: Credenciales de Supabase no encontradas en las variables de entorno.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Configurar Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


@app.get("/")
def home():
    return {"message": "API Cotizador PLANÉ funcionando correctamente"}


# ------------------------------------------------------------------
# 1. RUTA PARA SUBIR Y PROCESAR CATÁLOGOS (Resuelve el 404 de /upload-catalog)
# ------------------------------------------------------------------
@app.post("/upload-catalog")
@app.post("/api/subir-catalogo")
async def subir_catalogo(
    nombre_empresa: str = Form(...),
    telefono: str = Form(...),
    correo: str = Form(...),
    archivo: UploadFile = File(...)
):
    """
    Recibe el formulario de proveedores y el archivo del catálogo (PDF/Excel),
    registra al proveedor en Supabase y guarda la referencia.
    """
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no configurada.")

        # 1. Insertar o verificar proveedor en la tabla 'proveedores'
        res_proveedor = supabase.table("proveedores").insert({
            "nombre_proveedor": nombre_empresa,
            "telefono": telefono,
            "correo": correo
        }).execute()

        id_proveedor = res_proveedor.data[0]["id"] if res_proveedor.data else None

        # 2. Registrar el archivo subido en 'catalogos_archivos'
        res_catalogo = supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": archivo.filename
        }).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{archivo.filename}' de {nombre_empresa} subido y registrado exitosamente.",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al procesar el catálogo: {str(e)}")


# ------------------------------------------------------------------
# 2. RUTA PARA OBTENER PROVEEDORES (Resuelve el 404 de /api/proveedores)
# ------------------------------------------------------------------
@app.get("/api/proveedores")
async def obtener_proveedores():
    """
    Devuelve la lista completa de proveedores registrados en Supabase.
    """
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no configurada.")

        res = supabase.table("proveedores").select("*").execute()
        return {"exito": True, "proveedores": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error consultando proveedores: {str(e)}")


# ------------------------------------------------------------------
# 3. RUTA DE BÚSQUEDA INTELEGENTE / AGENTE GEMINI
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    """
    Recibe el prompt en lenguaje natural del usuario y utiliza Gemini 
    para seleccionar los productos adecuados del inventario en Supabase.
    """
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no configurada.")

        # Obtener inventario actual de Supabase
        res = supabase.table("productos_inventario").select("*, proveedores(nombre_proveedor)").execute()
        inventario_disponible = res.data

        if not inventario_disponible:
            return {
                "exito": False,
                "mensaje": "No hay productos registrados en el inventario de Supabase.",
                "items": []
            }

        # Construir el Prompt para el Agente Comprador
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
        Devuelve EXCLUSIVAMENTE un objeto JSON válido (sin marcas de markdown de ```json) con esta estructura:
        {{
            "items_seleccionados": [
                {{
                    "id_producto": 123,
                    "nombre_producto": "Nombre del producto",
                    "proveedor": "Nombre del Proveedor",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 150.00,
                    "subtotal": 1500.00,
                    "nota_justificacion": "Explicación breve de por qué se eligió este producto o sustituto"
                }}
            ],
            "resumen_agente": "Explicación ejecutiva de la propuesta para el equipo de PLANÉ."
        }}
        """

        # Llamada a la API de Gemini
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
        texto_respuesta = response.text.strip()

        # Limpieza de markdown en el JSON
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
        raise HTTPException(status_code=500, detail=f"Error en el motor de cotización: {str(e)}")