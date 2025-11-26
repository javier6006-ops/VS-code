import React, { useState } from 'react';
import { UploadCloud, FileSpreadsheet, LayoutDashboard, Settings, ArrowLeft, Download, BarChart2 } from 'lucide-react';
import { Button } from './components/Button';
import { MetricCard } from './components/MetricCard';
import { WaterfallChart } from './components/WaterfallChart';
import { CustomConfigForm } from './components/CustomConfigForm';
import { APP_NAME, APP_SLOGAN, DEFAULT_DRIVERS } from './constants';
import { AnalysisType, ConfigParams } from './types';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { generateExcelReport } from './excelGenerator';

// --- TYPES FOR DASHBOARD STATE ---
interface DashboardData {
  kpis: {
    forecastTotal: number;
    budgetTotal: number;
    variance: number;
    variancePct: number;
  };
  waterfall: any[];
  table: any[];
  trend: any[];
}

const App: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [view, setView] = useState<'upload' | 'config' | 'dashboard'>('upload');
  const [analysisType, setAnalysisType] = useState<AnalysisType>(AnalysisType.STANDARD);
  const [isProcessing, setIsProcessing] = useState(false);
  const [currentParams, setCurrentParams] = useState<ConfigParams>();
  const [data, setData] = useState<DashboardData | null>(null);

  // Handlers
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  const startAnalysis = (type: AnalysisType) => {
    setAnalysisType(type);
    if (type === AnalysisType.CUSTOM) {
      setView('config');
    } else {
      processData();
    }
  };

  // --- CALCULATION ENGINE (Simulating Python Logic) ---
  const processData = (params?: ConfigParams) => {
    setCurrentParams(params);
    setIsProcessing(true);

    // SIMULATING THE PYTHON "MOTOR DE CÁLCULO"
    setTimeout(() => {
      // 1. Generate Base Data (Forecast 2025)
      // In a real scenario, this comes from parsing the uploaded Excel
      const baseCategories = [
        { cat: 'Labor', baseAmount: 450, driverKey: 'Labor' },
        { cat: 'Contractors', baseAmount: 120, driverKey: 'Contractors' },
        { cat: 'Fuel', baseAmount: 300, driverKey: 'Fuel' },
        { cat: 'Power', baseAmount: 200, driverKey: 'Power' },
        { cat: 'S&C', baseAmount: 100, driverKey: 'S&C' },
        { cat: 'Maintenance', baseAmount: 180, driverKey: 'Maintenance' },
        { cat: 'Otros', baseAmount: 50, driverKey: 'Otros' }
      ];

      let totalForecast = 0;
      let totalBudget = 0;
      const tableRows: any[] = [];
      const waterfallIntermediate: any[] = [];

      // 2. Calculate Budget 2026 based on Drivers
      baseCategories.forEach(item => {
        const driver = DEFAULT_DRIVERS.find(d => d.item === item.driverKey) || DEFAULT_DRIVERS.find(d => d.item === 'Otros');
        const factor = driver ? driver.factor : 1.035;
        
        const forecastVal = item.baseAmount;
        const budgetVal = forecastVal * factor;
        const delta = budgetVal - forecastVal;

        totalForecast += forecastVal;
        totalBudget += budgetVal;

        tableRows.push({
          cat: item.cat,
          item: `${item.cat} General`, // Simplified detail
          f25: forecastVal,
          b26: budgetVal,
          delta: delta,
          factor: factor
        });

        waterfallIntermediate.push({
          name: item.cat,
          val: delta,
          color: delta >= 0 ? '#FF1744' : '#00E676' // Red for cost increase, Green for savings
        });
      });

      // 3. Construct Waterfall Data (Bridge)
      const waterfallData = [];
      
      // Bar 1: Total Forecast 2025
      waterfallData.push({ 
        name: 'FCST 25', 
        value: totalForecast, 
        start: 0, 
        color: '#1A237E', 
        isTotal: true 
      });

      // Intermediate Bars (Floating)
      let currentY = totalForecast;
      waterfallIntermediate.sort((a,b) => b.val - a.val).forEach(d => {
        waterfallData.push({
          name: d.name,
          value: Math.abs(d.val),
          start: d.val >= 0 ? currentY : currentY + d.val,
          color: d.color,
          displayValue: d.val // Store signed value for tooltip
        });
        currentY += d.val;
      });

      // Bar Last: Total Budget 2026
      waterfallData.push({ 
        name: 'BUD 26', 
        value: totalBudget, 
        start: 0, 
        color: '#00E5FF', 
        isTotal: true 
      });

      // 4. Construct Trend Data (LTP)
      const trendData = [
        { year: '2025', value: Math.round(totalForecast) },
        { year: '2026', value: Math.round(totalBudget) },
        { year: '2027', value: Math.round(totalBudget * 1.03) },
        { year: '2028', value: Math.round(totalBudget * 1.03 * 1.03) },
        { year: '2029', value: Math.round(totalBudget * 1.03 * 1.03 * 1.03) },
        { year: '2030', value: Math.round(totalBudget * 1.03 * 1.03 * 1.03 * 1.03) },
      ];

      setData({
        kpis: {
          forecastTotal: totalForecast,
          budgetTotal: totalBudget,
          variance: totalBudget - totalForecast,
          variancePct: ((totalBudget / totalForecast) - 1) * 100
        },
        waterfall: waterfallData,
        table: tableRows,
        trend: trendData
      });

      setIsProcessing(false);
      setView('dashboard');
    }, 1500);
  };

  const handleExport = () => {
    if (!data) return;
    generateExcelReport(
      analysisType, 
      currentParams, 
      data.table, 
      data.trend, 
      data.waterfall
    );
  };

  const reset = () => {
    setView('upload');
    setFile(null);
    setCurrentParams(undefined);
    setData(null);
  };

  // --- VIEWS ---

  const renderUpload = () => (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <div 
        className={`w-full max-w-2xl p-12 rounded-3xl border-2 border-dashed transition-all duration-300 flex flex-col items-center ${
          file ? 'border-opti-cyan bg-opti-indigo/60' : 'border-opti-indigo/50 hover:border-opti-cyan/50 hover:bg-opti-indigo/30'
        }`}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <div className="bg-opti-indigo p-6 rounded-full mb-6 shadow-xl border border-opti-violet/30">
          {file ? <FileSpreadsheet className="w-12 h-12 text-opti-green" /> : <UploadCloud className="w-12 h-12 text-opti-cyan" />}
        </div>
        
        {file ? (
          <div className="text-center">
            <h3 className="text-xl font-bold text-white mb-2">{file.name}</h3>
            <p className="text-opti-green font-medium mb-8">Archivo listo para procesar</p>
            <div className="flex gap-4 justify-center">
              <Button onClick={() => setFile(null)} variant="outline">Cambiar Archivo</Button>
            </div>
          </div>
        ) : (
          <div className="text-center">
            <h3 className="text-xl font-bold text-white mb-2">Sube tu Data Histórica</h3>
            <p className="text-gray-400 mb-6 max-w-sm mx-auto">Arrastra tu archivo Excel/CSV aquí o haz clic para explorar. El sistema detectará automáticamente las categorías.</p>
            <label className="cursor-pointer">
              <input type="file" className="hidden" accept=".xlsx,.csv" onChange={handleFileChange} />
              <span className="bg-opti-cyan text-opti-indigo px-6 py-3 rounded-lg font-bold hover:bg-cyan-300 transition shadow-[0_0_20px_rgba(0,229,255,0.3)]">
                Seleccionar Archivo
              </span>
            </label>
          </div>
        )}
      </div>

      {file && (
        <div className="mt-10 grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-2xl animate-fade-in-up">
          <button 
            onClick={() => startAnalysis(AnalysisType.STANDARD)}
            className="group p-6 bg-opti-indigo border border-opti-violet rounded-xl hover:bg-opti-violet/20 hover:border-opti-cyan/50 transition-all text-left relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition">
              <LayoutDashboard className="w-24 h-24 text-white" />
            </div>
            <h4 className="text-lg font-bold text-white mb-2 group-hover:text-opti-cyan transition">Análisis Estándar</h4>
            <p className="text-sm text-gray-400">
              Proyección automática 2025-2030 usando drivers estratégicos predefinidos (Contractors, Labor, Fuel, etc.).
            </p>
          </button>

          <button 
            onClick={() => startAnalysis(AnalysisType.CUSTOM)}
            className="group p-6 bg-opti-indigo border border-opti-violet rounded-xl hover:bg-opti-violet/20 hover:border-opti-cyan/50 transition-all text-left relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition">
              <Settings className="w-24 h-24 text-white" />
            </div>
            <h4 className="text-lg font-bold text-white mb-2 group-hover:text-opti-cyan transition">Análisis Personalizado</h4>
            <p className="text-sm text-gray-400">
              Define periodos de forecast, budget y granularidad (Semanal/Mensual/Anual) manualmente.
            </p>
          </button>
        </div>
      )}
    </div>
  );

  const renderDashboard = () => {
    if (!data) return null;

    return (
      <div className="space-y-6 animate-fade-in">
        {/* Header Actions */}
        <div className="flex justify-between items-center mb-6">
          <div>
            <h2 className="text-2xl font-bold text-white flex items-center">
              Dashboard Gerencial 
              <span className="ml-3 text-sm font-normal text-gray-400 bg-opti-indigo px-3 py-1 rounded-full border border-opti-violet">
                {analysisType === AnalysisType.STANDARD ? 'Modo Estándar' : 'Modo Personalizado'}
              </span>
            </h2>
            <p className="text-gray-400 text-sm mt-1">Comparativo Forecast 2025 vs Budget 2026</p>
          </div>
          <div className="flex gap-3">
            <Button variant="outline" onClick={reset} icon={<ArrowLeft className="w-4 h-4"/>}>Nuevo</Button>
            <Button onClick={handleExport} variant="accent" icon={<Download className="w-4 h-4"/>}>Exportar Excel</Button>
          </div>
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <MetricCard 
            title="Forecast 2025 (Cierre)" 
            value={`$ ${Math.round(data.kpis.forecastTotal).toLocaleString()} M`}
            subtitle="Proyección basada en Data Histórica" 
            trend="neutral"
          />
          <MetricCard 
            title="Budget 2026 (Target)" 
            value={`$ ${Math.round(data.kpis.budgetTotal).toLocaleString()} M`}
            subtitle="Considerando drivers estratégicos"
            trend="up"
            trendValue={`${data.kpis.variancePct.toFixed(1)}%`}
            isHighContrast
          />
          <MetricCard 
            title="Variación Total" 
            value={`$ ${Math.round(data.kpis.variance).toLocaleString()} M`}
            subtitle="Impacto Neto Costos"
            trend={data.kpis.variance > 0 ? "up" : "down"} // Cost increase is "up" but visually red
            trendValue={data.kpis.variance > 0 ? "Incremento" : "Ahorro"}
          />
        </div>

        {/* Charts Row 1 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <WaterfallChart data={data.waterfall} />
          
          <div className="w-full h-[400px] bg-opti-indigo/30 p-4 rounded-xl border border-opti-violet/30 flex flex-col">
             <h4 className="text-white font-semibold mb-4 flex items-center">
              <span className="w-2 h-6 bg-opti-violet mr-2 rounded-sm"></span>
              Tendencia Evolutiva (LTP 2025-2030)
            </h4>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data.trend} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00E5FF" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#00E5FF" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="year" stroke="#A5B4FC" tickLine={false} />
                <YAxis stroke="#A5B4FC" tickLine={false} tickFormatter={(v) => `$${v}`} />
                <CartesianGrid strokeDasharray="3 3" stroke="#4A148C" opacity={0.3} vertical={false} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1A237E', borderColor: '#00E5FF', color: '#fff' }}
                  formatter={(value: number) => [`$${value} M`, 'Monto Total']}
                />
                <Area type="monotone" dataKey="value" stroke="#00E5FF" strokeWidth={3} fillOpacity={1} fill="url(#colorValue)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Data Table */}
        <div className="bg-opti-silver rounded-xl overflow-hidden shadow-lg border border-opti-violet/20">
          <div className="bg-opti-violet px-6 py-4 border-b border-opti-indigo flex justify-between items-center">
            <h3 className="text-white font-bold">Detalle por Categoría de Gasto</h3>
            <span className="text-xs text-white/70 uppercase tracking-widest">Calculado</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-200 text-opti-indigo font-bold uppercase text-xs">
                <tr>
                  <th className="px-6 py-3">Categoría Driver</th>
                  <th className="px-6 py-3">Ítem Detalle</th>
                  <th className="px-6 py-3 text-right">Forecast 2025</th>
                  <th className="px-6 py-3 text-right">Budget 2026</th>
                  <th className="px-6 py-3 text-right">Factor</th>
                  <th className="px-6 py-3 text-right">Var %</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-300">
                {data.table.map((row: any, idx: number) => {
                  const varPct = ((row.b26 / row.f25) - 1) * 100;
                  return (
                    <tr key={idx} className="hover:bg-white transition-colors text-gray-800">
                      <td className="px-6 py-3 font-semibold text-opti-indigo">{row.cat}</td>
                      <td className="px-6 py-3">{row.item}</td>
                      <td className="px-6 py-3 text-right">${row.f25.toFixed(1)} M</td>
                      <td className="px-6 py-3 text-right font-bold">${row.b26.toFixed(1)} M</td>
                      <td className="px-6 py-3 text-right text-gray-500 font-mono">{row.factor.toFixed(3)}x</td>
                      <td className={`px-6 py-3 text-right font-bold ${varPct > 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {varPct.toFixed(1)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#1A237E] bg-gradient-to-br from-[#1A237E] to-[#0D1142] text-white selection:bg-opti-cyan selection:text-opti-indigo">
      {/* Navbar */}
      <nav className="border-b border-opti-violet/40 bg-[#1A237E]/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-20">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-gradient-to-tr from-opti-indigo to-opti-cyan rounded-lg flex items-center justify-center shadow-[0_0_15px_rgba(0,229,255,0.5)]">
                <BarChart2 className="text-white w-6 h-6" />
              </div>
              <div>
                <h1 className="text-2xl font-bold tracking-tight text-white">{APP_NAME}</h1>
                <p className="text-xs text-opti-cyan font-medium tracking-widest uppercase">{APP_SLOGAN}</p>
              </div>
            </div>
            <div className="hidden md:flex items-center space-x-8">
               <a href="#" className="text-gray-300 hover:text-opti-cyan transition text-sm font-medium">Historial</a>
               <a href="#" className="text-gray-300 hover:text-opti-cyan transition text-sm font-medium">Drivers</a>
               <a href="#" className="text-gray-300 hover:text-opti-cyan transition text-sm font-medium">Configuración</a>
               <div className="w-px h-6 bg-opti-violet"></div>
               <div className="flex items-center gap-2">
                 <div className="w-8 h-8 rounded-full bg-opti-violet flex items-center justify-center text-xs font-bold border border-opti-cyan">JD</div>
               </div>
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10">
        {isProcessing ? (
          <div className="flex flex-col items-center justify-center min-h-[60vh] animate-pulse">
            <div className="w-16 h-16 border-4 border-opti-cyan border-t-transparent rounded-full animate-spin mb-4"></div>
            <h3 className="text-2xl font-bold text-white">Procesando Modelo Financiero...</h3>
            <p className="text-opti-cyan mt-2">Aplicando Drivers Estratégicos y Generando Budget</p>
          </div>
        ) : (
          <>
            {view === 'upload' && renderUpload()}
            {view === 'config' && (
              <div className="flex justify-center items-center min-h-[60vh] animate-fade-in">
                <CustomConfigForm 
                  onProcess={(params) => processData(params)} 
                  onCancel={() => setView('upload')}
                />
              </div>
            )}
            {view === 'dashboard' && renderDashboard()}
          </>
        )}
      </main>
    </div>
  );
};

export default App;