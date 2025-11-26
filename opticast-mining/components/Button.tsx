import React from 'react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'accent' | 'outline';
  icon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({ 
  children, 
  variant = 'primary', 
  icon, 
  className = '', 
  ...props 
}) => {
  const baseStyles = "flex items-center justify-center px-4 py-2 rounded-lg font-semibold transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-opti-indigo disabled:opacity-50 disabled:cursor-not-allowed";
  
  const variants = {
    primary: "bg-opti-cyan text-opti-indigo hover:bg-cyan-300 focus:ring-opti-cyan",
    secondary: "bg-opti-violet text-white hover:bg-purple-800 focus:ring-opti-violet",
    accent: "bg-opti-green text-opti-indigo hover:bg-green-400 focus:ring-opti-green",
    outline: "border-2 border-opti-cyan text-opti-cyan hover:bg-opti-cyan/10 focus:ring-opti-cyan"
  };

  return (
    <button 
      className={`${baseStyles} ${variants[variant]} ${className}`}
      {...props}
    >
      {icon && <span className="mr-2">{icon}</span>}
      {children}
    </button>
  );
};