import os
import io
import re
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
import pypdf

# -----------------------------------------------------------------------------
# CONFIGURACIÓN E INICIALIZACIÓN
# -----------------------------------------------------------------------------
# Cargar variables del archivo .env sobrescribiendo el entorno si es necesario
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

# Validación estricta para evitar errores silenciosos al inicializar el cliente
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "❌ ERROR DE CONFIGURACIÓN: No se encontraron 'SUPABASE_URL' o 'SUPABASE_ANON_KEY' "
        "en tu archivo .env. Revisa que el archivo esté guardado en la raíz y con las claves correctas."
    )

# Cliente oficial de Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="API Cotizador B2B — PLANÉ",
    description="Backend para ingesta de catálogos PDF/Excel, consulta de inventario con margen del 30% y emisión de cotizaciones.",
    version="1.0.0"
)

# Permitir solicitudes CORS desde cualquier origen (pantallas HTML locales o desplegadas en Netlify)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# MODELOS DE DATOS (PYDANTIC)
# -----------------------------------------------------------------------------
class ItemCotizacionInput(BaseModel):
    producto_id: Optional[str] = None
    proveedor_id: Optional[str] = None
    concepto_nombre: str
    cantidad: int = 1
    precio_costo_proveedor: float
    es_concepto_adicional: bool = False

class SolicitudCotizacionInput(BaseModel):
    cliente_nombre: str
    fecha_evento: Optional[str] = None
    lugar_evento: Optional[str] = None
    margen_porcentaje: float = 30.0  # Margen estándar del 30%
    items: List[ItemCotizacionInput]

# -----------------------------------------------------------------------------
# ENDPOINTS / RUTAS DE LA API
# -----------------------------------------------------------------------------

@app.get("/")
def home():
    """Estado de salud de la API."""
    return {
        "status": "online",
        "empresa": "PLANÉ — Experiencias de Marca & Eventos Corporativos",
        "base_datos": "Supabase Conectado"
    }

