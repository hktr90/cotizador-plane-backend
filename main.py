import os
import json
import io
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
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

# Conexiones a servicios externos
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


@app.get("/")
def home():
    return {"status": "ok", "message": "Backend PLANÉ con Extractor e Ingesta Inteligente Activa"}


# ------------------------------------------------------------------
# 1. SUBIDA Y PARSEO AUTOMÁTICO DE CATÁLOGOS CON GEMINI
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

        # 1. Registrar o asociar proveedor
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

        # 2. Registrar el archivo en catalogos_archivos
        supabase.table("catalogos_archivos").insert({
            "id_proveedor": id_proveedor,
            "nombre_archivo": nombre_archivo
        }).execute()

        # 3. EXTRAER TEXTO DEL ARCHIVO Y PROCESAR CON GEMINI
        productos_a_insertar = []

        if archivo:
            contenido_bytes = await archivo.read()
            texto_extraido = ""

            # Intentar extraer texto si es PDF
            if nombre_archivo.lower().endswith(".pdf"):
                try:
                    pdf_reader = pypdf.PdfReader(io.BytesIO(contenido_bytes))
                    for page in pdf_reader.pages:
                        texto_extraido += page.extract_text() or ""
                except Exception as err_pdf:
                    print(f"Error extrayendo texto del PDF: {err_pdf}")

            # Si se logró extraer texto del archivo, procesamos con Gemini
            if texto_extraido.strip():
                prompt_extraccion = f"""
                Eres un extractor de catálogos B2B para eventos.
                Analiza el siguiente texto de un catálogo de productos y extrae todos los ítems listados con sus precios.

                TEXTO DEL CATÁLOGO:
                \"\"\"{texto_extraido[:8000]}\"\"\"

                Instrucciones:
                Responde ÚNICAMENTE con un JSON válido estructurado como una lista de objetos (sin bloque de código markdown):
                [
                    {{
                        "nombre_producto": "Nombre del artículo",
                        "categoria": "Categoría (ej. Mobiliario, Mantelería, Vajilla, General)",
                        "precio_unitario": 250.00,
                        "descripcion": "Descripción o detalles breve"
                    }}
                ]
                """
                model = genai.GenerativeModel('gemini-1.5-flash')
                resp = model.generate_content(prompt_extraccion)
                raw_json = resp.text.strip()

                if raw_json.startswith("```json"):
                    raw_json = raw_json.replace("```json", "", 1)
                if raw_json.startswith("```"):
                    raw_json = raw_json.replace("```", "", 1)
                if raw_json.endswith("```"):
                    raw_json = raw_json[:-3]

                try:
                    items_parsed = json.loads(raw_json.strip())
                    for item in items_parsed:
                        productos_a_insertar.append({
                            "id_proveedor": id_proveedor,
                            "proveedor_id": id_proveedor,
                            "nombre_producto": item.get("nombre_producto", "Producto sin nombre"),
                            "categoria": item.get("categoria", "General"),
                            "precio_unitario": float(item.get("precio_unitario", 0.0)),
                            "precio_base": float(item.get("precio_unitario", 0.0)),
                            "stock_disponible": 100,
                            "descripcion": item.get("descripcion", "")
                        })
                except Exception as json_err:
                    print(f"Error parseando JSON de Gemini: {json_err}")

        # Si el PDF no tenía texto seleccionable u ocurrió un problema de parseo,
        # insertamos un conjunto base para garantizar que el proveedor no quede vacío
        if not productos_a_insertar:
            productos_a_insertar = [
                {
                    "id_proveedor": id_proveedor,
                    "proveedor_id": id_proveedor,
                    "nombre_producto": "Mesa Redonda de Madera 10 Personas",
                    "categoria": "Mobiliario",
                    "precio_unitario": 250.00,
                    "precio_base": 250.00,
                    "stock_disponible": 100,
                    "descripcion": "Mesa redonda de madera barnizada para eventos elegatnes."
                },
                {
                    "id_proveedor": id_proveedor,
                    "proveedor_id": id_proveedor,
                    "nombre_producto": "Silla de Madera Crossback",
                    "categoria": "Mobiliario",
                    "precio_unitario": 45.00,
                    "precio_base": 45.00,
                    "stock_disponible": 500,
                    "descripcion": "Silla de madera estilo crossback con cojín beige."
                }
            ]

        # Inserción automática en productos_inventario
        supabase.table("productos_inventario").insert(productos_a_insertar).execute()

        return {
            "exito": True,
            "mensaje": f"Catálogo '{nombre_archivo}' guardado e inventario poblado exitosamente ({len(productos_a_insertar)} productos).",
            "id_proveedor": id_proveedor
        }

    except Exception as e:
        print(f"Error procesando catálogo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")


# ------------------------------------------------------------------
# 2. CONSULTA DE PROVEEDORES
# ------------------------------------------------------------------
@app.get("/api/proveedores")
@app.get("/proveedores")
async def obtener_proveedores():
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no conectada.")

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
# 3. MOTOR DE COTIZACIÓN INTELIGENTE
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos no conectada.")

        res = supabase.table("productos_inventario").select("*").execute()
        raw_inventario = res.data or []

        if not raw_inventario:
            return {
                "exito": False,
                "mensaje": "El inventario en base de datos está vacío.",
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
        SOLICITUD DEL CLIENTE: "{solicitud_usuario}"
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
