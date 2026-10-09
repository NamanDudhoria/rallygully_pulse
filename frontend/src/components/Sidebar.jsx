import { useState } from 'react'
import logo from "../assets/logo.svg";

function Sidebar() {
    return (
      <nav className="sidebar">
      {/* Logo/Brand Area */}
    <div className="sidebar-brand">
      <img src={logo} alt="RallyGully" className="brand-logo" />
      <span className="brand-subtitle">PULSE</span>
    </div>

      {/* Section: OVERVIEW */}
      <div className="sidebar-section">
        <h3 className="section-title">OVERVIEW</h3>
        <ul className="menu">
          <li><a href="#" className="active">Dashboard</a></li>
        </ul>
      </div>

      {/* Section: OPERATIONS */}
      <div className="sidebar-section">
        <h3 className="section-title">OPERATIONS</h3>
        <ul className="menu">
          <li><a href="#">Venues</a></li>
          <li><a href="#">Bookings</a></li>
          <li><a href="#">Availability</a></li>
          <li><a href="#">Community Games</a></li>
          <li><a href="#">Academy</a></li>
          <li><a href="#">Events</a></li>
        </ul>
      </div>

      {/* Section: INTELLIGENCE */}
      <div className="sidebar-section">
        <h3 className="section-title">INTELLIGENCE</h3>
        <ul className="menu">
          <li><a href="#">Performance</a></li>
          <li><a href="#">Customers</a></li>
          <li><a href="#">Revenue</a></li>
        </ul>
      </div>

      {/* Section: SYSTEM */}
      <div className="sidebar-section">
        <h3 className="section-title">SYSTEM</h3>
        <ul className="menu">
          <li><a href="#">Configuration</a></li>
          <li><a href="#">Audit Log</a></li>
        </ul>
      </div>
    </nav>
    );
}

export default Sidebar;
