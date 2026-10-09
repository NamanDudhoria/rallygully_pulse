import { useState } from 'react'
import './App.css'
import Sidebar from "./components/Sidebar";
import Dashboard from "./components/Dashboard";

function Pulse() {
  return (
    <div className="app-layout">
      <Sidebar />
      <main>
        < Dashboard />
      </main>
    </div>
  );
}

export default Pulse;
