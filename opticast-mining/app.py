import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import io
import xlsxwriter
from datetime import datetime

# --- 1. CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="OptiCast Mining",
    page_icon="💎",
    layout="wide"
)

# --- 2. TÍTULO Y DESCRIPCIÓN ---
st.title("💎 OptiCast Mining: Proyector Financiero IA")
st.markdown("""
Esta aplicación utiliza **Google Gemini 2.5 Flash** para proyectar presupuestos mineros basándose en datos históricos 
y reglas de negocio no lineales (Estacionalidad + Drivers).
""")

# --- 3. BARRA LATERAL: CONFIGURACIÓN ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Gestión de API Key (Prioridad: Secrets de la nube > Input manual)
    api_key = None
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    else:
        api_key = st.text_input("Ingresa tu Google AI API Key", type="password")
        if not api_key:
            st.warning("⚠️ Necesitas una API Key para ejecutar el modelo.")
        
    st.divider()
    st.subheader("📥 Datos Históricos (Cierre 2025)")
    
    # Inputs numéricos para los gastos del año anterior
    contractors = st.number_input("Contractors ($)", value=250000, step=1000)
    labor = st.number_input("Labor ($)", value=180000, step=1000)
    fuel = st.number_input("Fuel ($)", value=85000, step=1000)
    power = st.number_input("Power ($)", value=120000, step=1000)
    maintenance = st.number_input("Maintenance ($)", value=60000, step=1000)

# --- FUNCION PARA GENERAR EXCEL (Adaptada de TypeScript a Python) ---
def generate_excel_report(kpis, df_detail, analysis_text, input_data):
    output = io.BytesIO()
    
    # Usar xlsxwriter como motor
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # --- ESTILOS ---
        bold_format = workbook.add_format({'bold': True})
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
        currency_format = workbook.add_format({'num_format': '$#,##0'})
        pct_format = workbook.add_format({'num_format': '0.0%'})
        text_wrap_format = workbook.add_format({'text_wrap': True, 'valign': 'top'})
        
        # === SHEET 1: RESUMEN GERENCIAL ===
        ws_summary = workbook.add_worksheet('Resumen_Gerencial')
        
        # Título y Fecha
        ws_summary.write(0, 0, "REPORTE FINANCIERO - OPTICAST MINING", bold_format)
        ws_summary.write(1, 0, "Fecha de Generación:")
        ws_summary.write(1, 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        ws_summary.write(2, 0, "Modelo IA Utilizado:")
        ws_summary.write(2, 1, "Gemini 2.5 Flash")
        
        # Tabla de KPIs
        ws_summary.write(4, 0, "KPIs PRINCIPALES (USD)", bold_format)
        headers_kpi = ["Concepto", "Monto", "Estado/Nota"]
        for col, h in enumerate(headers_kpi):
            ws_summary.write(5, col, h, header_format)
            
        # Datos KPIs
        ws_summary.write(6, 0, "Forecast 2025 (Cierre)")
        ws_summary.write(6, 1, kpis['total_2025'], currency_format)
        ws_summary.write(6, 2, "Base Histórica")
        
        ws_summary.write(7, 0, "Budget 2026 (Target)")
        ws_summary.write(7, 1, kpis['total_2026'], currency_format)
        ws_summary.write(7, 2, "Meta Proyectada")
        
        # Validación de división por cero
        var_pct = kpis['variacion_pct'] / 100 if kpis['variacion_pct'] != 0 else 0
        
        ws_summary.write(8, 0, "Variación Global %")
        ws_summary.write(8, 1, var_pct, pct_format) 
        ws_summary.write(8, 2, "Incremento" if kpis['variacion_pct'] > 0 else "Ahorro")

        # Análisis de Texto
        ws_summary.write(10, 0, "ANÁLISIS ESTRATÉGICO (IA)", bold_format)
        # Ajustamos el ancho para que se lea bien
        ws_summary.set_column(0, 0, 30)
        ws_summary.set_column(1, 1, 20)
        ws_summary.set_column(2, 2, 20)
        
        # Escribimos el análisis mergeando celdas
        ws_summary.merge_range('A12:E20', analysis_text, text_wrap_format)

        # === SHEET 2: DETALLE NUMÉRICO ===
        # Preparamos el DataFrame con cálculos extra
        df_export = df_detail.copy()
        df_export['Variación $'] = df_export['monto_2026'] - df_export['monto_2025']
        
        # Evitar división por cero
        df_export['Variación %'] = df_export.apply(
            lambda x: (x['monto_2026'] / x['monto_2025'] - 1) if x['monto_2025'] != 0 else 0, axis=1
        )
        
        # Renombrar columnas para el Excel
        df_export.columns = ['Categoría', 'Forecast 2025', 'Budget 2026', 'Variación Abs', 'Variación %']
        
        # Escribir DataFrame
        df_export.to_excel(writer, sheet_name='Matriz_Detalle', index=False, startrow=0)
        
        ws_detail = writer.sheets['Matriz_Detalle']
        # Formato de columnas en hoja detalle
        ws_detail.set_column('A:A', 25)
        ws_detail.set_column('B:D', 18, currency_format)
        ws_detail.set_column('E:E', 12, pct_format)

        # === SHEET 3: SUPUESTOS ===
        ws_config = workbook.add_worksheet('Supuestos')
        ws_config.write(0, 0, "INPUTS DEL USUARIO (BASE 2025)", bold_format)
        
        row = 2
        ws_config.write(1, 0, "Categoría", header_format)
        ws_config.write(1, 1, "Monto Ingresado", header_format)
        
        for key, value in input_data.items():
            ws_config.write(row, 0, key)
            ws_config.write(row, 1, value, currency_format)
            row += 1
            
        ws_config.write(row + 2, 0, "DRIVERS APLICADOS", bold_format)
        drivers = [
            ["Contractors", "+1.5%", "Eficiencia operativa"],
            ["Labor", "+4.2%", "IPC ajustado"],
            ["Fuel", "+5.0%", "Aumento producción"],
            ["Power", "+6.0%", "Tarifas eléctricas"],
            ["Maintenance", "+3.0%", "Preventivo"]
        ]
        
        r_d = row + 4
        ws_config.write(r_d-1, 0, "Driver", header_format)
        ws_config.write(r_d-1, 1, "Factor", header_format)
        ws_config.write(r_d-1, 2, "Justificación", header_format)
        
        for d in drivers:
            ws_config.write(r_d, 0, d[0])
            ws_config.write(r_d, 1, d[1])
            ws_config.write(r_d, 2, d[2])
            r_d += 1
            
        ws_config.set_column(0, 0, 20)
        ws_config.set_column(2, 2, 30)

    output.seek(0)
    return output

# --- 4. LÓGICA DE IA (EL CEREBRO) ---
def run_model():
    if not api_key:
        st.error("⚠️ Por favor ingresa una API Key válida en la configuración.")
        return None

    try:
        genai.configure(api_key=api_key)

        generation_config = {
            "temperature": 0.2,
            "response_mime_type": "application/json",
        }

        # === CORRECCIÓN PRINCIPAL ===
        # Usamos el modelo que SÍ apareció en tu lista de diagnóstico
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash", 
            generation_config=generation_config
        )

        user_data = {
            "Contractors": contractors,
            "Labor": labor,
            "Fuel": fuel,
            "Power": power,
            "Maintenance": maintenance
        }

        prompt = f"""
        Actúa como el motor financiero 'Opticast Mining'.
        
        INPUT (Gasto Real 2025): 
        {json.dumps(user_data)}
        
        INSTRUCCIÓN:
        Genera una proyección de Budget 2026 aplicando estos drivers estratégicos:
        - Contractors: +1.5% (Eficiencia operativa)
        - Labor: +4.2% (IPC ajustado)
        - Fuel: +5.0% (Aumento producción)
        - Power: +6.0% (Tarifas eléctricas)
        - Maintenance: +3.0% (Preventivo)

        OUTPUT (Formato JSON estricto):
        {{
           "analisis_estrategico": "Párrafo profesional explicando las variaciones principales. Sé conciso.",
           "datos_grafico": [
               {{"categoria": "Contractors", "monto_2025": 0, "monto_2026": 0}},
               ... (para todas las categorías)
           ],
           "kpis": {{
               "total_2025": 0,
               "total_2026": 0,
               "variacion_pct": 0
           }}
        }}
        """

        with st.spinner('🔮 OptiCast Mining está procesando con Gemini 2.5 Flash...'):
            response = model.generate_content(prompt)
            return json.loads(response.text)

    except Exception as e:
        st.error(f"Error de conexión con Google AI: {e}")
        return None

