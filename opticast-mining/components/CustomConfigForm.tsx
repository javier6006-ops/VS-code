import React, { useState } from 'react';
import { Frequency, ConfigParams } from '../types';
import { Button } from './Button';
import { Settings, Calendar, TrendingUp, BarChart2 } from 'lucide-react';

interface Props {
  onProcess: (params: ConfigParams) => void;
  onCancel: () => void;
}

export const CustomConfigForm: React.FC<Props> = ({ onProcess, onCancel }) => {
  const [params, setParams] = useState<ConfigParams>({
    historicalPeriods: 12,
    forecastPeriods: 12,
    budgetPeriods: 4, // Years usually
    frequency: Frequency.ANNUAL
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onProcess(params);
  };

  const getHelperText = () => {
    if (params.frequency === Frequency.ANNUAL) {
      return "Para Budget Anual: El primer año generado será Mensual (Forecast Detallado), años subsiguientes serán Anuales.";
    }
    return "Proyección estándar basada en la frecuencia seleccionada.";
  };

  return (
    <div className="bg-opti-indigo/80 p-8 rounded-2xl border border-opti-cyan/30 max-w-2xl w-full shadow-2xl backdrop-blur-md">
      <div className="flex items-center gap-3 mb-6 border-b border-opti-violet/50 pb-4">
        <Settings className="w-8 h-8 text-opti-cyan" />
        <h2 className="text-2xl font-bold text-white">Configuración de Proyección</h2>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        
        {/* Historical Data Lookback */}
        <div className="space-y-2">
          <label className="flex items-center text-opti-cyan font-semibold text-sm">
            <Calendar className="w-4 h-4 mr-2" />
            Periodos Históricos (Lookback)
          </label>
          <input 
            type="number" 
            min="1" 
            max="60"
            value={params.historicalPeriods}
            onChange={(e) => setParams({...params, historicalPeriods: parseInt(e.target.value)})}
            className="w-full bg-opti-indigo/50 border border-opti-violet rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-opti-cyan focus:outline-none transition"
          />
          <p className="text-xs text-gray-400">Cantidad de periodos pasados a considerar para la tendencia.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Forecast Horizon */}
          <div className="space-y-2">
            <label className="flex items-center text-opti-cyan font-semibold text-sm">
              <TrendingUp className="w-4 h-4 mr-2" />
              Proyección (Forecast)
            </label>
            <input 
              type="number" 
              min="1"
              value={params.forecastPeriods}
              onChange={(e) => setParams({...params, forecastPeriods: parseInt(e.target.value)})}
              className="w-full bg-opti-indigo/50 border border-opti-violet rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-opti-cyan focus:outline-none"
            />
            <p className="text-xs text-gray-400">Periodos corto plazo.</p>
          </div>

          {/* Budget Horizon */}
          <div className="space-y-2">
            <label className="flex items-center text-opti-cyan font-semibold text-sm">
              <BarChart2 className="w-4 h-4 mr-2" />
              Proyección (Budget/LTP)
            </label>
            <input 
              type="number" 
              min="1"
              value={params.budgetPeriods}
              onChange={(e) => setParams({...params, budgetPeriods: parseInt(e.target.value)})}
              className="w-full bg-opti-indigo/50 border border-opti-violet rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-opti-cyan focus:outline-none"
            />
             <p className="text-xs text-gray-400">Periodos largo plazo.</p>
          </div>
        </div>

        {/* Frequency */}
        <div className="space-y-2">
          <label className="block text-opti-cyan font-semibold text-sm mb-2">Granularidad</label>
          <div className="grid grid-cols-3 gap-4">
            {Object.values(Frequency).map((freq) => (
              <button
                key={freq}
                type="button"
                onClick={() => setParams({...params, frequency: freq})}
                className={`py-3 px-4 rounded-lg font-medium transition-all ${
                  params.frequency === freq
                    ? 'bg-opti-cyan text-opti-indigo shadow-[0_0_15px_rgba(0,229,255,0.4)]'
                    : 'bg-opti-indigo/40 text-gray-400 border border-opti-violet hover:bg-opti-violet/40'
                }`}
              >
                {freq}
              </button>
            ))}
          </div>
          <p className="text-xs text-opti-green bg-opti-green/10 p-2 rounded border border-opti-green/20 mt-2">
            ℹ️ {getHelperText()}
          </p>
        </div>

        <div className="flex gap-4 pt-4 border-t border-opti-violet/50">
          <Button type="button" variant="outline" onClick={onCancel} className="flex-1">
            Cancelar
          </Button>
          <Button type="submit" variant="primary" className="flex-1">
            Generar Proyección
          </Button>
        </div>
      </form>
    </div>
  );
};