# -----------------------------------------------------------------------------
# 1. GESTIÓN DE PROVEEDORES
# -----------------------------------------------------------------------------
@app.get("/api/proveedores")
def listar_proveedores():
    """Retorna la lista de todos los proveedores registrados en Supabase."""
    try:
        response = supabase.table("proveedores").select("*").eq("activo", True).execute()
        return {"proveedores": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar proveedores: {str(e)}")

@app.post("/api/proveedores")
def crear_proveedor(nombre: str = Form(...), contacto: Optional[str] = Form(None), telefono: Optional[str] = Form(None)):
    """Crea un nuevo proveedor en la base de datos."""
    try:
        data = {
            "nombre": nombre,
            "contacto_nombre": contacto,
            "telefono": telefono
        }
        response = supabase.table("proveedores").insert(data).execute()
        return {"status": "ok", "proveedor": response.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al crear proveedor: {str(e)}")

# -----------------------------------------------------------------------------
# 2. CARGA Y EXTRACCIÓN DE CATÁLOGOS (PDF/Excel)
# -----------------------------------------------------------------------------
@app.post("/upload-catalog")
async def procesar_catalogo(
    proveedor_nombre: Optional[str] = Form("Proveedor General"),
    file: UploadFile = File(...)
):
    """
    Recibe un archivo PDF o Excel desde la pantalla de Netlify, extrae productos y precios base,
    y guarda el inventario unificado en Supabase.
    """
    if not file.filename.endswith(('.pdf', '.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Formato no soportado. Sube un archivo PDF o Excel.")

    try:
        # 1. Obtener o crear proveedor
        prov_res = supabase.table("proveedores").select("id").eq("nombre", proveedor_nombre).execute()
        if prov_res.data:
            proveedor_id = prov_res.data[0]["id"]
        else:
            nuevo_prov = supabase.table("proveedores").insert({"nombre": proveedor_nombre}).execute()
            proveedor_id = nuevo_prov.data[0]["id"]

        # 2. Registrar el archivo en historial
        cat_res = supabase.table("catalogos_archivos").insert({
            "proveedor_id": proveedor_id,
            "nombre_archivo": file.filename
        }).execute()
        catalogo_id = cat_res.data[0]["id"]

        # 3. Lectura de contenido PDF usando PyPDF
        contents = await file.read()
        items_extraidos = []

        if file.filename.endswith('.pdf'):
            pdf_reader = pypdf.PdfReader(io.BytesIO(contents))
            texto_completo = ""
            for page in pdf_reader.pages:
                texto_completo += page.extract_text() or ""

            # Patron para detectar líneas del tipo: "Silla Crossback $45.00"
            patron = r'([A-Za-z0-9\sÁÉÍÓÚáéíóúÑñ\-\.\,]{3,60})\s+\$?(\d+[\.\,]\d{2}|\d+)'
            coincidencias = re.findall(patron, texto_completo)

            for nombre_prod, precio_str in coincidencias:
                nombre_limpio = nombre_prod.strip().replace('\n', ' ')
                if len(nombre_limpio) > 3 and not nombre_limpio.lower().startswith(('página', 'total', 'tel')):
                    precio_limpio = float(precio_str.replace(',', ''))
                    if precio_limpio > 0:
                        items_extraidos.append({
                            "proveedor_id": proveedor_id,
                            "catalogo_id": catalogo_id,
                            "nombre_producto": nombre_limpio,
                            "nombre_normalizado": nombre_limpio.lower(),
                            "precio_base": precio_limpio,
                            "categoria": "General"
                        })

        # 4. Inserción masiva en Supabase
        if items_extraidos:
            supabase.table("productos_inventario").insert(items_extraidos).execute()
            supabase.table("catalogos_archivos").update({
                "procesado": True,
                "total_items_extraidos": len(items_extraidos)
            }).eq("id", catalogo_id).execute()

        return {
            "status": "éxito",
            "proveedor": proveedor_nombre,
            "archivo": file.filename,
            "items_extraidos": len(items_extraidos)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando catálogo: {str(e)}")

# -----------------------------------------------------------------------------
# 3. BÚSQUEDA Y COMPARATIVO DE PRODUCTOS
# -----------------------------------------------------------------------------
@app.get("/api/buscar-productos")
def buscar_productos(query: str = Query(..., min_length=2)):
    """Busca coincidencias en el inventario de Supabase para comparar precios."""
    try:
        response = supabase.table("productos_inventario")\
            .select("*, proveedores(nombre)")\
            .ilike("nombre_producto", f"%{query}%")\
            .eq("activo", True)\
            .execute()

        return {"busqueda": query, "resultados": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en búsqueda: {str(e)}")

# -----------------------------------------------------------------------------
# 4. EMISIÓN Y GUARDADO DE COTIZACIÓN
# -----------------------------------------------------------------------------
@app.post("/api/generar-cotizacion")
def crear_cotizacion(payload: SolicitudCotizacionInput):
    """Calcula subtotal con el % de margen (30%), IVA (16%) y guarda en Supabase."""
    try:
        subtotal_base = 0.0
        subtotal_cliente = 0.0
        items_procesados = []

        multiplicador_margen = 1 + (payload.margen_porcentaje / 100.0)

        for item in payload.items:
            precio_cliente = round(item.precio_costo_proveedor * multiplicador_margen, 2)
            subtotal_item = round(precio_cliente * item.cantidad, 2)
            
            subtotal_base += (item.precio_costo_proveedor * item.cantidad)
            subtotal_cliente += subtotal_item

            items_procesados.append({
                "producto_id": item.producto_id,
                "proveedor_id": item.proveedor_id,
                "concepto_nombre": item.concepto_nombre,
                "cantidad": item.cantidad,
                "precio_costo_proveedor": item.precio_costo_proveedor,
                "precio_unitario_cliente": precio_cliente,
                "subtotal_item": subtotal_item,
                "es_concepto_adicional": item.es_concepto_adicional
            })

        iva = round(subtotal_cliente * 0.16, 2)
        total_final = round(subtotal_cliente + iva, 2)

        import random
        folio = f"PLN-2026-{random.randint(1000, 9999)}"

        cot_res = supabase.table("cotizaciones").insert({
            "folio": folio,
            "cliente_nombre": payload.cliente_nombre,
            "fecha_evento": payload.fecha_evento,
            "lugar_evento": payload.lugar_evento,
            "margen_porcentaje": payload.margen_porcentaje,
            "subtotal": subtotal_cliente,
            "iva": iva,
            "total": total_final,
            "estado": "Borrador"
        }).execute()

        cotizacion_id = cot_res.data[0]["id"]

        for item in items_procesados:
            item["cotizacion_id"] = cotizacion_id

        supabase.table("detalle_cotizacion_items").insert(items_procesados).execute()

        return {
            "status": "Cotización creada con éxito",
            "folio": folio,
            "resumen": {
                "cliente": payload.cliente_nombre,
                "subtotal": subtotal_cliente,
                "iva_16": iva,
                "total_mxn": total_final,
                "margen_aplicado": f"{payload.margen_porcentaje}%"
            },
            "cotizacion_id": cotizacion_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando cotización: {str(e)}")
