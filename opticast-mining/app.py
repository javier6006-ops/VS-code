import streamlit as st
import google.generativeai as genai
import json
import pandas as pd

# --- 1. CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="OptiCast Mining",
    page_icon="💎",
    layout="wide"
)

# --- 2. TÍTULO Y DESCRIPCIÓN ---
st.title("💎 OptiCast Mining: Proyector Financiero IA")
st.markdown("""
Esta aplicación utiliza **Google Gemini Pro** para proyectar presupuestos mineros basándose en datos históricos 
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

# --- 4. LÓGICA DE IA (EL CEREBRO) ---
def run_model():
    if not api_key:
        st.error("⚠️ Por favor ingresa una API Key válida en la configuración.")
        return None

    try:
        genai.configure(api_key=api_key)

        # Configuración para forzar respuesta JSON limpia
        generation_config = {
            "temperature": 0.2, # Baja temperatura para precisión numérica
            "response_mime_type": "application/json",
        }

        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config
        )

        # Empaquetar datos del usuario
        user_data = {
            "Contractors": contractors,
            "Labor": labor,
            "Fuel": fuel,
            "Power": power,
            "Maintenance": maintenance
        }

        # El Prompt de Ingeniería
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
           "analisis_estrategico": "Párrafo profesional explicando las variaciones principales.",
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

        with st.spinner('🔮 OptiCast Mining está procesando los modelos financieros...'):
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
        
        with c1:
            st.subheader("📈 Comparativa por Categoría")
            # Convertimos a DataFrame para usar los gráficos nativos de Streamlit
            df_grafico = pd.DataFrame(resultado["datos_grafico"])
            st.bar_chart(
                data=df_grafico,
                x="categoria",
                y=["monto_2025", "monto_2026"],
                color=["#A9D6E5", "#014F86"] # Colores corporativos (Celeste/Azul)
            )
            
        with c2:
            st.subheader("🤖 Análisis Estratégico")
            st.info(resultado["analisis_estrategico"])
            
        # C. Tabla de Datos Detallada
        with st.expander("Ver Detalle Numérico Completo"):
            st.dataframe(df_grafico, use_container_width=True)

else:
    st.info("👈 Configura los montos en el menú lateral y presiona 'Ejecutar Proyección' para comenzar.")