# --- 5. INTERFAZ PRINCIPAL ---
if st.button("🚀 Ejecutar Proyección 2026", type="primary", use_container_width=True):
    resultado = run_model()
    
    if resultado:
        st.divider()
        
        # A. KPIs Principales
        kpis = resultado['kpis']
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Forecast 2025", f"${kpis['total_2025']:,.0f}")
        col2.metric("Total Budget 2026", f"${kpis['total_2026']:,.0f}")
        col3.metric("Variación Global", f"{kpis['variacion_pct']}%", delta_color="inverse")
        
        st.divider()
        
        # B. Gráfico y Análisis
        c1, c2 = st.columns([2, 1])
        
        # Convertimos a DataFrame para usar los gráficos y para el Excel después
        df_grafico = pd.DataFrame(resultado["datos_grafico"])
        
        with c1:
            st.subheader("📈 Comparativa por Categoría")
            st.bar_chart(
                data=df_grafico,
                x="categoria",
                y=["monto_2025", "monto_2026"],
                color=["#A9D6E5", "#014F86"]
            )
            
        with c2:
            st.subheader("🤖 Análisis Estratégico")
            st.info(resultado["analisis_estrategico"])
            
        # C. Tabla de Datos Detallada
        with st.expander("Ver Detalle Numérico Completo"):
            st.dataframe(df_grafico, use_container_width=True)

        # --- D. BOTÓN DE DESCARGA EXCEL ---
        st.write("---")
        st.subheader("📥 Exportar Reporte")
        
        # Preparamos los inputs para el reporte
        current_inputs = {
            "Contractors": contractors,
            "Labor": labor,
            "Fuel": fuel,
            "Power": power,
            "Maintenance": maintenance
        }
        
        # Generamos el archivo Excel en memoria
        try:
            excel_data = generate_excel_report(
                kpis=resultado['kpis'],
                df_detail=df_grafico,
                analysis_text=resultado['analisis_estrategico'],
                input_data=current_inputs
            )
            
            st.download_button(
                label="Descargar Reporte Excel (.xlsx)",
                data=excel_data,
                file_name=f"OptiCast_Reporte_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary"
            )
        except Exception as e:
            st.warning(f"No se pudo generar el Excel. Verifica que 'xlsxwriter' esté en requirements.txt. Error: {e}")

else:
    st.info("👈 Configura los montos en el menú lateral y presiona 'Ejecutar Proyección' para comenzar.")