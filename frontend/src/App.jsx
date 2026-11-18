import React, { useEffect, useState } from "react";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import CopilotChat from "./components/CopilotChat";
import ComplaintForm from "./components/ComplaintForm";
import QADashboard from "./QADashboard";
import OrganizationDashboard from "./OrganizationDashboard";
import ManufacturerDashboard from "./ManufacturerDashboard";
import CustomerDashboard from "./CustomerDashboard";
import ChemistDashboard from "./ChemistDashboard";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const App = () => (
  <BrowserRouter>
    <AppContent />
  </BrowserRouter>
);

const AppContent = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const showWorkspace = location.pathname === "/complaints";
  const showQADashboard = location.pathname === "/qa-dashboard";
  const showOrganizationDashboard = location.pathname === "/organization-dashboard";
  const showManufacturerDashboard = location.pathname === "/manufacturer-dashboard";
  const showCustomerDashboard = location.pathname === "/customer-dashboard";
  const showChemistDashboard = location.pathname === "/chemist-dashboard";
  const [theme, setTheme] = useState(() => localStorage.getItem("qms-theme") || "dark");
  useEffect(() => { localStorage.setItem("qms-theme", theme); }, [theme]);
  const [authModal, setAuthModal] = useState(null);
  const [authNotice, setAuthNotice] = useState("");
  const [isChemist, setIsChemist] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  if (showQADashboard) {
    return <QADashboard />;
  }

  if (showOrganizationDashboard) {
    return <OrganizationDashboard />;
  }

  if (showManufacturerDashboard) {
    return <ManufacturerDashboard />;
  }

  if (showCustomerDashboard) {
    return <CustomerDashboard />;
  }

  if (showChemistDashboard) {
    return <ChemistDashboard />;
  }

  if (showWorkspace) {
    return (
      <>
        <style>{`
          .qms-layout[data-theme="dark"] {
            --c-080c19: #080c19;
            --c-0c1222: #0c1222;
            --c-0c1427: #0c1427;
            --c-0d1323: #0d1323;
            --c-0d1726: #0d1726;
            --c-101627: #101627;
            --c-10172a: #10172a;
            --c-111d2e: #111d2e;
            --c-111e30: #111e30;
            --c-131a2e: #131a2e;
            --c-151a29: #151a29;
            --c-153b35: #153b35;
            --c-171e32: #171e32;
            --c-172438: #172438;
            --c-17243a: #17243a;
            --c-172b3b: #172b3b;
            --c-173b38: #173b38;
            --c-183b38: #183b38;
            --c-19213a: #19213a;
            --c-1a2134: #1a2134;
            --c-1b2440: #1b2440;
            --c-1c253b: #1c253b;
            --c-202a42: #202a42;
            --c-222b40: #222b40;
            --c-26364c: #26364c;
            --c-286b5d: #286b5d;
            --c-29234a: #29234a;
            --c-29334b: #29334b;
            --c-2b3b52: #2b3b52;
            --c-302b22: #302b22;
            --c-30364b: #30364b;
            --c-303951: #303951;
            --c-303a53: #303a53;
            --c-303a57: #303a57;
            --c-30415a: #30415a;
            --c-33364c: #33364c;
            --c-34314f: #34314f;
            --c-34445f: #34445f;
            --c-34465f: #34465f;
            --c-34756a: #34756a;
            --c-35405b: #35405b;
            --c-35415e: #35415e;
            --c-38bdf8: #38bdf8;
            --c-39252e: #39252e;
            --c-39445f: #39445f;
            --c-394664: #394664;
            --c-3a3020: #3a3020;
            --c-3a4b65: #3a4b65;
            --c-42d5a1: #42d5a1;
            --c-46516c: #46516c;
            --c-4ed6a7: #4ed6a7;
            --c-50d8a9: #50d8a9;
            --c-52617a: #52617a;
            --c-55d6ac: #55d6ac;
            --c-57d2e8: #57d2e8;
            --c-5bcde2: #5bcde2;
            --c-62558f: #62558f;
            --c-65b69b: #65b69b;
            --c-6655a0: #6655a0;
            --c-6741e8: #6741e8;
            --c-6744e9: #6744e9;
            --c-7467a9: #7467a9;
            --c-7568a8: #7568a8;
            --c-76643f: #76643f;
            --c-7968b8: #7968b8;
            --c-80505d: #80505d;
            --c-8060ff: #8060ff;
            --c-80643a: #80643a;
            --c-8493b6: #8493b6;
            --c-8591ad: #8591ad;
            --c-8876c8: #8876c8;
            --c-8965ff: #8965ff;
            --c-8994b0: #8994b0;
            --c-919bb4: #919bb4;
            --c-9268ff: #9268ff;
            --c-929db9: #929db9;
            --c-96a5ba: #96a5ba;
            --c-9875ff: #9875ff;
            --c-9971ff: #9971ff;
            --c-9adbc8: #9adbc8;
            --c-9eacc0: #9eacc0;
            --c-a78bfa: #a78bfa;
            --c-a99ade: #a99ade;
            --c-aab4cd: #aab4cd;
            --c-aab4d0: #aab4d0;
            --c-aab7c9: #aab7c9;
            --c-aeb8d0: #aeb8d0;
            --c-b2bfd0: #b2bfd0;
            --c-b69aff: #b69aff;
            --c-b69cff: #b69cff;
            --c-b6a9e4: #b6a9e4;
            --c-b7addc: #b7addc;
            --c-b7c3d3: #b7c3d3;
            --c-b8a8e8: #b8a8e8;
            --c-bca6ff: #bca6ff;
            --c-c2c9db: #c2c9db;
            --c-c5c9dd: #c5c9dd;
            --c-d0bdff: #d0bdff;
            --c-d5c8ff: #d5c8ff;
            --c-d6deea: #d6deea;
            --c-d9d2f1: #d9d2f1;
            --c-d9d7ee: #d9d7ee;
            --c-dce1f2: #dce1f2;
            --c-dce3ed: #dce3ed;
            --c-e2e2f0: #e2e2f0;
            --c-e5a83d: #e5a83d;
            --c-e5ebf3: #e5ebf3;
            --c-e8e2ff: #e8e2ff;
            --c-e9bd75: #e9bd75;
            --c-eef2f7: #eef2f7;
            --c-ef5350: #ef5350;
            --c-f0b45c: #f0b45c;
            --c-f2a3ad: #f2a3ad;
            --c-f2f5fa: #f2f5fa;
            --c-f5f2ff: #f5f2ff;
            --c-f5f5ff: #f5f5ff;
            --c-ff8295: #ff8295;
            --c-fff: #fff;
          }
          .qms-layout[data-theme="light"] {
            --c-080c19: #edf2f8;
            --c-0c1222: #edf2f8;
            --c-0c1427: #edf2f8;
            --c-0d1323: #edf2f8;
            --c-0d1726: #f3f6fb;
            --c-101627: #edf2f8;
            --c-10172a: #edf2f8;
            --c-111d2e: #ffffff;
            --c-111e30: #f4f7fb;
            --c-131a2e: #edf2f8;
            --c-151a29: #edf2f8;
            --c-153b35: #e8f5f0;
            --c-171e32: #edf2f8;
            --c-172438: #ffffff;
            --c-17243a: #edf2f8;
            --c-172b3b: #eaf2f6;
            --c-173b38: #e8f5f0;
            --c-183b38: #dff2ea;
            --c-19213a: #edf2f8;
            --c-1a2134: #edf2f8;
            --c-1b2440: #e9edf8;
            --c-1c253b: #edf2f8;
            --c-202a42: #edf2f8;
            --c-222b40: #edf2f8;
            --c-26364c: #e8edf5;
            --c-286b5d: #9bcfc2;
            --c-29234a: #f0ebfa;
            --c-29334b: #dfe7f0;
            --c-2b3b52: #d8e1ec;
            --c-302b22: #edf2f8;
            --c-30364b: #dfe7f0;
            --c-303951: #dfe7f0;
            --c-303a53: #dfe7f0;
            --c-303a57: #dfe7f0;
            --c-30415a: #d9e2ed;
            --c-33364c: #dfe7f0;
            --c-34314f: #dfe7f0;
            --c-34445f: #dfe7f0;
            --c-34465f: #d4deea;
            --c-34756a: #a8d9cb;
            --c-35405b: #dfe7f0;
            --c-35415e: #dfe7f0;
            --c-38bdf8: #53657a;
            --c-39252e: #fbecee;
            --c-39445f: #dfe7f0;
            --c-394664: #dfe7f0;
            --c-3a3020: #fbf3e5;
            --c-3a4b65: #c6d2e0;
            --c-42d5a1: #53657a;
            --c-46516c: #dfe7f0;
            --c-4ed6a7: #53657a;
            --c-50d8a9: #53657a;
            --c-52617a: #bdc9d8;
            --c-55d6ac: #27866d;
            --c-57d2e8: #53657a;
            --c-5bcde2: #53657a;
            --c-62558f: #dfe7f0;
            --c-65b69b: #27866d;
            --c-6655a0: #c5b8e2;
            --c-6741e8: #dfe7f0;
            --c-6744e9: #dfe7f0;
            --c-7467a9: #7657ad;
            --c-7568a8: #718198;
            --c-76643f: #718198;
            --c-7968b8: #7657ad;
            --c-80505d: #e4b8bf;
            --c-8060ff: #718198;
            --c-80643a: #e6c995;
            --c-8493b6: #6b7c91;
            --c-8591ad: #718198;
            --c-8876c8: #718198;
            --c-8965ff: #718198;
            --c-8994b0: #718198;
            --c-919bb4: #53657a;
            --c-9268ff: #718198;
            --c-929db9: #66778c;
            --c-96a5ba: #718198;
            --c-9875ff: #718198;
            --c-9971ff: #718198;
            --c-9adbc8: #286c5a;
            --c-9eacc0: #66778c;
            --c-a78bfa: #53657a;
            --c-a99ade: #7757ad;
            --c-aab4cd: #52647a;
            --c-aab4d0: #53657a;
            --c-aab7c9: #5b6b80;
            --c-aeb8d0: #53657a;
            --c-b2bfd0: #52647a;
            --c-b69aff: #53657a;
            --c-b69cff: #53657a;
            --c-b6a9e4: #53657a;
            --c-b7addc: #53657a;
            --c-b7c3d3: #4b5d72;
            --c-b8a8e8: #7054a3;
            --c-bca6ff: #53657a;
            --c-c2c9db: #26364a;
            --c-c5c9dd: #26364a;
            --c-d0bdff: #53657a;
            --c-d5c8ff: #26364a;
            --c-d6deea: #26364a;
            --c-d9d2f1: #58417e;
            --c-d9d7ee: #35445a;
            --c-dce1f2: #26364a;
            --c-dce3ed: #26364a;
            --c-e2e2f0: #26364a;
            --c-e5a83d: #53657a;
            --c-e5ebf3: #26364a;
            --c-e8e2ff: #f4efff;
            --c-e9bd75: #53657a;
            --c-eef2f7: #243449;
            --c-ef5350: #c53e4d;
            --c-f0b45c: #98621a;
            --c-f2a3ad: #a83245;
            --c-f2f5fa: #1d2b3d;
            --c-f5f2ff: #2e2450;
            --c-f5f5ff: #1d2b3d;
            --c-ff8295: #b8324b;
            --c-fff: #ffffff;
          }

          .qms-app[data-theme="light"] {

            --c-080c19: #edf2f8;
            --c-0c1222: #edf2f8;
            --c-0c1427: #edf2f8;
            --c-0d1323: #edf2f8;
            --c-0d1726: #f3f6fb;
            --c-101627: #edf2f8;
            --c-10172a: #edf2f8;
            --c-111d2e: #ffffff;
            --c-111e30: #f4f7fb;
            --c-131a2e: #edf2f8;
            --c-151a29: #edf2f8;
            --c-153b35: #e8f5f0;
            --c-171e32: #edf2f8;
            --c-172438: #ffffff;
            --c-17243a: #edf2f8;
            --c-172b3b: #eaf2f6;
            --c-173b38: #e8f5f0;
            --c-183b38: #dff2ea;
            --c-19213a: #edf2f8;
            --c-1a2134: #edf2f8;
            --c-1b2440: #e9edf8;
            --c-1c253b: #edf2f8;
            --c-202a42: #edf2f8;
            --c-222b40: #edf2f8;
            --c-26364c: #e8edf5;
            --c-286b5d: #9bcfc2;
            --c-29234a: #f0ebfa;
            --c-29334b: #dfe7f0;
            --c-2b3b52: #d8e1ec;
            --c-302b22: #edf2f8;
            --c-30364b: #dfe7f0;
            --c-303951: #dfe7f0;
            --c-303a53: #dfe7f0;
            --c-303a57: #dfe7f0;
            --c-30415a: #d9e2ed;
            --c-33364c: #dfe7f0;
            --c-34314f: #dfe7f0;
            --c-34445f: #dfe7f0;
            --c-34465f: #d4deea;
            --c-34756a: #a8d9cb;
            --c-35405b: #dfe7f0;
            --c-35415e: #dfe7f0;
            --c-38bdf8: #53657a;
            --c-39252e: #fbecee;
            --c-39445f: #dfe7f0;
            --c-394664: #dfe7f0;
            --c-3a3020: #fbf3e5;
            --c-3a4b65: #c6d2e0;
            --c-42d5a1: #53657a;
            --c-46516c: #dfe7f0;
            --c-4ed6a7: #53657a;
            --c-50d8a9: #53657a;
            --c-52617a: #bdc9d8;
            --c-55d6ac: #27866d;
            --c-57d2e8: #53657a;
            --c-5bcde2: #53657a;
            --c-62558f: #7657ad;
            --c-65b69b: #27866d;
            --c-6655a0: #c5b8e2;
            --c-6741e8: #7657c5;
            --c-6744e9: #8060d6;
            --c-7467a9: #7657ad;
            --c-7568a8: #718198;
            --c-76643f: #718198;
            --c-7968b8: #7657ad;
            --c-80505d: #e4b8bf;
            --c-8060ff: #7657c5;
            --c-80643a: #e6c995;
            --c-8493b6: #6b7c91;
            --c-8591ad: #718198;
            --c-8876c8: #718198;
            --c-8965ff: #8060d6;
            --c-8994b0: #718198;
            --c-919bb4: #53657a;
            --c-9268ff: #8060d6;
            --c-929db9: #66778c;
            --c-96a5ba: #718198;
            --c-9875ff: #7657c5;
            --c-9971ff: #8060d6;
            --c-9adbc8: #286c5a;
            --c-9eacc0: #66778c;
            --c-a78bfa: #7657c5;
            --c-a99ade: #7757ad;
            --c-aab4cd: #52647a;
            --c-aab4d0: #53657a;
            --c-aab7c9: #5b6b80;
            --c-aeb8d0: #53657a;
            --c-b2bfd0: #52647a;
            --c-b69aff: #7657c5;
            --c-b69cff: #7657c5;
            --c-b6a9e4: #53657a;
            --c-b7addc: #53657a;
            --c-b7c3d3: #4b5d72;
            --c-b8a8e8: #7054a3;
            --c-bca6ff: #7657c5;
            --c-c2c9db: #26364a;
            --c-c5c9dd: #26364a;
            --c-d0bdff: #7657c5;
            --c-d5c8ff: #26364a;
            --c-d6deea: #26364a;
            --c-d9d2f1: #58417e;
            --c-d9d7ee: #35445a;
            --c-dce1f2: #26364a;
            --c-dce3ed: #26364a;
            --c-e2e2f0: #26364a;
            --c-e5a83d: #53657a;
            --c-e5ebf3: #26364a;
            --c-e8e2ff: #f4efff;
            --c-e9bd75: #53657a;
            --c-eef2f7: #243449;
            --c-ef5350: #c53e4d;
            --c-f0b45c: #98621a;
            --c-f2a3ad: #a83245;
            --c-f2f5fa: #1d2b3d;
            --c-f5f2ff: #2e2450;
            --c-f5f5ff: #1d2b3d;
            --c-ff8295: #b8324b;
            --c-fff: #ffffff;
          }
          .qms-app[data-theme="light"] .landing {
            color:#243449;
            background:radial-gradient(ellipse at 75% 20%, rgba(124,87,197,.10), transparent 42%), #f4f6fb;
          }
          .qms-app[data-theme="light"] .navbar { border-bottom-color:#dfe5ef; }
          .qms-app[data-theme="light"] .logo { color:#1d2b3d; }
          .qms-app[data-theme="light"] .nav-links a { color:#52647a; }
          .qms-app[data-theme="light"] .nav-links a:hover { color:#7657ad; }
          .qms-app[data-theme="light"] .sign-btn { color:#34445a; border-color:#cbd5e1; background:#fff; }
          .qms-app[data-theme="light"] .hero h1,
          .qms-app[data-theme="light"] .section h2,
          .qms-app[data-theme="light"] .landing h2 { color:#1d2b3d; }
          .qms-app[data-theme="light"] .hero-description,
          .qms-app[data-theme="light"] .section-description { color:#596b80; }
          .qms-app[data-theme="light"] .secondary-btn { color:#34445a; background:#fff; border-color:#d5deea; }
          .qms-app[data-theme="light"] .dashboard { background:linear-gradient(145deg,#fff,#f2f5fb); border-color:#d7deeb; box-shadow:0 22px 60px rgba(37,52,73,.12); }
          .qms-app[data-theme="light"] .dash-top,
          .qms-app[data-theme="light"] .sidebar { color:#52647a; border-color:#dfe5ef; }
          .qms-app[data-theme="light"] .sidebar { background:#f4f6fb; }
          .qms-app[data-theme="light"] .stat-card,
          .qms-app[data-theme="light"] .process-card,
          .qms-app[data-theme="light"] .risk-card,
          .qms-app[data-theme="light"] .feature-card { background:#fff; border-color:#dfe5ef; color:#243449; }
          .qms-app[data-theme="light"] .section { border-top-color:#e0e6ef; }
          .qms-app[data-theme="light"] .floating-card { background:#fff; border-color:#d8d0eb; color:#243449; }
          .qms-app[data-theme="light"] .footer { color:#596b80; border-top:1px solid #dfe5ef; }
          .qms-app[data-theme="light"] .footer a { color:#7657ad; }
          .qms-app[data-theme="light"] .auth-overlay { background:rgba(31,41,55,.36); }
          .qms-app[data-theme="light"] .auth-modal { color:#243449; background:linear-gradient(155deg,#fff 0%,#f7f5fc 100%); border-color:#d8cdef; box-shadow:0 24px 70px rgba(31,41,55,.20); }
          .qms-app[data-theme="light"] .auth-field { color:#34445a; }
          .qms-app[data-theme="light"] .auth-field input { color:#243449; background:#fff; border-color:#d4dce8; }
          .qms-app[data-theme="light"] .auth-close { color:#52647a; background:#f1eff8; border-color:#ddd5ed; }
          .qms-theme-toggle { display:inline-flex; align-items:center; gap:7px; padding:7px 11px; border:1px solid var(--c-30415a); border-radius:8px; background:var(--c-172438); color:var(--c-f2f5fa); font:600 12px Inter,Arial,sans-serif; cursor:pointer; white-space:nowrap; }
          .qms-theme-toggle:hover { border-color:var(--c-7968b8); }
          .landing-theme-toggle { display:inline-flex; align-items:center; gap:8px; padding:11px 14px; border-radius:11px; border:1px solid rgba(167,139,250,.35); background:rgba(128,85,245,.10); color:var(--c-f5f5ff); font:600 13px Inter,Arial,sans-serif; cursor:pointer; white-space:nowrap; }
          .qms-app[data-theme="light"] .landing-theme-toggle { color:#6746a5; background:#f0eafb; border-color:#d8c9f1; }
          .qms-theme-toggle { display:inline-flex; align-items:center; gap:7px; padding:7px 11px; border:1px solid var(--c-30415a); border-radius:8px; background:var(--c-172438); color:var(--c-f2f5fa); font:600 12px Inter,Arial,sans-serif; cursor:pointer; white-space:nowrap; }
          .qms-theme-toggle:hover { border-color:var(--c-7968b8); }
          * { box-sizing: border-box; }
          html, body, #root { width:100%; height:100%; margin:0; overflow:hidden; }\n          body { font-family: Inter, Arial, sans-serif; background: var(--c-0d1726); color: var(--c-f2f5fa); }
          .qms-layout { width:100%; height:100vh; min-height:0; display:flex; overflow:hidden; background:var(--c-0d1726); }
          .qms-sidebar { width:184px; flex:0 0 184px; height:100%; min-height:0; display:flex; flex-direction:column; padding:20px 13px; background:var(--c-111d2e); border-right:1px solid var(--c-2b3b52); }
          .qms-brand { display:flex; align-items:center; gap:12px; padding: 4px 8px 28px; border-bottom:1px solid var(--c-2b3b52); }
          .qms-brand-mark { width:40px; height:40px; display:grid; place-items:center; border-radius:12px; color:var(--c-fff); font-size:20px; background:linear-gradient(145deg,var(--c-8876c8),var(--c-62558f)); }
          .qms-brand strong { display:block; font-size:17px; letter-spacing:-.3px; }
          .qms-brand small { display:block; margin-top:4px; color:var(--c-9eacc0); font-size:11px; }
          .qms-nav { display:grid; gap:8px; margin-top:28px; }
          .qms-nav button { width:100%; display:flex; align-items:center; gap:12px; padding:13px 12px; border:1px solid transparent; border-radius:9px; background:transparent; color:var(--c-b7c3d3); text-align:left; font-size:13px; font-weight:600; cursor:pointer; }
          .qms-nav button.active { color:var(--c-f5f2ff); background:rgba(121,104,184,.20); border-color:rgba(169,154,222,.28); }
          .qms-nav button:hover { background:rgba(121,104,184,.12); color:var(--c-fff); }
          .qms-sidebar-bottom { margin-top:auto; padding:14px 10px 0; border-top:1px solid var(--c-2b3b52); color:var(--c-9eacc0); font-size:11px; line-height:1.6; }
          .qms-content { min-width:0; min-height:0; height:100%; flex:1; display:flex; flex-direction:column; overflow:hidden; }
          .qms-topbar { height:58px; flex:0 0 58px; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:0 30px; background:var(--c-111d2e); border-bottom:1px solid var(--c-2b3b52); }
          .qms-topbar-label { color:var(--c-aab7c9); font-size:13px; }
          .qms-profile { display:flex; align-items:center; gap:11px; color:var(--c-eef2f7); font-size:12px; font-weight:600; }
          .qms-avatar { width:34px; height:34px; display:grid; place-items:center; border-radius:50%; background:var(--c-34445f); color:var(--c-e8e2ff); border:1px solid var(--c-7568a8); }
          .qms-page-heading { flex:0 0 auto; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:15px 24px 13px; }
          .qms-page-heading h1 { margin:0 0 5px; color:var(--c-f2f5fa); font-size:25px; letter-spacing:-.5px; }
          .qms-page-heading p { margin:0; color:var(--c-9eacc0); font-size:13px; }
          .qms-page-tag { padding:8px 13px; border:1px solid var(--c-76643f); border-radius:8px; color:var(--c-e9bd75); background:var(--c-302b22); font-size:12px; font-weight:700; white-space:nowrap; }
          .app-shell { height:0; min-height:0; flex:1 1 auto; display:flex; padding:0 20px 18px; gap:14px; overflow:hidden; }
          .chat-panel, .form-panel { display:flex; flex-direction:column; min-height:0; background:var(--c-172438); border:1px solid var(--c-30415a); border-radius:13px; overflow:hidden; box-shadow:0 8px 24px rgba(0,0,0,.14); }
          .chat-panel { width:300px; flex:0 0 300px; min-height:0; height:100%; }
          .chat-panel > div { flex:1 1 auto; min-height:0; height:100%; overflow:hidden; }
          .chat-panel > div > div:nth-child(3) { flex:1 1 auto; min-height:0; overflow-y:auto; overscroll-behavior:contain; }
          .chat-panel > div > div:nth-child(4) { flex:0 0 auto; min-width:0; width:100%; }
          .chat-panel form { width:100%; min-width:0; }
          .chat-panel textarea { display:block; width:100%; min-width:0; max-width:100%; }
          .copilot-chat-root { flex:1 1 auto; min-height:0; min-width:0; overflow:hidden; }
          .copilot-upload-area { flex:0 0 auto; }
          .copilot-messages { flex:1 1 0 !important; min-height:0 !important; overflow-y:auto !important; overflow-x:hidden !important; scrollbar-gutter:stable; }
          .copilot-composer { flex:0 0 auto; width:100%; min-width:0; }
          .copilot-composer textarea { width:100%; max-width:100%; min-height:48px; max-height:80px; overflow:hidden !important; scrollbar-width:none; }
          .copilot-composer textarea::-webkit-scrollbar { display:none; }
          .copilot-composer button { z-index:2; }
          .chat-panel { min-width:0; }
          .form-panel { flex:1; min-width:0; min-height:0; height:100%; overflow-y:auto; overflow-x:hidden; }
          .back-button { position:fixed; left:16px; bottom:14px; z-index:20; opacity:0; width:1px; height:1px; overflow:hidden; }
          @media(max-width:1100px) { .qms-sidebar { width:168px; flex-basis:168px; } .chat-panel { width:280px; flex-basis:280px; } .app-shell { padding-left:14px; padding-right:14px; } }
          @media(max-width:800px) { html, body, #root { height:auto; min-height:100%; overflow:auto; } .qms-layout { height:auto; min-height:100vh; overflow:visible; } .qms-sidebar { width:64px; flex-basis:64px; min-height:100vh; padding:16px 7px; } .qms-brand { justify-content:center; padding:0 0 16px; } .qms-brand > div:last-child, .qms-nav button span:last-child, .qms-sidebar-bottom { display:none; } .qms-nav button { justify-content:center; padding:13px 5px; font-size:18px; } .qms-topbar { padding:0 14px; } .qms-page-heading { padding:15px 14px; } .app-shell { height:auto; min-height:0; flex-direction:column; overflow:visible; padding:0 12px 14px; } .chat-panel { width:100%; flex:0 0 auto; height:440px; min-height:0; } .chat-panel > div { min-height:0; } .form-panel { min-height:650px; height:auto; overflow:visible; } .qms-content { height:auto; min-height:100vh; overflow:visible; } }
          @media(max-width:480px) { .qms-sidebar { display:none; } .qms-page-heading h1 { font-size:20px; } .qms-topbar-label { font-size:11px; } .app-shell { padding:0 10px 14px; } }
        
          /* Refined enterprise palette: navy + purple */
          .qms-layout[data-theme="dark"] {
            --c-080c19: #080B18;
            --c-0c1222: #0C1224;
            --c-0c1427: #0D1428;
            --c-0d1323: #0C1224;
            --c-0d1726: #10182E;
            --c-101627: #10182E;
            --c-10172a: #111A32;
            --c-111d2e: #111B34;
            --c-111e30: #141D38;
            --c-131a2e: #121A32;
            --c-151a29: #151E39;
            --c-171e32: #17213D;
            --c-172438: #17213D;
            --c-17243a: #192443;
            --c-172b3b: #1B2948;
            --c-19213a: #1B2544;
            --c-1a2134: #1B2544;
            --c-1b2440: #202B4D;
            --c-1c253b: #202B4D;
            --c-202a42: #263252;
            --c-222b40: #293555;
            --c-26364c: #2C395B;
            --c-29334b: #303B60;
            --c-2b3b52: #354166;
            --c-30364b: #39436B;
            --c-303951: #39436B;
            --c-303a53: #3B456D;
            --c-303a57: #3B456D;
            --c-30415a: #414B75;
            --c-33364c: #414B75;
            --c-34445f: #46517A;
            --c-34465f: #46517A;
            --c-3a4b65: #4A5680;
            --c-7467a9: #9B83D5;
            --c-7568a8: #8D75C9;
            --c-7968b8: #9A7BE6;
            --c-8060ff: #8B68D8;
            --c-8965ff: #9472E2;
            --c-9268ff: #9B78E8;
            --c-9875ff: #A58AF0;
            --c-9971ff: #8D69D9;
            --c-a78bfa: #B6A0F3;
            --c-a99ade: #BBA7F2;
            --c-b69aff: #C2B0F5;
            --c-b69cff: #C2B0F5;
            --c-b6a9e4: #C5B5F2;
            --c-b7addc: #C8B9F4;
            --c-b8a8e8: #C5B2F3;
            --c-bca6ff: #D0C0FA;
            --c-d0bdff: #D9CCFA;
            --c-57d2e8: #B39AF0;
            --c-5bcde2: #A98CEB;
            --c-38bdf8: #9E86E8;
            --c-f2f5fa: #F5F3FC;
            --c-f5f5ff: #F7F5FF;
            --c-eef2f7: #F1EFF8;
            --c-e5ebf3: #E9E5F4;
            --c-dce3ed: #E7E1F3;
            --c-d6deea: #DED8EE;
            --c-aab7c9: #B9B4CC;
            --c-b2bfd0: #C1BCD2;
            --c-aab4d0: #B9B4CC;
            --c-aab4cd: #B9B4CC;
            --c-9eacc0: #A9A3BE;
            --c-96a5ba: #A9A3BE;
            --c-929db9: #A9A3BE;
            --c-8493b6: #9992B3;
          }
          /* Clean white + lavender palette */
          .qms-layout[data-theme="light"] {
            --c-080c19: #FBFAFE;
            --c-0c1222: #FBFAFE;
            --c-0c1427: #FBFAFE;
            --c-0d1323: #FBFAFE;
            --c-0d1726: #F7F4FC;
            --c-101627: #F7F4FC;
            --c-10172a: #F7F4FC;
            --c-111d2e: #FFFFFF;
            --c-111e30: #F8F5FC;
            --c-131a2e: #F7F4FC;
            --c-151a29: #F5F1FA;
            --c-171e32: #F6F2FB;
            --c-172438: #FFFFFF;
            --c-17243a: #F5F0FB;
            --c-172b3b: #F0EAF8;
            --c-19213a: #F2ECF9;
            --c-1a2134: #F2ECF9;
            --c-1b2440: #EEE7F8;
            --c-1c253b: #F0EAF8;
            --c-202a42: #EDE6F6;
            --c-222b40: #EDE6F6;
            --c-26364c: #E9E2F2;
            --c-29334b: #E8E0F2;
            --c-2b3b52: #E5DDF0;
            --c-30364b: #E5DDF0;
            --c-303951: #E5DDF0;
            --c-303a53: #E2D9EF;
            --c-303a57: #E2D9EF;
            --c-30415a: #DED5ED;
            --c-33364c: #DED5ED;
            --c-34445f: #D9CFEA;
            --c-34465f: #D9CFEA;
            --c-3a4b65: #D3C8E6;
            --c-7467a9: #7654B5;
            --c-7568a8: #8060BC;
            --c-7968b8: #7652B5;
            --c-8060ff: #7652B5;
            --c-8965ff: #8060C6;
            --c-9268ff: #8060C6;
            --c-9875ff: #8B6BCB;
            --c-9971ff: #7652B5;
            --c-a78bfa: #7652B5;
            --c-a99ade: #7652B5;
            --c-b69aff: #8060BC;
            --c-b69cff: #8060BC;
            --c-b6a9e4: #8060BC;
            --c-b7addc: #8060BC;
            --c-b8a8e8: #7652B5;
            --c-bca6ff: #8060BC;
            --c-d0bdff: #8A6BC3;
            --c-57d2e8: #8B6BCB;
            --c-5bcde2: #8B6BCB;
            --c-38bdf8: #8060BC;
            --c-f2f5fa: #29223A;
            --c-f5f5ff: #29223A;
            --c-eef2f7: #29223A;
            --c-e5ebf3: #3D3450;
            --c-dce3ed: #3D3450;
            --c-d6deea: #443A57;
            --c-aab7c9: #675D78;
            --c-b2bfd0: #675D78;
            --c-aab4d0: #675D78;
            --c-aab4cd: #675D78;
            --c-9eacc0: #766C86;
            --c-96a5ba: #766C86;
            --c-929db9: #766C86;
            --c-8493b6: #766C86;
          }
          .qms-layout[data-theme="dark"] .qms-brand-mark { background:linear-gradient(145deg,#9272D5,#60439A); }
          .qms-layout[data-theme="dark"] .qms-nav button.active { background:rgba(139,104,216,.20); border-color:rgba(173,145,235,.35); }
          .qms-layout[data-theme="light"] .qms-brand-mark { background:linear-gradient(145deg,#A58BD5,#7652B5); }
          .qms-layout[data-theme="light"] .qms-nav button.active { background:#EEE7F8; border-color:#D6C7EA; color:#624394; }
          .qms-layout[data-theme="light"] .qms-page-tag { color:#76521D; background:#FFF7E8; border-color:#E9D5AC; }
          .qms-layout[data-theme="light"] .qms-theme-toggle { background:#F1EAF9; border-color:#D8C9EA; color:#65469A; }
        `}</style>

        <div className="qms-layout" data-theme={theme}>
          <aside className="qms-sidebar">
            <div className="qms-brand">
              <div className="qms-brand-mark">▣</div>
              <div><strong>QMS Copilot</strong><small>Quality Management System</small></div>
            </div>
            <nav className="qms-nav" aria-label="Main navigation">
              <button className="active" type="button"><span>＋</span><span>Log Complaint</span></button>
              <button type="button"><span>▤</span><span>My Complaints</span></button>
              <button type="button"><span>▥</span><span>Analytics</span></button>
              <button type="button" onClick={() => navigate('/qa-dashboard')}><span>✓</span><span>QA Dashboard</span></button>
              <button type="button"><span>⚙</span><span>Settings</span></button>
            </nav>
            <div className="qms-sidebar-bottom">
              <div style={{fontWeight:700,color:'var(--c-dce3ed)'}}>◈ &nbsp; QMS</div>
              API &amp; FDF Quality Assurance<br/>
              <button type="button" onClick={() => navigate('/')} style={{marginTop:12,padding:0,border:0,background:'none',color:'var(--c-b6a9e4)',cursor:'pointer',fontSize:11}}>← Back to Home</button>
            </div>
          </aside>
          <div className="qms-content">
            <header className="qms-topbar">
              <div className="qms-topbar-label">Quality Operations <span style={{color:'var(--c-7467a9)'}}> / </span> Complaint Intake</div>
              <div style={{display:'flex',alignItems:'center',gap:14}}>
                <button
                  className="qms-theme-toggle"
                  type="button"
                  onClick={() => setTheme(current => current === "dark" ? "light" : "dark")}
                  aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
                  title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
                >
                  <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
                  {theme === "dark" ? "Light mode" : "Dark mode"}
                </button>
                <div className="qms-profile"><span style={{fontSize:17,color:'var(--c-b7addc)'}}>♧</span><span className="qms-avatar">KS</span><span>Quality Team<br/><small style={{color:'var(--c-9eacc0)',fontWeight:400}}>Workspace</small></span></div>
              </div>
            </header>
            <div className="qms-page-heading">
              <div><h1>Log Customer Complaint</h1><p>API &amp; FDF Quality Assurance Module</p></div>
              <span className="qms-page-tag">● &nbsp; Pending Triage</span>
            </div>
            <div className="app-shell">
              <div className="chat-panel"><CopilotChat /></div>
              <div className="form-panel"><ComplaintForm /></div>
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <style>{`
        :root {
            --c-080c19: #080c19;
            --c-0c1222: #0c1222;
            --c-0c1427: #0c1427;
            --c-0d1323: #0d1323;
            --c-0d1726: #0d1726;
            --c-101627: #101627;
            --c-10172a: #10172a;
            --c-111d2e: #111d2e;
            --c-111e30: #111e30;
            --c-131a2e: #131a2e;
            --c-151a29: #151a29;
            --c-153b35: #153b35;
            --c-171e32: #171e32;
            --c-172438: #172438;
            --c-17243a: #17243a;
            --c-172b3b: #172b3b;
            --c-173b38: #173b38;
            --c-183b38: #183b38;
            --c-19213a: #19213a;
            --c-1a2134: #1a2134;
            --c-1b2440: #1b2440;
            --c-1c253b: #1c253b;
            --c-202a42: #202a42;
            --c-222b40: #222b40;
            --c-26364c: #26364c;
            --c-286b5d: #286b5d;
            --c-29234a: #29234a;
            --c-29334b: #29334b;
            --c-2b3b52: #2b3b52;
            --c-302b22: #302b22;
            --c-30364b: #30364b;
            --c-303951: #303951;
            --c-303a53: #303a53;
            --c-303a57: #303a57;
            --c-30415a: #30415a;
            --c-33364c: #33364c;
            --c-34314f: #34314f;
            --c-34445f: #34445f;
            --c-34465f: #34465f;
            --c-34756a: #34756a;
            --c-35405b: #35405b;
            --c-35415e: #35415e;
            --c-38bdf8: #38bdf8;
            --c-39252e: #39252e;
            --c-39445f: #39445f;
            --c-394664: #394664;
            --c-3a3020: #3a3020;
            --c-3a4b65: #3a4b65;
            --c-42d5a1: #42d5a1;
            --c-46516c: #46516c;
            --c-4ed6a7: #4ed6a7;
            --c-50d8a9: #50d8a9;
            --c-52617a: #52617a;
            --c-55d6ac: #55d6ac;
            --c-57d2e8: #57d2e8;
            --c-5bcde2: #5bcde2;
            --c-62558f: #62558f;
            --c-65b69b: #65b69b;
            --c-6655a0: #6655a0;
            --c-6741e8: #6741e8;
            --c-6744e9: #6744e9;
            --c-7467a9: #7467a9;
            --c-7568a8: #7568a8;
            --c-76643f: #76643f;
            --c-7968b8: #7968b8;
            --c-80505d: #80505d;
            --c-8060ff: #8060ff;
            --c-80643a: #80643a;
            --c-8493b6: #8493b6;
            --c-8591ad: #8591ad;
            --c-8876c8: #8876c8;
            --c-8965ff: #8965ff;
            --c-8994b0: #8994b0;
            --c-919bb4: #919bb4;
            --c-9268ff: #9268ff;
            --c-929db9: #929db9;
            --c-96a5ba: #96a5ba;
            --c-9875ff: #9875ff;
            --c-9971ff: #9971ff;
            --c-9adbc8: #9adbc8;
            --c-9eacc0: #9eacc0;
            --c-a78bfa: #a78bfa;
            --c-a99ade: #a99ade;
            --c-aab4cd: #aab4cd;
            --c-aab4d0: #aab4d0;
            --c-aab7c9: #aab7c9;
            --c-aeb8d0: #aeb8d0;
            --c-b2bfd0: #b2bfd0;
            --c-b69aff: #b69aff;
            --c-b69cff: #b69cff;
            --c-b6a9e4: #b6a9e4;
            --c-b7addc: #b7addc;
            --c-b7c3d3: #b7c3d3;
            --c-b8a8e8: #b8a8e8;
            --c-bca6ff: #bca6ff;
            --c-c2c9db: #c2c9db;
            --c-c5c9dd: #c5c9dd;
            --c-d0bdff: #d0bdff;
            --c-d5c8ff: #d5c8ff;
            --c-d6deea: #d6deea;
            --c-d9d2f1: #d9d2f1;
            --c-d9d7ee: #d9d7ee;
            --c-dce1f2: #dce1f2;
            --c-dce3ed: #dce3ed;
            --c-e2e2f0: #e2e2f0;
            --c-e5a83d: #e5a83d;
            --c-e5ebf3: #e5ebf3;
            --c-e8e2ff: #e8e2ff;
            --c-e9bd75: #e9bd75;
            --c-eef2f7: #eef2f7;
            --c-ef5350: #ef5350;
            --c-f0b45c: #f0b45c;
            --c-f2a3ad: #f2a3ad;
            --c-f2f5fa: #f2f5fa;
            --c-f5f2ff: #f5f2ff;
            --c-f5f5ff: #f5f5ff;
            --c-ff8295: #ff8295;
            --c-fff: #fff;
        }
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

        * {
          box-sizing: border-box;
          margin: 0;
          padding: 0;
        }

        html {
          scroll-behavior: smooth;
        }

        body {
          background: var(--c-080c19);
          color: var(--c-f5f5ff);
          font-family: 'Inter', sans-serif;
        }

        button {
          font-family: inherit;
        }

        .landing {
          min-height: 100vh;
          overflow: hidden;
          background:
            radial-gradient(ellipse at 75% 20%, rgba(91,58,180,.18), transparent 40%),
            var(--c-080c19);
        }

        /* NAVBAR */

        .navbar {
          height: 98px;
          margin: 0 20px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          border-bottom: 1px solid rgba(255,255,255,.08);
        }

        .logo {
          display: flex;
          align-items: center;
          gap: 13px;
          font-size: 25px;
          font-weight: 800;
          letter-spacing: -1px;
        }

        .logo-icon {
          width: 48px;
          height: 48px;
          display: grid;
          place-items: center;
          background: linear-gradient(135deg,var(--c-a78bfa),var(--c-6741e8));
          border-radius: 15px;
          font-size: 25px;
          box-shadow: 0 8px 30px rgba(124,87,255,.25);
        }

        .logo-dot {
          color: var(--c-a78bfa);
        }

        .nav-links {
          display: flex;
          gap: 42px;
        }

        .nav-links a {
          color: var(--c-c5c9dd);
          font-size: 15px;
          font-weight: 500;
          text-decoration: none;
          transition: .3s;
        }

        .nav-links a:hover {
          color: var(--c-a78bfa);
        }

        .nav-actions {
          display: flex;
          gap: 15px;
          align-items: center;
        }

        .sign-btn, .start-btn {
          padding: 16px 23px;
          border-radius: 14px;
          font-weight: 700;
          font-size: 14px;
          cursor: pointer;
          transition: .3s;
        }

        .sign-btn {
          background: transparent;
          color: var(--c-e2e2f0);
          border: 1px solid var(--c-33364c);
        }

        .start-btn {
          color: white;
          border: none;
          background: linear-gradient(110deg,var(--c-9971ff),var(--c-6744e9));
          box-shadow: 0 8px 30px rgba(124,87,255,.2);
        }

        .start-btn:hover, .hero-btn:hover {
          transform: translateY(-3px);
          box-shadow: 0 12px 35px rgba(124,87,255,.35);
        }

        /* HERO */

        .hero {
          min-height: 800px;
          display: grid;
          grid-template-columns: 1.05fr .95fr;
          align-items: center;
          padding: 75px 20px 100px;
          gap: 35px;
        }

        .hero-content {
          max-width: 680px;
        }

        .badge {
          display: inline-flex;
          align-items: center;
          gap: 11px;
          padding: 11px 16px;
          border-radius: 30px;
          border: 1px solid rgba(151,115,255,.35);
          background: rgba(124,87,255,.09);
          color: var(--c-bca6ff);
          font-size: 13px;
          font-weight: 700;
          letter-spacing: .4px;
          margin-bottom: 35px;
        }

        .badge-dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--c-42d5a1);
          box-shadow: 0 0 12px var(--c-42d5a1);
        }

        .hero h1 {
          font-size: clamp(48px,5.2vw,76px);
          font-weight: 800;
          line-height: 1.1;
          letter-spacing: -4px;
          margin-bottom: 24px;
        }

        .gradient-text {
          background: linear-gradient(100deg,var(--c-b69aff),var(--c-8060ff),var(--c-57d2e8));
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
        }

        .hero-description {
          color: var(--c-aab4d0);
          font-size: 19px;
          line-height: 1.9;
          max-width: 630px;
          margin-bottom: 35px;
        }

        .hero-buttons {
          display: flex;
          gap: 16px;
          flex-wrap: wrap;
        }

        .hero-btn {
          padding: 20px 26px;
          border-radius: 15px;
          font-size: 15px;
          font-weight: 700;
          cursor: pointer;
          transition: .3s;
        }

        .primary-btn {
          border: none;
          color: white;
          background: linear-gradient(110deg,var(--c-9971ff),var(--c-6744e9));
        }

        .secondary-btn {
          color: white;
          background: var(--c-151a29);
          border: 1px solid var(--c-30364b);
        }

        .trust-text {
          margin-top: 27px;
          color: var(--c-8493b6);
          font-size: 13px;
        }

        .trust-text span {
          color: var(--c-42d5a1);
          margin-right: 8px;
        }

        /* DASHBOARD PREVIEW */

        .dashboard-wrap {
          position: relative;
          width: 100%;
          max-width: 690px;
          margin: auto;
          perspective: 1200px;
        }

        .dashboard {
          background: linear-gradient(145deg,var(--c-171e32),var(--c-0d1323));
          border: 1px solid var(--c-303a53);
          border-radius: 27px;
          overflow: hidden;
          box-shadow: 0 30px 100px rgba(0,0,0,.45),
                      0 0 80px rgba(115,77,255,.12);
          transform: rotateY(-3deg);
        }

        .dash-top {
          height: 57px;
          padding: 0 22px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          border-bottom: 1px solid var(--c-30364b);
          color: var(--c-929db9);
          font-size: 10px;
          font-weight: 700;
        }

        .dash-dots {
          display: flex;
          gap: 6px;
        }

        .dash-dots span {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--c-46516c);
        }

        .dash-avatar {
          width: 27px;
          height: 27px;
          border-radius: 50%;
          background: linear-gradient(135deg,var(--c-a78bfa),var(--c-38bdf8));
        }

        .dash-body {
          display: grid;
          grid-template-columns: 135px 1fr;
          min-height: 465px;
        }

        .sidebar {
          padding: 25px 12px;
          background: rgba(5,10,25,.3);
          border-right: 1px solid var(--c-30364b);
        }

        .sidebar-title {
          font-size: 12px;
          font-weight: 800;
          margin: 0 7px 25px;
        }

        .side-item {
          padding: 12px 9px;
          margin-bottom: 8px;
          border-radius: 10px;
          font-size: 10px;
          color: var(--c-929db9);
        }

        .side-item.active {
          background: rgba(135,98,255,.18);
          border: 1px solid rgba(135,98,255,.3);
          color: var(--c-d0bdff);
        }

        .dash-main {
          padding: 24px;
          min-width: 0;
        }

        .dash-heading {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
          margin-bottom: 25px;
        }

        .dash-heading h3 {
          font-size: 17px;
        }

        .ready {
          font-size: 9px;
          color: var(--c-55d6ac);
          background: rgba(66,213,161,.1);
          padding: 9px 12px;
          border-radius: 20px;
          white-space: nowrap;
        }

        .dash-subtitle {
          color: var(--c-8591ad);
          font-size: 10px;
          margin-top: 8px;
        }

        .stats {
          display: grid;
          grid-template-columns: repeat(3,1fr);
          gap: 10px;
          margin-bottom: 13px;
        }

        .stat-card {
          padding: 15px 12px;
          background: var(--c-1a2134);
          border: 1px solid var(--c-303951);
          border-radius: 13px;
        }

        .stat-card small {
          display: block;
          color: var(--c-919bb4);
          font-size: 9px;
        }

        .stat-card h2 {
          font-size: 25px;
          margin: 9px 0;
        }

        .stat-card p {
          font-size: 9px;
          color: var(--c-4ed6a7);
        }

        .dash-bottom {
          display: grid;
          grid-template-columns: 1.2fr .8fr;
          gap: 12px;
        }

        .process-card, .risk-card {
          background: var(--c-1a2134);
          border: 1px solid var(--c-303951);
          border-radius: 14px;
          padding: 16px 13px;
          min-width: 0;
        }

        .process-card h4, .risk-card h4 {
          font-size: 11px;
          margin-bottom: 20px;
        }

        .progress-row {
          display: flex;
          align-items: center;
          gap: 8px;
          margin: 18px 0;
          font-size: 9px;
          color: var(--c-aab4cd);
        }

        .progress {
          flex: 1;
          height: 6px;
          background: var(--c-303a57);
          border-radius: 20px;
          overflow: hidden;
        }

        .progress-fill {
          height: 100%;
          border-radius: 20px;
          background: linear-gradient(90deg,var(--c-9268ff),var(--c-5bcde2));
        }

        .progress-row strong {
          font-size: 9px;
        }

        .risk-circle {
          width: 55px;
          height: 55px;
          border: 5px solid var(--c-e5a83d);
          border-right-color: var(--c-39445f);
          border-radius: 50%;
          display: grid;
          place-items: center;
          margin: 15px auto;
          font-size: 14px;
          font-weight: 800;
        }

        .risk-label {
          text-align: center;
          font-size: 10px;
          font-weight: 700;
        }

        .risk-sub {
          color: var(--c-929db9);
          text-align: center;
          font-size: 8px;
          margin-top: 7px;
        }

        .floating-card {
          position: absolute;
          right: -10px;
          bottom: 35px;
          background: var(--c-1c253b);
          border: 1px solid var(--c-394664);
          border-radius: 15px;
          padding: 18px;
          box-shadow: 0 15px 40px rgba(0,0,0,.4);
        }

        .floating-card strong {
          color: var(--c-50d8a9);
          font-size: 13px;
        }

        .floating-card p {
          margin-top: 7px;
          font-size: 10px;
          color: var(--c-c2c9db);
        }

        /* FEATURES */

        .section {
          padding: 100px 25px;
          text-align: center;
          border-top: 1px solid rgba(255,255,255,.06);
        }

        .section-label {
          color: var(--c-a78bfa);
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 2px;
          margin-bottom: 20px;
        }

        .section h2 {
          font-size: clamp(32px,4vw,48px);
          letter-spacing: -2px;
          margin-bottom: 18px;
        }

        .section-description {
          color: var(--c-929db9);
          max-width: 620px;
          line-height: 1.8;
          margin: 0 auto 50px;
        }

        .feature-grid {
          display: grid;
          grid-template-columns: repeat(3,1fr);
          gap: 22px;
          max-width: 1150px;
          margin: auto;
        }

        .feature-card {
          text-align: left;
          padding: 32px;
          border-radius: 20px;
          background: linear-gradient(145deg,var(--c-171e32),var(--c-101627));
          border: 1px solid var(--c-29334b);
          transition: .3s;
        }

        .feature-card:hover {
          transform: translateY(-7px);
          border-color: var(--c-8060ff);
        }

        .feature-icon {
          width: 52px;
          height: 52px;
          display: grid;
          place-items: center;
          font-size: 25px;
          background: rgba(137,102,255,.15);
          border-radius: 15px;
          margin-bottom: 25px;
        }

        .feature-card h3 {
          font-size: 19px;
          margin-bottom: 14px;
        }

        .feature-card p {
          font-size: 14px;
          line-height: 1.8;
          color: var(--c-929db9);
        }

        /* WORKFLOW */

        .workflow {
          display: grid;
          grid-template-columns: repeat(4,1fr);
          gap: 20px;
          max-width: 1100px;
          margin: auto;
          text-align: left;
        }

        .step {
          padding: 25px;
          border-left: 2px solid var(--c-8965ff);
          background: rgba(255,255,255,.025);
          border-radius: 0 15px 15px 0;
        }

        .step span {
          color: var(--c-a78bfa);
          font-size: 12px;
          font-weight: 800;
        }

        .step h3 {
          margin: 15px 0 10px;
          font-size: 17px;
        }

        .step p {
          color: var(--c-929db9);
          font-size: 13px;
          line-height: 1.7;
        }

        /* CTA */

        .cta {
          margin: 60px 25px 90px;
          padding: 75px 25px;
          text-align: center;
          border-radius: 30px;
          background:
            radial-gradient(circle at 50% 100%,rgba(125,88,255,.3),transparent 65%),
            var(--c-131a2e);
          border: 1px solid var(--c-34314f);
        }

        .cta h2 {
          font-size: clamp(30px,4vw,45px);
          margin-bottom: 20px;
        }

        .cta p {
          color: var(--c-aab4cd);
          margin-bottom: 30px;
          line-height: 1.8;
        }

        /* FOOTER */

        .footer {
          padding: 35px 25px;
          border-top: 1px solid var(--c-222b40);
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 20px;
          color: var(--c-8994b0);
          font-size: 12px;
        }

        .footer a {
          color: var(--c-aab4cd);
          text-decoration: none;
          margin-left: 20px;
        }

        @media(max-width: 1050px) {
          .hero {
            grid-template-columns: 1fr;
            padding-top: 70px;
          }

          .hero-content {
            max-width: 800px;
          }

          .dashboard-wrap {
            max-width: 750px;
          }

          .nav-links { gap: 18px; }
        }

        @media(max-width: 700px) {
          .navbar {
            height: auto;
            min-height: 80px;
            flex-wrap: wrap;
            gap: 18px;
            padding: 15px 0;
          }

          .nav-links {
            order: 3;
            width: 100%;
            justify-content: center;
          }

          .nav-links a { font-size: 12px; }

          .nav-actions { gap: 7px; }

          .sign-btn, .start-btn {
            padding: 12px;
            font-size: 11px;
          }

          .hero {
            padding: 65px 18px;
            gap: 55px;
          }

          .hero h1 {
            letter-spacing: -2px;
          }

          .hero-description {
            font-size: 16px;
          }

          .dashboard {
            transform: none;
          }

          .dash-body {
            grid-template-columns: 90px 1fr;
          }

          .sidebar {
            padding: 20px 6px;
          }

          .dash-main {
            padding: 13px 9px;
          }

          .dash-heading h3 { font-size: 13px; }

          .stats { gap: 5px; }

          .stat-card {
            padding: 10px 6px;
          }

          .stat-card h2 { font-size: 19px; }

          .dash-bottom {
            grid-template-columns: 1fr;
          }

          .floating-card {
            right: 0;
            bottom: -35px;
          }

          .feature-grid {
            grid-template-columns: 1fr;
          }

          .workflow {
            grid-template-columns: 1fr 1fr;
          }

          .footer {
            flex-direction: column;
            text-align: center;
          }

          .footer a { margin: 0 8px; }
        }

        @media(max-width: 420px) {
          .workflow { grid-template-columns: 1fr; }
          .dash-top { padding: 0 10px; }
          .ready { font-size: 7px; }
        }

.qms-app[data-theme="light"] {

            --c-080c19: #edf2f8;
            --c-0c1222: #edf2f8;
            --c-0c1427: #edf2f8;
            --c-0d1323: #edf2f8;
            --c-0d1726: #f3f6fb;
            --c-101627: #edf2f8;
            --c-10172a: #edf2f8;
            --c-111d2e: #ffffff;
            --c-111e30: #f4f7fb;
            --c-131a2e: #edf2f8;
            --c-151a29: #edf2f8;
            --c-153b35: #e8f5f0;
            --c-171e32: #edf2f8;
            --c-172438: #ffffff;
            --c-17243a: #edf2f8;
            --c-172b3b: #eaf2f6;
            --c-173b38: #e8f5f0;
            --c-183b38: #dff2ea;
            --c-19213a: #edf2f8;
            --c-1a2134: #edf2f8;
            --c-1b2440: #e9edf8;
            --c-1c253b: #edf2f8;
            --c-202a42: #edf2f8;
            --c-222b40: #edf2f8;
            --c-26364c: #e8edf5;
            --c-286b5d: #9bcfc2;
            --c-29234a: #f0ebfa;
            --c-29334b: #dfe7f0;
            --c-2b3b52: #d8e1ec;
            --c-302b22: #edf2f8;
            --c-30364b: #dfe7f0;
            --c-303951: #dfe7f0;
            --c-303a53: #dfe7f0;
            --c-303a57: #dfe7f0;
            --c-30415a: #d9e2ed;
            --c-33364c: #dfe7f0;
            --c-34314f: #dfe7f0;
            --c-34445f: #dfe7f0;
            --c-34465f: #d4deea;
            --c-34756a: #a8d9cb;
            --c-35405b: #dfe7f0;
            --c-35415e: #dfe7f0;
            --c-38bdf8: #53657a;
            --c-39252e: #fbecee;
            --c-39445f: #dfe7f0;
            --c-394664: #dfe7f0;
            --c-3a3020: #fbf3e5;
            --c-3a4b65: #c6d2e0;
            --c-42d5a1: #53657a;
            --c-46516c: #dfe7f0;
            --c-4ed6a7: #53657a;
            --c-50d8a9: #53657a;
            --c-52617a: #bdc9d8;
            --c-55d6ac: #27866d;
            --c-57d2e8: #53657a;
            --c-5bcde2: #53657a;
            --c-62558f: #7657ad;
            --c-65b69b: #27866d;
            --c-6655a0: #c5b8e2;
            --c-6741e8: #7657c5;
            --c-6744e9: #8060d6;
            --c-7467a9: #7657ad;
            --c-7568a8: #718198;
            --c-76643f: #718198;
            --c-7968b8: #7657ad;
            --c-80505d: #e4b8bf;
            --c-8060ff: #7657c5;
            --c-80643a: #e6c995;
            --c-8493b6: #6b7c91;
            --c-8591ad: #718198;
            --c-8876c8: #718198;
            --c-8965ff: #8060d6;
            --c-8994b0: #718198;
            --c-919bb4: #53657a;
            --c-9268ff: #8060d6;
            --c-929db9: #66778c;
            --c-96a5ba: #718198;
            --c-9875ff: #7657c5;
            --c-9971ff: #8060d6;
            --c-9adbc8: #286c5a;
            --c-9eacc0: #66778c;
            --c-a78bfa: #7657c5;
            --c-a99ade: #7757ad;
            --c-aab4cd: #52647a;
            --c-aab4d0: #53657a;
            --c-aab7c9: #5b6b80;
            --c-aeb8d0: #53657a;
            --c-b2bfd0: #52647a;
            --c-b69aff: #7657c5;
            --c-b69cff: #7657c5;
            --c-b6a9e4: #53657a;
            --c-b7addc: #53657a;
            --c-b7c3d3: #4b5d72;
            --c-b8a8e8: #7054a3;
            --c-bca6ff: #7657c5;
            --c-c2c9db: #26364a;
            --c-c5c9dd: #26364a;
            --c-d0bdff: #7657c5;
            --c-d5c8ff: #26364a;
            --c-d6deea: #26364a;
            --c-d9d2f1: #58417e;
            --c-d9d7ee: #35445a;
            --c-dce1f2: #26364a;
            --c-dce3ed: #26364a;
            --c-e2e2f0: #26364a;
            --c-e5a83d: #53657a;
            --c-e5ebf3: #26364a;
            --c-e8e2ff: #f4efff;
            --c-e9bd75: #53657a;
            --c-eef2f7: #243449;
            --c-ef5350: #c53e4d;
            --c-f0b45c: #98621a;
            --c-f2a3ad: #a83245;
            --c-f2f5fa: #1d2b3d;
            --c-f5f2ff: #2e2450;
            --c-f5f5ff: #1d2b3d;
            --c-ff8295: #b8324b;
            --c-fff: #ffffff;
          }
          .qms-app[data-theme="light"] .landing {
            color:#243449;
            background:radial-gradient(ellipse at 75% 20%, rgba(124,87,197,.10), transparent 42%), #f4f6fb;
          }
          .qms-app[data-theme="light"] .navbar { border-bottom-color:#dfe5ef; }
          .qms-app[data-theme="light"] .logo { color:#1d2b3d; }
          .qms-app[data-theme="light"] .nav-links a { color:#52647a; }
          .qms-app[data-theme="light"] .nav-links a:hover { color:#7657ad; }
          .qms-app[data-theme="light"] .sign-btn { color:#34445a; border-color:#cbd5e1; background:#fff; }
          .qms-app[data-theme="light"] .hero h1,
          .qms-app[data-theme="light"] .section h2,
          .qms-app[data-theme="light"] .landing h2 { color:#1d2b3d; }
          .qms-app[data-theme="light"] .hero-description,
          .qms-app[data-theme="light"] .section-description { color:#596b80; }
          .qms-app[data-theme="light"] .secondary-btn { color:#34445a; background:#fff; border-color:#d5deea; }
          .qms-app[data-theme="light"] .dashboard { background:linear-gradient(145deg,#fff,#f2f5fb); border-color:#d7deeb; box-shadow:0 22px 60px rgba(37,52,73,.12); }
          .qms-app[data-theme="light"] .dash-top,
          .qms-app[data-theme="light"] .sidebar { color:#52647a; border-color:#dfe5ef; }
          .qms-app[data-theme="light"] .sidebar { background:#f4f6fb; }
          .qms-app[data-theme="light"] .stat-card,
          .qms-app[data-theme="light"] .process-card,
          .qms-app[data-theme="light"] .risk-card,
          .qms-app[data-theme="light"] .feature-card { background:#fff; border-color:#dfe5ef; color:#243449; }
          .qms-app[data-theme="light"] .section { border-top-color:#e0e6ef; }
          .qms-app[data-theme="light"] .floating-card { background:#fff; border-color:#d8d0eb; color:#243449; }
          .qms-app[data-theme="light"] .footer { color:#596b80; border-top:1px solid #dfe5ef; }
          .qms-app[data-theme="light"] .footer a { color:#7657ad; }
          .qms-app[data-theme="light"] .auth-overlay { background:rgba(31,41,55,.36); }
          .qms-app[data-theme="light"] .auth-modal { color:#243449; background:linear-gradient(155deg,#fff 0%,#f7f5fc 100%); border-color:#d8cdef; box-shadow:0 24px 70px rgba(31,41,55,.20); }
          .qms-app[data-theme="light"] .auth-field { color:#34445a; }
          .qms-app[data-theme="light"] .auth-field input { color:#243449; background:#fff; border-color:#d4dce8; }
          .qms-app[data-theme="light"] .auth-close { color:#52647a; background:#f1eff8; border-color:#ddd5ed; }
          .qms-theme-toggle { display:inline-flex; align-items:center; gap:7px; padding:7px 11px; border:1px solid var(--c-30415a); border-radius:8px; background:var(--c-172438); color:var(--c-f2f5fa); font:600 12px Inter,Arial,sans-serif; cursor:pointer; white-space:nowrap; }
          .qms-theme-toggle:hover { border-color:var(--c-7968b8); }
          .landing-theme-toggle { display:inline-flex; align-items:center; gap:8px; padding:11px 14px; border-radius:11px; border:1px solid rgba(167,139,250,.35); background:rgba(128,85,245,.10); color:var(--c-f5f5ff); font:600 13px Inter,Arial,sans-serif; cursor:pointer; white-space:nowrap; }
          .qms-app[data-theme="light"] .landing-theme-toggle { color:#6746a5; background:#f0eafb; border-color:#d8c9f1; }

        /* AUTHENTICATION MODAL — UI ONLY */
        .auth-overlay { position:fixed; inset:0; z-index:1000; display:flex; align-items:center; justify-content:center; padding:20px; background:rgba(3,6,16,.78); backdrop-filter:blur(12px); }
        .auth-modal { position:relative; width:min(100%,560px); max-height:calc(100vh - 40px); overflow-y:auto; padding:34px; border:1px solid rgba(167,139,250,.28); border-radius:24px; background:linear-gradient(155deg,var(--c-19213a) 0%,var(--c-10172a) 58%,var(--c-0c1222) 100%); box-shadow:0 28px 90px rgba(0,0,0,.58),0 0 45px rgba(128,85,245,.14); color:var(--c-f5f5ff); }
        .auth-close { position:absolute; top:15px; right:17px; width:34px; height:34px; border:1px solid var(--c-35405b); border-radius:10px; background:var(--c-202a42); color:var(--c-d9d7ee); font-size:21px; cursor:pointer; }
        .auth-mark { width:48px; height:48px; display:grid; place-items:center; border-radius:15px; margin-bottom:20px; font-size:24px; background:linear-gradient(135deg,var(--c-a78bfa),var(--c-6741e8)); box-shadow:0 8px 28px rgba(124,87,255,.28); }
        .auth-modal h2 { font-size:27px; margin-bottom:8px; letter-spacing:-.7px; }
        .auth-subtitle { color:var(--c-aeb8d0); font-size:14px; line-height:1.6; margin-bottom:25px; }
        .auth-form { display:grid; gap:15px; }
        .auth-field { display:grid; gap:8px; color:var(--c-dce1f2); font-size:13px; font-weight:600; }
        .auth-field input, .auth-field select { width:100%; padding:14px 15px; border-radius:11px; outline:none; border:1px solid var(--c-35415e); background:var(--c-0c1427); color:var(--c-f5f5ff); font:inherit; font-size:14px; transition:.2s; }
        .auth-field input:focus, .auth-field select:focus { border-color:var(--c-9875ff); box-shadow:0 0 0 3px rgba(128,85,245,.15); }
        .auth-field select { border-radius:11px; outline:none; border:1px solid var(--c-35415e); background:var(--c-0c1427); color:var(--c-f5f5ff); font:inherit; font-size:14px; }
        .chemist-toggle { display:flex; align-items:center; gap:10px; padding:13px 14px; border:1px solid var(--c-35415e); border-radius:11px; background:rgba(128,85,245,.08); color:var(--c-f5f5ff); font-size:13px; font-weight:600; cursor:pointer; }
        .chemist-toggle input { width:17px; height:17px; accent-color:#7652B5; }
        .chemist-fields { display:grid; gap:14px; padding:16px; border:1px solid rgba(167,139,250,.3); border-radius:14px; background:rgba(128,85,245,.055); }
        .chemist-fields-title { margin:0; color:var(--c-b69cff); font-size:13px; font-weight:700; letter-spacing:.3px; }
        .chemist-address { min-height:82px; resize:vertical; width:100%; padding:13px 15px; border-radius:11px; outline:none; border:1px solid var(--c-35415e); background:var(--c-0c1427); color:var(--c-f5f5ff); font:inherit; font-size:14px; }
        .auth-field textarea:focus { border-color:var(--c-9875ff); box-shadow:0 0 0 3px rgba(128,85,245,.15); }
        .auth-submit { margin-top:5px; padding:15px; border:0; border-radius:12px; color:white; font-weight:700; font-size:14px; cursor:pointer; background:linear-gradient(110deg,var(--c-9971ff),var(--c-6744e9)); box-shadow:0 9px 26px rgba(124,87,255,.22); }
        .auth-switch { margin-top:21px; text-align:center; color:var(--c-aeb8d0); font-size:13px; }
        .auth-switch button,.auth-forgot { padding:0; border:0; background:none; color:var(--c-b69cff); font:inherit; font-weight:700; cursor:pointer; }
        .auth-forgot { justify-self:end; font-size:12px; }
        .auth-notice { padding:10px 12px; border-radius:10px; background:rgba(128,85,245,.12); color:var(--c-d5c8ff); font-size:12px; line-height:1.5; }
        .portal-choice-list { display:grid; gap:13px; margin-top:8px; }
        .portal-choice { width:100%; display:flex; align-items:center; gap:14px; padding:17px; border:1px solid var(--c-35415e); border-radius:15px; background:rgba(255,255,255,.025); color:var(--c-f5f5ff); text-align:left; cursor:pointer; transition:.2s ease; }
        .portal-choice:hover { transform:translateY(-2px); border-color:var(--c-9875ff); background:rgba(128,85,245,.09); }
        .portal-choice-icon { width:43px; height:43px; flex:0 0 43px; display:grid; place-items:center; border-radius:12px; background:rgba(128,85,245,.14); color:var(--c-b69cff); font-size:23px; }
        .employee-choice .portal-choice-icon { background:rgba(66,213,161,.12); color:#55d6ac; }
        .portal-choice-copy { display:grid; gap:6px; flex:1; }
        .portal-choice-copy strong { font-size:15px; }
        .portal-choice-copy small { color:var(--c-aeb8d0); font-size:12px; line-height:1.55; font-weight:400; }
        .portal-choice-arrow { color:var(--c-b69cff); font-size:22px; }
        .password-input-wrap { position:relative; display:flex; align-items:center; }
        .password-input-wrap input { padding-right:65px; }
        .password-toggle { position:absolute; right:12px; border:0; background:transparent; color:var(--c-b69cff); font-size:12px; font-weight:700; cursor:pointer; }
        .auth-back { display:block; margin:18px auto 0; border:0; background:none; color:var(--c-b69cff); font:inherit; font-size:12px; cursor:pointer; }
        .qms-app[data-theme="light"] .portal-choice { color:#29223A; background:#FFFFFF; border-color:#DED3EC; }
        .qms-app[data-theme="light"] .portal-choice-copy small { color:#6B617B; }
        .qms-app[data-theme="light"] .portal-choice:hover { background:#F7F2FC; border-color:#B9A2E3; }
        .qms-app[data-theme="light"] .password-toggle, .qms-app[data-theme="light"] .auth-back { color:#7652B5; }
        @media(max-width:480px) { .portal-choice { padding:13px; gap:10px; } .portal-choice-copy small { font-size:11px; } }
        @media(max-width:480px) { .auth-modal { padding:27px 21px; } .auth-modal h2 { font-size:24px; } }
        /* Landing page: white and lavender in light mode; navy and purple in dark mode */
        .qms-app[data-theme="light"] .landing { background:radial-gradient(ellipse at 78% 15%,rgba(153,119,207,.16),transparent 42%),#FBFAFE !important; color:#29223A; }
        .qms-app[data-theme="light"] .navbar { border-bottom-color:#E7DFF1; }
        .qms-app[data-theme="light"] .logo-icon { background:linear-gradient(135deg,#B9A2E3,#7652B5); box-shadow:0 6px 18px rgba(118,82,181,.16); }
        .qms-app[data-theme="light"] .logo-dot, .qms-app[data-theme="light"] .section-label { color:#7652B5; }
        .qms-app[data-theme="light"] .nav-links a { color:#655B76; }
        .qms-app[data-theme="light"] .nav-links a:hover { color:#7652B5; }
        .qms-app[data-theme="light"] .sign-btn, .qms-app[data-theme="light"] .secondary-btn { color:#55476C; background:#FFFFFF; border-color:#DED3EC; }
        .qms-app[data-theme="light"] .start-btn, .qms-app[data-theme="light"] .primary-btn, .qms-app[data-theme="light"] .hero-btn.primary-btn { background:linear-gradient(110deg,#9271C9,#7652B5); color:#fff; box-shadow:0 7px 18px rgba(118,82,181,.18); }
        .qms-app[data-theme="light"] .hero h1, .qms-app[data-theme="light"] .section h2, .qms-app[data-theme="light"] .landing h2 { color:#29223A; }
        .qms-app[data-theme="light"] .gradient-text { background:linear-gradient(100deg,#7652B5,#A18BCB); -webkit-background-clip:text; background-clip:text; color:transparent; }
        .qms-app[data-theme="light"] .badge { color:#7652B5; background:#F1EAF9; border-color:#DED1EF; }
        .qms-app[data-theme="light"] .hero-description, .qms-app[data-theme="light"] .section-description, .qms-app[data-theme="light"] .trust-text { color:#6B617B; }
        .qms-app[data-theme="light"] .dashboard { background:linear-gradient(145deg,#FFFFFF,#F4EFFA); border-color:#E1D7ED; box-shadow:0 22px 65px rgba(58,42,84,.12); }
        .qms-app[data-theme="light"] .dash-top, .qms-app[data-theme="light"] .sidebar { border-color:#E5DDF0; color:#655B76; }
        .qms-app[data-theme="light"] .sidebar { background:#F5F0FA; }
        .qms-app[data-theme="light"] .stat-card, .qms-app[data-theme="light"] .process-card, .qms-app[data-theme="light"] .risk-card, .qms-app[data-theme="light"] .feature-card { background:#FFFFFF; border-color:#E5DDF0; color:#29223A; }
        .qms-app[data-theme="light"] .floating-card { background:#FFFFFF; border-color:#DCCFEB; color:#29223A; }
        .qms-app[data-theme="light"] .auth-modal { background:linear-gradient(150deg,#FFFFFF,#F5EFFB); border-color:#DCCFEB; color:#29223A; }
        .qms-app[data-theme="dark"] .landing { background:radial-gradient(ellipse at 78% 15%,rgba(118,82,181,.20),transparent 42%),#080B18; }
        .qms-app[data-theme="dark"] .logo-icon, .qms-app[data-theme="dark"] .start-btn, .qms-app[data-theme="dark"] .primary-btn { background:linear-gradient(120deg,#9272D5,#60439A); }
        .qms-app[data-theme="dark"] .gradient-text { background:linear-gradient(100deg,#C5B1F3,#9272D5,#A98CEB); -webkit-background-clip:text; background-clip:text; color:transparent; }
        /* Light theme: premium white + purple accents */
        .qms-app[data-theme="light"] {
          --c-62558f:#7652B5; --c-6741e8:#7652B5; --c-6744e9:#68449F;
          --c-7467a9:#7652B5; --c-7968b8:#7652B5; --c-8060ff:#7652B5;
          --c-8965ff:#8060C6; --c-9268ff:#8060C6; --c-9875ff:#8B6BCB;
          --c-9971ff:#7652B5; --c-a78bfa:#7652B5; --c-b69aff:#A58BD5;
          --c-b69cff:#A58BD5; --c-bca6ff:#A58BD5; --c-d0bdff:#7652B5;
        }
        .qms-app[data-theme="light"] .landing {
          background:radial-gradient(ellipse at 78% 15%,rgba(153,119,207,.16),transparent 42%),radial-gradient(ellipse at 12% 80%,rgba(196,176,226,.12),transparent 38%),#FBFAFE !important;
          color:#29223A;
        }
        .qms-app[data-theme="light"] .navbar { border-bottom-color:#E7DFF1; }
        .qms-app[data-theme="light"] .logo-icon { background:linear-gradient(135deg,#B9A2E3,#7652B5); box-shadow:0 6px 18px rgba(118,82,181,.18); }
        .qms-app[data-theme="light"] .logo-dot, .qms-app[data-theme="light"] .section-label { color:#7652B5; }
        .qms-app[data-theme="light"] .nav-links a { color:#655B76; }
        .qms-app[data-theme="light"] .nav-links a:hover { color:#7652B5; }
        .qms-app[data-theme="light"] .sign-btn, .qms-app[data-theme="light"] .secondary-btn { color:#55476C; background:#FFFFFF; border-color:#DED3EC; }
        .qms-app[data-theme="light"] .start-btn, .qms-app[data-theme="light"] .primary-btn, .qms-app[data-theme="light"] .hero-btn.primary-btn { background:linear-gradient(110deg,#9271C9,#7652B5); color:#FFFFFF; box-shadow:0 7px 20px rgba(118,82,181,.20); }
        .qms-app[data-theme="light"] .hero h1, .qms-app[data-theme="light"] .section h2, .qms-app[data-theme="light"] .landing h2 { color:#29223A; }
        .qms-app[data-theme="light"] .gradient-text { background:linear-gradient(100deg,#7652B5,#A18BCB); -webkit-background-clip:text; background-clip:text; color:transparent; }
        .qms-app[data-theme="light"] .badge { color:#68449F; background:#F1EAF9; border-color:#DED1EF; }
        .qms-app[data-theme="light"] .hero-description, .qms-app[data-theme="light"] .section-description, .qms-app[data-theme="light"] .trust-text { color:#6B617B; }
        .qms-app[data-theme="light"] .dashboard { background:linear-gradient(145deg,#FFFFFF,#F5F0FA); border-color:#E1D7ED; box-shadow:0 22px 65px rgba(58,42,84,.12); }
        .qms-app[data-theme="light"] .dash-top, .qms-app[data-theme="light"] .sidebar { border-color:#E5DDF0; color:#655B76; }
        .qms-app[data-theme="light"] .sidebar { background:#F5F0FA; }
        .qms-app[data-theme="light"] .stat-card, .qms-app[data-theme="light"] .process-card, .qms-app[data-theme="light"] .risk-card, .qms-app[data-theme="light"] .feature-card { background:#FFFFFF; border-color:#E5DDF0; color:#29223A; }
        .qms-app[data-theme="light"] .floating-card { background:#FFFFFF; border-color:#DCCFEB; color:#29223A; }
        .qms-app[data-theme="light"] .auth-modal { background:linear-gradient(150deg,#FFFFFF,#F5EFFB); border-color:#DCCFEB; color:#29223A; }
        .qms-app[data-theme="light"] .auth-field input, .qms-app[data-theme="light"] .auth-field select, .qms-app[data-theme="light"] .chemist-address { background:#FFFFFF; border-color:#DED3EC; color:#29223A; }
        .qms-app[data-theme="light"] .chemist-toggle { color:#55476C; background:#F5F0FA; border-color:#DED3EC; }
        .qms-app[data-theme="light"] .chemist-fields { background:#FAF7FD; border-color:#E5DDF0; }
        .qms-app[data-theme="light"] .chemist-fields-title { color:#7652B5; }
        .qms-app[data-theme="light"] .auth-close, .qms-app[data-theme="light"] .landing-theme-toggle { color:#68449F; background:#F1EAF9; border-color:#DCCFEB; }
        .qms-app[data-theme="light"] .landing-theme-toggle:hover { background:#E9DFF5; }
      `}</style>

      <div className="qms-app" data-theme={theme}>
      <div className="landing">

        {/* NAVBAR */}

        <nav className="navbar">

          <div className="logo">
            <div className="logo-icon">▣</div>
            QMS<span className="logo-dot">.</span>
          </div>

          <div className="nav-links">
            <a href="#platform">Platform</a>
            <a href="#capabilities">Capabilities</a>
            <a href="#workflow">How it works</a>
          </div>

          <div className="nav-actions">
            <button
              className="landing-theme-toggle"
              type="button"
              onClick={() => setTheme(current => current === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
              {theme === "dark" ? "Light" : "Dark"}
            </button>
            <button
              className="sign-btn"
              onClick={() => { setAuthNotice(""); setAuthModal("portal-selection"); }}
            >
              Sign in
            </button>

            <button
              className="start-btn"
              onClick={() => { setAuthNotice(""); setAuthModal("portal-selection"); }}
            >
              Get started ↗
            </button>
          </div>

        </nav>

        {/* HERO SECTION */}

        <section className="hero" id="platform">

          <div className="hero-content">

            <div className="badge">
              <span className="badge-dot"></span>
              AI-POWERED QUALITY OPERATIONS
            </div>

            <h1>
              Quality complaints,
              <br />

              <span className="gradient-text">
                handled intelligently.
              </span>
            </h1>

            <p className="hero-description">
              Bring complaint intake, product identification and
              risk assessment into one clear workflow. Let AI help
              your quality team turn unstructured reports into
              review-ready records.
            </p>

            <div className="hero-buttons">

              <button
                className="hero-btn primary-btn"
                onClick={() => { setAuthNotice(""); setAuthModal("portal-selection"); }}
              >
                Explore the platform →
              </button>

              <button
                className="hero-btn secondary-btn"
                onClick={() =>
                  document.getElementById("workflow").scrollIntoView({
                    behavior: "smooth"
                  })
                }
              >
                See how it works
              </button>

            </div>

            <div className="trust-text">
              <span>♧</span>
              Human review stays in control at every critical step.
            </div>

          </div>

          {/* DASHBOARD MOCKUP */}

          <div className="dashboard-wrap">

            <div className="dashboard">

              <div className="dash-top">
                <div className="dash-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>

                QMS · QUALITY ASSURANCE WORKSPACE

                <div className="dash-avatar"></div>
              </div>

              <div className="dash-body">

                <aside className="sidebar">

                  <div className="sidebar-title">
                    QMS<br />WORKSPACE
                  </div>

                  <div className="side-item active">
                    ✦ &nbsp; New complaint
                  </div>

                  <div className="side-item">
                    ▤ &nbsp; Complaints
                  </div>

                  <div className="side-item">
                    ◷ &nbsp; Triage queue
                  </div>

                  <div className="side-item">
                    ▦ &nbsp; Reports
                  </div>

                  <div className="side-item">
                    ⌘ &nbsp; Analytics
                  </div>

                  <div className="side-item">
                    ⚙ &nbsp; Settings
                  </div>

                </aside>

                <main className="dash-main">

                  <div className="dash-heading">
                    <div>
                      <h3>Quality overview</h3>
                      <p className="dash-subtitle">
                        Complaint intake & triage workspace
                      </p>
                    </div>

                    <span className="ready">
                      ● System ready
                    </span>
                  </div>

                  <div className="stats">

                    <div className="stat-card">
                      <small>Open complaints</small>
                      <h2>24</h2>
                      <p>↑ Active queue</p>
                    </div>

                    <div className="stat-card">
                      <small>Awaiting triage</small>
                      <h2>08</h2>
                      <p>Needs review</p>
                    </div>

                    <div className="stat-card">
                      <small>Reviewed today</small>
                      <h2>12</h2>
                      <p>Review activity</p>
                    </div>

                  </div>

                  <div className="dash-bottom">

                    <div className="process-card">

                      <h4>Complaint processing</h4>

                      {[
                        ["Intake", 88],
                        ["Identification", 72],
                        ["Risk review", 54],
                        ["Ready to log", 36]
                      ].map(([name, value]) => (

                        <div className="progress-row" key={name}>
                          <span>{name}</span>

                          <div className="progress">
                            <div
                              className="progress-fill"
                              style={{ width: `${value}%` }}
                            ></div>
                          </div>

                          <strong>{value}%</strong>
                        </div>

                      ))}

                    </div>

                    <div className="risk-card">

                      <h4>AI risk assessment</h4>

                      <div className="risk-circle">M</div>

                      <div className="risk-label">
                        Medium priority
                      </div>

                      <div className="risk-sub">
                        Illustrative assessment
                      </div>

                    </div>

                  </div>

                </main>

              </div>

            </div>

            <div className="floating-card">
              <strong>✓ Review-ready workflow</strong>
              <p>AI-assisted · Human verified</p>
            </div>

          </div>

        </section>

        {/* CAPABILITIES */}

        <section className="section" id="capabilities">

          <div className="section-label">
            PLATFORM CAPABILITIES
          </div>

          <h2>
            Everything your quality team needs.
          </h2>

          <p className="section-description">
            Simplify complaint management with intelligent
            automation, structured data and human-controlled
            quality decisions.
          </p>

          <div className="feature-grid">

            <div className="feature-card">
              <div className="feature-icon">✦</div>
              <h3>AI Complaint Intake</h3>
              <p>
                Convert unstructured customer complaints into
                structured and review-ready information.
              </p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">⌕</div>
              <h3>Product Identification</h3>
              <p>
                Identify relevant product information and
                organize complaint details efficiently.
              </p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">◈</div>
              <h3>Risk Assessment</h3>
              <p>
                Support quality teams with AI-assisted
                risk assessment and review workflows.
              </p>
            </div>

          </div>

        </section>

        {/* HOW IT WORKS */}

        <section className="section" id="workflow">

          <div className="section-label">
            OUR WORKFLOW
          </div>

          <h2>How QMS works</h2>

          <p className="section-description">
            A simple workflow from complaint submission
            to quality review.
          </p>

          <div className="workflow">

            <div className="step">
              <span>STEP 01</span>
              <h3>Complaint Intake</h3>
              <p>
                Collect customer complaint information
                through a structured process.
              </p>
            </div>

            <div className="step">
              <span>STEP 02</span>
              <h3>AI Processing</h3>
              <p>
                Extract and organize important
                information from the complaint.
              </p>
            </div>

            <div className="step">
              <span>STEP 03</span>
              <h3>Risk Assessment</h3>
              <p>
                Analyze complaint details and support
                quality risk evaluation.
              </p>
            </div>

            <div className="step">
              <span>STEP 04</span>
              <h3>Human Review</h3>
              <p>
                Quality professionals verify the
                information before final decisions.
              </p>
            </div>

          </div>

        </section>

        {/* CTA */}

        <section className="cta">

          <div className="section-label">
            GET STARTED
          </div>

          <h2>
            Make quality management smarter.
          </h2>

          <p>
            Bring your complaint management workflow
            into one intelligent workspace.
          </p>

          <button
            className="hero-btn primary-btn"
            onClick={() => { setAuthNotice(""); setAuthModal("portal-selection"); }}
          >
            Explore QMS Workspace ↗
          </button>

        </section>

        {/* FOOTER */}

        <footer className="footer">

          <div>
            © 2026 QMS. All rights reserved.
          </div>

          <div>
            <a href="#platform">Platform</a>
            <a href="#capabilities">Capabilities</a>
            <a href="#workflow">Workflow</a>
          </div>

        </footer>

        {authModal && (
          <div
            className="auth-overlay"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                setAuthModal(null);
                setAuthNotice("");
                setIsChemist(false);
                setShowPassword(false);
              }
            }}
          >
            <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title">
              <button
                className="auth-close"
                type="button"
                aria-label="Close"
                onClick={() => {
                  setAuthModal(null);
                  setAuthNotice("");
                  setIsChemist(false);
                  setShowPassword(false);
                }}
              >×</button>
              <div className="auth-mark">▣</div>

              {authModal === "portal-selection" ? (
                <>
                  <h2 id="auth-title">Welcome to OJASYN</h2>
                  <p className="auth-subtitle">Choose how you want to continue.</p>
                  <div className="portal-choice-list">
                    <button
                      className="portal-choice customer-choice"
                      type="button"
                      onClick={() => { setAuthNotice(""); setAuthModal("customer-login"); }}
                    >
                      <span className="portal-choice-icon">♙</span>
                      <span className="portal-choice-copy">
                        <strong>Customer Portal</strong>
                        <small>For customers, patients, chemists and distributors who want to submit or track product complaints.</small>
                      </span>
                      <span className="portal-choice-arrow">→</span>
                    </button>
                    <button
                      className="portal-choice employee-choice"
                      type="button"
                      onClick={() => { setAuthNotice(""); setAuthModal("employee-login"); }}
                    >
                      <span className="portal-choice-icon">▥</span>
                      <span className="portal-choice-copy">
                        <strong>Organization Portal</strong>
                        <small>For company owners, administrators, QA, QC, Production and other employees.</small>
                      </span>
                      <span className="portal-choice-arrow">→</span>
                    </button>
                  </div>
                  <p className="auth-switch">External customer complaints can also be submitted through the public complaint portal.</p>
                </>
              ) : (
                <>
                  <h2 id="auth-title">
                    {authModal === "customer-login" ? "Customer Sign In" :
                     authModal === "customer-register" ? "Create Customer Account" :
                     authModal === "employee-login" ? "Organization Sign In" :
                     "Register Your Organization"}
                  </h2>
                  <p className="auth-subtitle">
                    {authModal === "customer-login" ? "Sign in to submit and track your product complaints." :
                     authModal === "customer-register" ? "Create an account to manage your complaint history." :
                     authModal === "employee-login" ? "Sign in with your organization-provided employee account." :
                     "Submit your company details to begin organization onboarding."}
                  </p>

                  <form className="auth-form" onSubmit={async (event) => {
                    event.preventDefault();
                    const formData = new FormData(event.currentTarget);

                    // Both portal buttons use the same authentication endpoint.
                    // The backend determines whether the account is a customer or
                    // employee and returns the correct dashboard_path.
                    if (authModal === "customer-login" || authModal === "employee-login") {
                      setAuthNotice("Signing in...");
                      try {
                        const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            email: String(formData.get("email") || "").trim().toLowerCase(),
                            password: String(formData.get("password") || "")
                          })
                        });
                        const result = await response.json().catch(() => ({}));
                        if (!response.ok) {
                          const detail = typeof result.detail === "string" ? result.detail : "Unable to sign in. Check your credentials and try again.";
                          throw new Error(detail);
                        }
                        if (!result.access_token) {
                          throw new Error("The server response is missing the access token. Please check the backend login response.");
                        }
                        localStorage.setItem("qms-access-token", result.access_token);
                        localStorage.setItem("qms-user-email", String(formData.get("email") || "").trim().toLowerCase());
                        localStorage.setItem("qms-user-name", result.full_name || "");
                        localStorage.setItem("qms-user-role", result.role || "");
                        localStorage.setItem("qms-user-designation", result.designation || "");
                        localStorage.setItem("qms-user-id", result.user_id == null ? "" : String(result.user_id));
                        setAuthModal(null);
                        setAuthNotice("");
                        const normalizedRole = String(result.role || "").toLowerCase().replaceAll(" ", "_");
                        const targetDashboard = normalizedRole === "chemist"
                          ? "/chemist-dashboard"
                          : normalizedRole === "customer"
                            ? "/customer-dashboard"
                            : (result.dashboard_path || "");
                        if (!targetDashboard) {
                          throw new Error("Your account role does not have a dashboard route configured yet.");
                        }
                        navigate(targetDashboard, { replace: true });
                      } catch (error) {
                        setAuthNotice(error.message || "Could not connect to the authentication server. Make sure the backend is running.");
                      }
                      return;
                    }

                    if (authModal === "customer-register") {
                      if (formData.get("password") !== formData.get("confirmPassword")) {
                        setAuthNotice("Passwords do not match. Please check and try again.");
                        return;
                      }
                      setAuthNotice("Customer registration API is not connected yet. Your form is ready for backend integration.");
                      return;
                    }

                    if (authModal === "organization-register") {
                      if (formData.get("admin_password") !== formData.get("confirm_admin_password")) {
                        setAuthNotice("Administrator passwords do not match. Please check and try again.");
                        return;
                      }
                      setAuthNotice("Organization registration form validated. Backend API is not connected yet, so no organization or administrator account has been created.");
                      return;
                    }

                    setAuthNotice("Please select a valid registration option.");
                  }}>
                    {authModal === "customer-register" && <>
                      <label className="auth-field">Full name<input type="text" name="full_name" placeholder="Enter your full name" autoComplete="name" required /></label>
                      <label className="auth-field">Mobile number<input type="tel" name="mobile_number" placeholder="Enter your mobile number" autoComplete="tel" inputMode="tel" pattern="[0-9+() -]{8,16}" required /></label>
                      <label className="auth-field">State<input type="text" name="state" placeholder="Enter state" autoComplete="address-level1" required /></label>
                      <label className="auth-field">City<input type="text" name="city" placeholder="Enter city" autoComplete="address-level2" required /></label>
                      <label className="chemist-toggle"><input type="checkbox" name="is_chemist" checked={isChemist} onChange={(event) => setIsChemist(event.target.checked)} /> I am registering as a Chemist / Pharmacy</label>
                      {isChemist && <div className="chemist-fields">
                        <p className="chemist-fields-title">Chemist / Pharmacy Profile</p>
                        <label className="auth-field">Chemist / Pharmacy Name<input type="text" name="chemist_pharmacy_name" placeholder="Enter pharmacy name" required /></label>
                        <label className="auth-field">Drug Licence Number<input type="text" name="drug_licence_number" placeholder="Enter drug licence number" required /></label>
                        <label className="auth-field">GST Number<input type="text" name="gst_number" placeholder="Enter GST number" /></label>
                        <label className="auth-field">Pharmacy Address<textarea className="chemist-address" name="pharmacy_address" placeholder="Enter complete pharmacy address" required /></label>
                      </div>}
                    </>}

                    {authModal === "organization-register" && <>
                      <div className="auth-notice">Register your company and create its first Organization Administrator account. Employees will be added later by an authorized organization administrator.</div>

                      <p className="chemist-fields-title">Organization details</p>
                      <label className="auth-field">Legal organization name
                        <input type="text" name="organization_name" placeholder="Enter registered company name" autoComplete="organization" required />
                      </label>
                      <label className="auth-field">Organization registration number
                        <input type="text" name="registration_number" placeholder="Company / incorporation registration number" required />
                      </label>
                      <label className="auth-field">GST number (optional)
                        <input type="text" name="gst_number" placeholder="Enter GSTIN, if applicable" />
                      </label>
                      <label className="auth-field">Organization type
                        <select name="organization_type" defaultValue="" required>
                          <option value="" disabled>Select organization type</option>
                          <option value="pharmaceutical_manufacturer">Pharmaceutical manufacturer</option>
                          <option value="pharmaceutical_distributor">Pharmaceutical distributor</option>
                          <option value="healthcare">Healthcare organization</option>
                          <option value="other">Other</option>
                        </select>
                      </label>

                      <p className="chemist-fields-title">Company contact details</p>
                      <label className="auth-field">Official company email
                        <input type="email" name="official_email" placeholder="quality@company.com" autoComplete="email" required />
                      </label>
                      <label className="auth-field">Company contact number
                        <input type="tel" name="company_mobile" placeholder="Enter company contact number" inputMode="tel" pattern="[0-9+() -]{8,16}" required />
                      </label>
                      <label className="auth-field">Registered address
                        <textarea className="chemist-address" name="registered_address" placeholder="Enter complete registered address" required />
                      </label>
                      <label className="auth-field">Country
                        <input type="text" name="country" defaultValue="India" autoComplete="country-name" required />
                      </label>
                      <label className="auth-field">State
                        <input type="text" name="state" placeholder="Enter state" autoComplete="address-level1" required />
                      </label>
                      <label className="auth-field">City
                        <input type="text" name="city" placeholder="Enter city" autoComplete="address-level2" required />
                      </label>
                      <label className="auth-field">PIN code
                        <input type="text" name="postal_code" placeholder="Enter PIN code" inputMode="numeric" pattern="[0-9]{6}" required />
                      </label>

                      <p className="chemist-fields-title">Primary organization administrator</p>
                      <label className="auth-field">Administrator full name
                        <input type="text" name="admin_full_name" placeholder="Enter administrator's full name" autoComplete="name" required />
                      </label>
                      <label className="auth-field">Designation
                        <input type="text" name="admin_designation" placeholder="e.g. Company Owner / Quality Head" required />
                      </label>
                      <label className="auth-field">Administrator login email
                        <input type="email" name="admin_email" placeholder="admin@company.com" autoComplete="username" required />
                      </label>
                      <label className="auth-field">Create administrator password
                        <div className="password-input-wrap">
                          <input type={showPassword ? "text" : "password"} name="admin_password" placeholder="Create a strong password" autoComplete="new-password" minLength="8" required />
                          <button type="button" className="password-toggle" onClick={() => setShowPassword(value => !value)}>{showPassword ? "Hide" : "Show"}</button>
                        </div>
                      </label>
                      <label className="auth-field">Confirm administrator password
                        <input type="password" name="confirm_admin_password" placeholder="Re-enter administrator password" autoComplete="new-password" minLength="8" required />
                      </label>
                    </>}

                    {authModal !== "organization-register" && <label className="auth-field">
                      {authModal === "employee-login" ? "Work email address" : "Email address"}
                      <input type="email" name="email" placeholder={authModal === "employee-login" ? "you@company.com" : "you@example.com"} autoComplete="email" required />
                    </label>}

                    {authModal !== "organization-register" && <label className="auth-field">
                      Password
                      <div className="password-input-wrap">
                        <input type={showPassword ? "text" : "password"} name="password" placeholder="Enter your password" autoComplete={authModal.endsWith("login") ? "current-password" : "new-password"} minLength="6" required />
                        <button type="button" className="password-toggle" onClick={() => setShowPassword(value => !value)}>{showPassword ? "Hide" : "Show"}</button>
                      </div>
                    </label>}

                    {authModal === "customer-register" && <label className="auth-field">Confirm password<input type="password" name="confirmPassword" placeholder="Re-enter your password" autoComplete="new-password" minLength="6" required /></label>}

                    {(authModal === "customer-login" || authModal === "employee-login") && <button className="auth-forgot" type="button" onClick={() => setAuthNotice("Password recovery will be connected to the backend authentication service.")}>Forgot password?</button>}
                    <button className="auth-submit" type="submit">
                      {authModal === "customer-login" || authModal === "employee-login" ? "Sign in" : "Submit registration"} →
                    </button>
                    {authNotice && <div className="auth-notice" role="status">{authNotice}</div>}
                  </form>

                  <div className="auth-switch">
                    {authModal === "customer-login" ? <>Don't have a customer account? <button type="button" onClick={() => { setAuthNotice(""); setIsChemist(false); setAuthModal("customer-register"); }}>Register</button></> : null}
                    {authModal === "customer-register" ? <>Already have a customer account? <button type="button" onClick={() => { setAuthNotice(""); setAuthModal("customer-login"); }}>Sign in</button></> : null}
                    {authModal === "employee-login" ? <>New organization? <button type="button" onClick={() => { setAuthNotice(""); setAuthModal("organization-register"); }}>Register your company</button></> : null}
                    {authModal === "organization-register" ? <>Already registered? <button type="button" onClick={() => { setAuthNotice(""); setAuthModal("employee-login"); }}>Employee sign in</button></> : null}
                  </div>
                  <button className="auth-back" type="button" onClick={() => { setAuthNotice(""); setIsChemist(false); setShowPassword(false); setAuthModal("portal-selection"); }}>← Back to portal selection</button>
                </>
              )}
            </section>
          </div>
        )}

      </div>
      </div>
    </>
  );
};
export default App;
