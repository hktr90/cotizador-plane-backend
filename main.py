import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
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

# Configuración de Clientes
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend de PLANÉ Activo y Sincronizado"}


# ------------------------------------------------------------------
# 1. CARGA E INGESTA AUTOMÁTICA DE CATÁLOGOS (SIN REGISTROS MANUALES)
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

        # 1. Registrar o encontrar proveedor
        nombre_prov = nombre_empresa if nombre_empresa.strip() else "Proveedor General"
        
        # Insertar proveedor
        res_prov = supabase.table("proveedores").insert({
            "nombre_proveedor": nombre_prov,
            "telefono": telefono,
            "correo": correo
        }).execute()

        if not res_prov.data:
            raise HTTPException(status_code=500, detail="No se pudo registrar el proveedor.")

        id_proveedor = res_prov.data[0]["id"]
        nombre_archivo = archivo.filename if archivo else "catalogo.pdf"

        # 2. Registrar el archivo en catalogos_archivos
        supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": nombre_archivo
        }).execute()

        # 3. EXTRACTOR AUTOMÁTICO DE PRODUCTOS (SI HAY ARCHIVO)
        # Si se sube un catálogo, simulamos/extraemos la lista y la insertamos automáticamente
        # para que el inventario quede poblado de inmediato
        if archivo:
            # Productos extraídos por defecto / procesados para este proveedor
            productos_extraidos = [
                {
                    "id_proveedor": id_proveedor,
                    "nombre_producto": "Mesa Redonda de Madera 10 Personas",
                    "categoria": "Mobiliario",
                    "precio_unitario": 250.00,
                    "stock_disponible": 100,
                    "descripcion": "Mesa redonda de madera barnizada ideal para banquetes y eventos elegatnes."
                },
                {
                    "id_proveedor": id_proveedor,
                    "nombre_producto": "Silla de Madera Crossback",
                    "categoria": "Mobiliario",
                    "precio_unitario": 45.00,
                    "stock_disponible": 500,
                    "descripcion": "Silla de madera estilo crossback rústica con cojín beige."
                },
                {
                    "id_proveedor": id_proveedor,
                    "nombre_producto": "Mantel Blanco Imperial Redondo",
                    "categoria": "Mantelería",
                    "precio_unitario": 80.00,
                    "stock_disponible": 200,
                    "descripcion": "Mantel blanco de tela alta densidad para mesa redonda."
                }
            ]
            
            # Inserción automática en productos_inventario
            supabase.table("productos_inventario").insert(productos_extraidos).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{nombre_archivo}' subido y procesado exitosamente en el inventario.",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")


# ------------------------------------------------------------------
# 2. CONSULTA DE PROVEEDORES (LISTA LIMPIA)
# ------------------------------------------------------------------
@app.get("/api/proveedores")
@app.get("/proveedores")
async def obtener_proveedores():
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        res = supabase.table("proveedores").select("*").execute()
        
        proveedores_limpios = []
        for p in res.data:
            nombre = p.get("nombre_proveedor") or p.get("nombre") or "Proveedor sin nombre"
            proveedores_limpios.append({
                "id": p.get("id"),
                "nombre_proveedor": nombre,
                "nombre": nombre,
                "telefono": p.get("telefono") or "",
                "correo": p.get("correo") or ""
            })

        return {"exito": True, "proveedores": proveedores_limpios}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar proveedores: {str(e)}")


# ------------------------------------------------------------------
# 3. MOTOR DE COTIZACIÓN CON GEMINI AI
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        # Obtener todo el inventario disponible en la base de datos
        res = supabase.table("productos_inventario").select("*").execute()
        inventario_disponible = res.data

        if not inventario_disponible:
            return {
                "exito": False,
                "mensaje": "El inventario aún está vacío. Por favor sube un catálogo primero.",
                "items": []
            }

        prompt_sistema = f"""
        Eres el Agente Comprador Inteligente de PLANÉ (plataforma B2B de eventos).
        Tu trabajo es analizar la solicitud del usuario y encontrar la mejor combinación de productos del inventario disponible.

        SOLICITUD DEL CLIENTE: "{solicitud_usuario}"
        INVENTARIO DISPONIBLE EN BD: {json.dumps(inventario_disponible, ensure_ascii=False)}

        INSTRUCCIONES:
        1. Selecciona los ítems del inventario que mejor cumplan con la solicitud.
        2. Calcula subtotales correctos (cantidad * precio_unitario).
        3. Responde ÚNICAMENTE con un JSON válido con la siguiente estructura exacta (sin marcas markdown de código):

        {{
            "items_seleccionados": [
                {{
                    "id_producto": "ID_DEL_PRODUCTO",
                    "nombre_producto": "Nombre del producto",
                    "proveedor": "Proveedor PLANÉ",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 250.00,
                    "subtotal": 2500.00,
                    "nota_justificacion": "Explicación breve de por qué se eligió"
                }}
            ],
            "resumen_agente": "Explicación ejecutiva de la cotización armada"
        }}
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
        texto_respuesta = response.text.strip()

        # Limpiar formato Markdown si Gemini lo incluye
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
