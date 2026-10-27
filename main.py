# ------------------------------------------------------------------
# MOTOR DE COTIZACIÓN CON GEMINI AI (Normalizado para leer precio_base y proveedor_id)
# ------------------------------------------------------------------
@app.post("/api/buscar-cotizacion-inteligente")
async def buscar_cotizacion_inteligente(payload: dict):
    solicitud_usuario = payload.get("solicitud", "")
    if not solicitud_usuario:
        raise HTTPException(status_code=400, detail="La solicitud no puede estar vacía.")

    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Base de datos Supabase no conectada.")

        # 1. Obtener todo el inventario
        res = supabase.table("productos_inventario").select("*").execute()
        raw_inventario = res.data or []

        if not raw_inventario:
            return {
                "exito": False,
                "mensaje": "El inventario está vacío. Suba un catálogo primero.",
                "items": []
            }

        # 2. HOMOGENIZAR DATOS (Resuelve el problema del CSV)
        inventario_limpio = []
        for p in raw_inventario:
            # Obtener el precio correcto (prioriza precio_unitario, luego precio_base)
            precio = p.get("precio_unitario") or p.get("precio_base") or 0.0
            
            # Obtener el ID de proveedor correcto
            prov_id = p.get("id_proveedor") or p.get("proveedor_id") or ""

            # Solo incluir si el producto tiene un precio válido > 0 y un nombre con sentido
            if precio > 0 and p.get("nombre_producto"):
                inventario_limpio.append({
                    "id_producto": str(p.get("id")),
                    "id_proveedor": str(prov_id),
                    "nombre_producto": p.get("nombre_producto"),
                    "categoria": p.get("categoria") or "General",
                    "precio_unitario": float(precio),
                    "stock_disponible": p.get("stock_disponible") or p.get("stock") or 100,
                    "descripcion": p.get("descripcion") or p.get("nombre_producto")
                })

        prompt_sistema = f"""
        Eres el Agente Comprador Inteligente de PLANÉ (plataforma B2B de eventos).
        SOLICITUD DEL CLIENTE: "{solicitud_usuario}"
        INVENTARIO DISPONIBLE (Limpio): {json.dumps(inventario_limpio, ensure_ascii=False)}

        INSTRUCCIONES:
        1. Selecciona los ítems del inventario que coincidan con la solicitud (por ejemplo mesas, sillas, etc.).
        2. Si la solicitud pide N personas, calcula la cantidad de mesas y sillas necesarias.
        3. Calcula subtotales (cantidad * precio_unitario).
        4. Responde ÚNICAMENTE con un JSON válido con la siguiente estructura (sin formato Markdown ```json):

        {{
            "items_seleccionados": [
                {{
                    "id_producto": "ID_DEL_PRODUCTO",
                    "nombre_producto": "Nombre exacto del producto",
                    "proveedor": "Proveedor PLANÉ",
                    "cantidad_sugerida": 10,
                    "precio_unitario": 250.00,
                    "subtotal": 2500.00,
                    "nota_justificacion": "Explicación breve de por qué se seleccionó"
                }}
            ],
            "resumen_agente": "Explicación ejecutiva de la cotización armada"
        }}
        """

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_sistema)
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
