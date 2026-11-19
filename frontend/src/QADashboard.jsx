import React, { useMemo, useState } from "react";

const sampleComplaints = [
  { id: "CMP-2026-0148", product: "Paracetamol 500 mg", batch: "PCM-24A91", category: "Packaging", severity: "High", status: "Under Investigation", date: "17 Nov 2026", owner: "A. Sharma" },
  { id: "CMP-2026-0147", product: "Amoxicillin 250 mg", batch: "AMX-19B42", category: "Product Quality", severity: "Medium", status: "Awaiting Review", date: "17 Nov 2026", owner: "R. Patil" },
  { id: "CMP-2026-0146", product: "Vitamin D3 Drops", batch: "VD3-08C12", category: "Labeling", severity: "Low", status: "Closed", date: "16 Nov 2026", owner: "S. Kulkarni" },
  { id: "CMP-2026-0145", product: "Cough Syrup 100 ml", batch: "CS-72D08", category: "Adverse Event", severity: "Critical", status: "Escalated", date: "16 Nov 2026", owner: "M. Joshi" },
  { id: "CMP-2026-0144", product: "ORS Sachet", batch: "ORS-55F31", category: "Product Quality", severity: "Medium", status: "Under Investigation", date: "15 Nov 2026", owner: "A. Sharma" },
];

const navItems = [
  ["▦", "Overview"], ["▤", "Complaints"], ["◷", "Triage Queue"],
  ["⌁", "Investigations"], ["▥", "Reports & Analytics"], ["✓", "CAPA"],
];

const trend = [34, 48, 42, 61, 53, 72, 64, 82, 69, 88, 76, 94];

function Icon({ children }) {
  return <span className="qa-icon" aria-hidden="true">{children}</span>;
}

export default function QADashboard() {
  const [activeNav, setActiveNav] = useState("Overview");
  const [period, setPeriod] = useState("Last 30 days");
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("All severity");
  const [showAll, setShowAll] = useState(false);

  const filtered = useMemo(() => sampleComplaints.filter((item) => {
    const text = `${item.id} ${item.product} ${item.batch} ${item.category}`.toLowerCase();
    return text.includes(search.toLowerCase()) && (severity === "All severity" || item.severity === severity);
  }), [search, severity]);

  const exportCsv = () => {
    const rows = [
      ["Complaint ID", "Product", "Batch", "Category", "Severity", "Status", "Date", "Owner"],
      ...filtered.map(({ id, product, batch, category, severity, status, date, owner }) =>
        [id, product, batch, category, severity, status, date, owner])
    ];
    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "qms-qa-complaints.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="qa-shell">
      <style>{styles}</style>
      <aside className="qa-sidebar">
        <div className="qa-brand"><span className="qa-brand-mark">Q</span><span>QMS<span className="qa-brand-dot">.</span><small>QUALITY MANAGEMENT</small></span></div>
        <div className="qa-workspace-label">WORKSPACE</div>
        <nav className="qa-nav">
          {navItems.map(([icon, label]) => (
            <button key={label} className={activeNav === label ? "qa-nav-item active" : "qa-nav-item"} onClick={() => setActiveNav(label)}>
              <Icon>{icon}</Icon>{label}{label === "Triage Queue" && <b className="qa-nav-count">8</b>}
            </button>
          ))}
        </nav>
        <div className="qa-sidebar-bottom">
          <div className="qa-help-card"><span>✦</span><b>Quality assistant</b><p>Need help reviewing a complaint?</p><button onClick={() => alert("Connect this action to your AI assistant route.")}>Open assistant ↗</button></div>
          <button className="qa-nav-item"><Icon>⚙</Icon>Settings</button>
          <div className="qa-user"><div className="qa-avatar">AS</div><div><b>Alex Sharma</b><small>QA Administrator</small></div><span className="qa-more">•••</span></div>
        </div>
      </aside>

      <main className="qa-main">
        <header className="qa-topbar">
          <div className="qa-breadcrumb">Workspace <span>/</span> <b>{activeNav}</b></div>
          <div className="qa-top-actions"><span className="qa-system"><i /> All systems operational</span><button className="qa-bell" aria-label="Notifications">♧<i /></button><div className="qa-avatar small">AS</div></div>
        </header>

        <div className="qa-content">
          <section className="qa-heading">
            <div><div className="qa-eyebrow">QUALITY ASSURANCE WORKSPACE</div><h1>{activeNav === "Overview" ? "QA Dashboard" : activeNav}</h1><p>Monitor complaint quality, review activity, and investigation performance.</p></div>
            <div className="qa-heading-actions"><select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Reporting period"><option>Last 7 days</option><option>Last 30 days</option><option>Last 90 days</option></select><button className="qa-primary" onClick={exportCsv}>↓ Export report</button></div>
          </section>

          <section className="qa-kpis">
            <article className="qa-kpi"><div className="qa-kpi-top"><span>Total complaints</span><span className="qa-kpi-icon purple">▤</span></div><strong>248</strong><div className="qa-kpi-foot"><em>↗ 12.8%</em><span>vs previous period</span></div><div className="qa-spark"><i style={{height:"35%"}}/><i style={{height:"48%"}}/><i style={{height:"42%"}}/><i style={{height:"68%"}}/><i style={{height:"56%"}}/><i style={{height:"78%"}}/><i style={{height:"92%"}}/></div></article>
            <article className="qa-kpi"><div className="qa-kpi-top"><span>Open complaints</span><span className="qa-kpi-icon blue">◷</span></div><strong>36</strong><div className="qa-kpi-foot"><em className="neutral">8 need triage</em><span>Requires attention</span></div><div className="qa-kpi-progress"><i style={{width:"64%"}}/></div></article>
            <article className="qa-kpi"><div className="qa-kpi-top"><span>Under investigation</span><span className="qa-kpi-icon amber">⌕</span></div><strong>19</strong><div className="qa-kpi-foot"><em className="neutral">5 high priority</em><span>Active investigations</span></div><div className="qa-kpi-progress amber-progress"><i style={{width:"48%"}}/></div></article>
            <article className="qa-kpi"><div className="qa-kpi-top"><span>Closed this period</span><span className="qa-kpi-icon green">✓</span></div><strong>82</strong><div className="qa-kpi-foot"><em>↗ 9.4%</em><span>vs previous period</span></div><div className="qa-kpi-progress green-progress"><i style={{width:"82%"}}/></div></article>
          </section>

          <section className="qa-grid-main">
            <article className="qa-panel qa-trend-panel">
              <div className="qa-panel-head"><div><h2>Complaint trend</h2><p>Incoming complaints over the selected period</p></div><button className="qa-more-button">•••</button></div>
              <div className="qa-chart-legend"><span><i className="legend-purple"/>Complaints received</span><span><i className="legend-blue"/>Complaints resolved</span></div>
              <div className="qa-chart">
                <div className="qa-y-labels"><span>100</span><span>75</span><span>50</span><span>25</span><span>0</span></div>
                <div className="qa-chart-area">
                  {[0,1,2,3,4].map((n)=><div className="qa-gridline" key={n}/>)}
                  <svg viewBox="0 0 600 190" preserveAspectRatio="none" role="img" aria-label="Complaint trend chart">
                    <defs><linearGradient id="qaFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#9275ff" stopOpacity=".24"/><stop offset="100%" stopColor="#9275ff" stopOpacity="0"/></linearGradient></defs>
                    <path d="M0 135 L55 108 L109 121 L164 80 L218 96 L273 62 L327 78 L382 42 L436 65 L491 27 L545 48 L600 12 L600 190 L0 190Z" fill="url(#qaFill)"/>
                    <polyline points="0,135 55,108 109,121 164,80 218,96 273,62 327,78 382,42 436,65 491,27 545,48 600,12" fill="none" stroke="#9a80ff" strokeWidth="3" vectorEffect="non-scaling-stroke"/>
                    <polyline points="0,158 55,146 109,151 164,130 218,139 273,115 327,125 382,106 436,116 491,91 545,103 600,82" fill="none" stroke="#58c5d8" strokeWidth="2.5" vectorEffect="non-scaling-stroke"/>
                  </svg>
                  <div className="qa-x-labels"><span>Oct 20</span><span>Oct 24</span><span>Oct 28</span><span>Nov 01</span><span>Nov 05</span><span>Nov 09</span><span>Nov 13</span><span>Nov 17</span></div>
                </div>
              </div>
            </article>

            <article className="qa-panel qa-breakdown-panel">
              <div className="qa-panel-head"><div><h2>Severity breakdown</h2><p>Current complaint distribution</p></div><button className="qa-more-button">•••</button></div>
              <div className="qa-severity-wrap"><div className="qa-donut"><div><strong>36</strong><small>Open items</small></div></div><div className="qa-severity-legend">
                <div><i className="critical-dot"/><span>Critical</span><b>3</b></div><div><i className="high-dot"/><span>High</span><b>8</b></div><div><i className="medium-dot"/><span>Medium</span><b>16</b></div><div><i className="low-dot"/><span>Low</span><b>9</b></div>
              </div></div>
              <div className="qa-risk-note"><span>✦</span><div><b>Priority attention</b><small>3 critical complaints require immediate QA review.</small></div></div>
            </article>
          </section>

          <section className="qa-grid-bottom">
            <article className="qa-panel qa-table-panel">
              <div className="qa-panel-head qa-table-head"><div><h2>Recent complaints</h2><p>Latest records requiring quality oversight</p></div><button className="qa-text-button" onClick={() => setShowAll(!showAll)}>{showAll ? "Show less" : "View all complaints"} →</button></div>
              <div className="qa-table-tools"><input value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="⌕  Search complaint, product or batch..." aria-label="Search complaints"/><select value={severity} onChange={(e)=>setSeverity(e.target.value)} aria-label="Filter by severity"><option>All severity</option><option>Critical</option><option>High</option><option>Medium</option><option>Low</option></select></div>
              <div className="qa-table-scroll"><table><thead><tr><th>COMPLAINT ID</th><th>PRODUCT / BATCH</th><th>SEVERITY</th><th>STATUS</th><th>OWNER</th><th>DATE</th></tr></thead><tbody>
                {(showAll ? filtered : filtered.slice(0,4)).map((item)=><tr key={item.id}><td><b className="qa-id">{item.id}</b><small>{item.category}</small></td><td><b>{item.product}</b><small>Batch: {item.batch}</small></td><td><span className={`qa-pill ${item.severity.toLowerCase()}`}>{item.severity}</span></td><td><span className="qa-status"><i className={item.status === "Closed" ? "closed" : item.status === "Escalated" ? "escalated" : ""}/>{item.status}</span></td><td>{item.owner}</td><td>{item.date}</td></tr>)}
                {filtered.length === 0 && <tr><td colSpan="6" className="qa-empty">No complaints match your filters.</td></tr>}
              </tbody></table></div>
            </article>
            <article className="qa-panel qa-activity-panel">
              <div className="qa-panel-head"><div><h2>QA activity</h2><p>Recent team updates</p></div><button className="qa-more-button">•••</button></div>
              <div className="qa-activity-list">
                <div className="qa-activity"><span className="qa-activity-icon purple">✓</span><div><b>Complaint approved</b><p>CMP-2026-0142 moved to closed.</p><small>12 minutes ago · A. Sharma</small></div></div>
                <div className="qa-activity"><span className="qa-activity-icon amber">!</span><div><b>Investigation escalated</b><p>CMP-2026-0145 marked critical.</p><small>48 minutes ago · M. Joshi</small></div></div>
                <div className="qa-activity"><span className="qa-activity-icon blue">✦</span><div><b>AI extraction completed</b><p>Product and batch details identified.</p><small>1 hour ago · QMS Assistant</small></div></div>
                <div className="qa-activity"><span className="qa-activity-icon green">＋</span><div><b>New complaint received</b><p>CMP-2026-0148 added to intake.</p><small>2 hours ago · System</small></div></div>
              </div>
            </article>
          </section>
          <footer className="qa-footer"><span>QMS Quality Management System</span><span>Dashboard data shown is illustrative · <b>QA workspace</b></span></footer>
        </div>
      </main>
    </div>
  );
}

const styles = `
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
.qa-shell{--bg:#0a0e1c;--panel:#11182b;--panel2:#151e34;--line:rgba(170,185,225,.13);--text:#f1f3fc;--muted:#929db8;--purple:#9275ff;min-height:100vh;background:var(--bg);color:var(--text);font-family:'DM Sans',Inter,system-ui,sans-serif;display:flex;font-size:13px}.qa-shell *{box-sizing:border-box}.qa-sidebar{width:244px;flex:0 0 244px;min-height:100vh;border-right:1px solid var(--line);background:#0d1324;padding:25px 16px 18px;display:flex;flex-direction:column}.qa-brand{display:flex;align-items:center;gap:11px;padding:0 9px 35px;font-size:19px;font-weight:800;letter-spacing:.4px}.qa-brand-mark{width:37px;height:37px;display:grid;place-items:center;border-radius:11px;background:linear-gradient(145deg,#a18aff,#6548df);box-shadow:0 8px 22px #7658ed44}.qa-brand-dot{color:#a995ff}.qa-brand small{display:block;font-size:8px;letter-spacing:1.2px;color:#74809c;font-weight:700;margin-top:1px}.qa-workspace-label{font-size:9px;letter-spacing:1.5px;color:#65718d;font-weight:800;padding:0 12px 12px}.qa-nav{display:grid;gap:5px}.qa-nav-item{height:43px;border:0;background:transparent;color:#929db8;display:flex;align-items:center;gap:12px;text-align:left;padding:0 12px;border-radius:10px;font:600 12px inherit;cursor:pointer;width:100%;transition:.18s}.qa-nav-item:hover{background:#ffffff08;color:#e8eaff}.qa-nav-item.active{background:linear-gradient(90deg,#7658ed30,#7658ed0c);color:#d8ceff;border:1px solid #9275ff2c}.qa-icon{width:18px;text-align:center;font-size:16px;color:#8894b1}.active .qa-icon{color:#b7a7ff}.qa-nav-count{margin-left:auto;background:#8b6cff2c;color:#cbbfff;border-radius:20px;padding:3px 7px;font-size:10px}.qa-sidebar-bottom{margin-top:auto}.qa-help-card{border:1px solid #9275ff30;background:linear-gradient(145deg,#8b6cff17,#ffffff04);border-radius:13px;padding:14px;margin:0 2px 20px}.qa-help-card>span{color:#b9aaff;font-size:18px}.qa-help-card b{display:block;margin-top:8px;font-size:11px}.qa-help-card p{font-size:10px;line-height:1.5;color:#8995b0;margin:5px 0 11px}.qa-help-card button{border:0;background:transparent;color:#b8a8ff;padding:0;font:700 10px inherit;cursor:pointer}.qa-user{border-top:1px solid var(--line);margin-top:14px;padding:17px 4px 0;display:flex;align-items:center;gap:9px}.qa-avatar{width:34px;height:34px;border-radius:50%;background:linear-gradient(145deg,#7f6be9,#43a7b9);display:grid;place-items:center;font-size:10px;font-weight:800;color:white;flex-shrink:0}.qa-user b{display:block;font-size:10px}.qa-user small{display:block;color:#7f8ba7;font-size:9px;margin-top:3px}.qa-more{margin-left:auto;color:#77839e;letter-spacing:2px}.qa-main{min-width:0;flex:1}.qa-topbar{height:70px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 38px;background:#0d1323a8}.qa-breadcrumb{color:#7e8aa6;font-size:11px}.qa-breadcrumb span{padding:0 9px;color:#4e5a75}.qa-breadcrumb b{color:#e5e9f8;font-weight:600}.qa-top-actions{display:flex;align-items:center;gap:20px}.qa-system{font-size:10px;color:#8fa3b6}.qa-system i{display:inline-block;width:6px;height:6px;background:#54d6a2;border-radius:50%;margin-right:7px;box-shadow:0 0 0 4px #54d6a21a}.qa-bell{position:relative;border:0;background:none;color:#aab5ce;font-size:19px;cursor:pointer}.qa-bell i{position:absolute;width:5px;height:5px;border-radius:50%;background:#a58aff;right:0;top:2px}.qa-avatar.small{width:31px;height:31px}.qa-content{max-width:1500px;margin:auto;padding:34px 38px 20px}.qa-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:25px}.qa-eyebrow{font-size:9px;letter-spacing:1.5px;font-weight:800;color:#a995ff}.qa-heading h1{font-size:27px;letter-spacing:-.8px;margin:7px 0 5px}.qa-heading p,.qa-panel-head p{font-size:11px;color:var(--muted);margin:0}.qa-heading-actions{display:flex;gap:9px}.qa-heading-actions select,.qa-table-tools select{border:1px solid var(--line);background:#121b30;color:#c3cce0;border-radius:9px;padding:10px 12px;font:500 10px inherit;outline:none}.qa-primary{border:1px solid #9275ff55;background:linear-gradient(135deg,#8c70f5,#694bd8);color:white;padding:0 15px;border-radius:9px;font:700 10px inherit;cursor:pointer;box-shadow:0 7px 18px #7658ed26}.qa-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:17px}.qa-kpi,.qa-panel{border:1px solid var(--line);background:linear-gradient(145deg,#141d32,#101729);border-radius:13px}.qa-kpi{padding:17px 18px;min-height:142px;position:relative;overflow:hidden}.qa-kpi-top{display:flex;align-items:center;justify-content:space-between;color:#a1acc5;font-size:10px;font-weight:600}.qa-kpi-icon{width:29px;height:29px;border-radius:9px;display:grid;place-items:center;font-size:15px}.purple{color:#b7a7ff;background:#9275ff1c}.blue{color:#6ed3e2;background:#58c5d81b}.amber{color:#eab76b;background:#eab76b1b}.green{color:#67d8ad;background:#54d6a21b}.qa-kpi>strong{display:block;font-size:27px;letter-spacing:-1px;margin:10px 0 5px}.qa-kpi-foot{display:flex;align-items:center;gap:6px;font-size:9px;color:#77839f}.qa-kpi-foot em{font-style:normal;color:#5fd3a4;font-weight:700}.qa-kpi-foot em.neutral{color:#d6b06e}.qa-spark{position:absolute;right:16px;bottom:17px;height:34px;display:flex;align-items:flex-end;gap:4px;opacity:.75}.qa-spark i{width:5px;border-radius:3px 3px 0 0;background:#8e73f5}.qa-kpi-progress{height:4px;background:#26314a;border-radius:10px;margin-top:14px;overflow:hidden}.qa-kpi-progress i{height:100%;display:block;background:#67c7d8;border-radius:10px}.amber-progress i{background:#eab76b}.green-progress i{background:#54d6a2}.qa-grid-main{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(290px,.85fr);gap:15px;margin-bottom:15px}.qa-panel{padding:18px}.qa-panel-head{display:flex;justify-content:space-between;align-items:flex-start}.qa-panel-head h2{font-size:13px;margin:0 0 5px;font-weight:700}.qa-more-button{border:0;background:transparent;color:#8591ab;letter-spacing:2px;cursor:pointer}.qa-chart-legend{display:flex;gap:17px;margin:17px 0 8px 36px;color:#909bb5;font-size:9px}.qa-chart-legend span{display:flex;align-items:center;gap:6px}.qa-chart-legend i{width:7px;height:7px;border-radius:50%}.legend-purple{background:#9a80ff}.legend-blue{background:#58c5d8}.qa-chart{display:flex;height:190px}.qa-y-labels{display:flex;flex-direction:column;justify-content:space-between;color:#6e7a96;font-size:8px;padding:0 10px 24px 0}.qa-chart-area{position:relative;flex:1;height:166px}.qa-gridline{height:1px;background:#ffffff0b;position:absolute;left:0;right:0}.qa-gridline:nth-child(1){top:0}.qa-gridline:nth-child(2){top:25%}.qa-gridline:nth-child(3){top:50%}.qa-gridline:nth-child(4){top:75%}.qa-gridline:nth-child(5){top:100%}.qa-chart-area svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}.qa-x-labels{position:absolute;top:174px;left:0;right:0;display:flex;justify-content:space-between;color:#6e7a96;font-size:8px}.qa-severity-wrap{display:flex;align-items:center;justify-content:center;gap:25px;padding:22px 0 18px}.qa-donut{width:132px;height:132px;border-radius:50%;background:conic-gradient(#d45c72 0 8.3%,#e9a65b 8.3% 30.5%,#8d75f1 30.5% 75%,#5abf9b 75% 100%);display:grid;place-items:center;position:relative}.qa-donut:before{content:"";position:absolute;inset:13px;border-radius:50%;background:#121a2e}.qa-donut>div{position:relative;text-align:center}.qa-donut strong{display:block;font-size:25px}.qa-donut small{display:block;font-size:8px;color:#8995b0}.qa-severity-legend{display:grid;gap:12px;min-width:100px}.qa-severity-legend div{display:grid;grid-template-columns:8px 1fr auto;align-items:center;gap:7px;font-size:9px;color:#a4aec4}.qa-severity-legend i{width:7px;height:7px;border-radius:50%}.qa-severity-legend b{color:#e6eafa}.critical-dot{background:#d45c72}.high-dot{background:#e9a65b}.medium-dot{background:#8d75f1}.low-dot{background:#5abf9b}.qa-risk-note{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #e9a65b2a;background:#e9a65b0c;border-radius:9px}.qa-risk-note>span{color:#e9b66f;font-size:17px}.qa-risk-note b{display:block;font-size:9px;color:#e8c18a}.qa-risk-note small{display:block;font-size:8px;color:#9d9a9a;margin-top:3px}.qa-grid-bottom{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(290px,.85fr);gap:15px}.qa-table-head{align-items:center}.qa-text-button{border:0;background:none;color:#b5a6ff;font:700 9px inherit;cursor:pointer}.qa-table-tools{display:flex;gap:9px;margin:17px 0 12px}.qa-table-tools input{flex:1;min-width:100px;background:#0c1426;border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:#e7eafa;font:10px inherit;outline:none}.qa-table-tools input::placeholder{color:#6f7b97}.qa-table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th{text-align:left;color:#6f7c98;font-size:8px;letter-spacing:.7px;font-weight:700;padding:10px 10px;border-bottom:1px solid var(--line)}td{padding:12px 10px;border-bottom:1px solid #ffffff09;color:#aeb8cd;font-size:9px}tbody tr:last-child td{border-bottom:0}td b{display:block;color:#e2e7f5;font-size:9px;font-weight:600}td small{display:block;color:#7885a1;font-size:8px;margin-top:4px}.qa-id{color:#b6a5ff!important}.qa-pill{display:inline-block;border-radius:20px;padding:4px 8px;font-size:8px;font-weight:700}.qa-pill.critical{background:#d45c7220;color:#ef8999}.qa-pill.high{background:#e9a65b20;color:#efbd79}.qa-pill.medium{background:#9275ff20;color:#bcaaff}.qa-pill.low{background:#54d6a21b;color:#70dcb3}.qa-status{display:flex;align-items:center;gap:6px;font-size:9px}.qa-status i{width:6px;height:6px;border-radius:50%;background:#e9b66f}.qa-status i.closed{background:#54d6a2}.qa-status i.escalated{background:#d45c72}.qa-empty{text-align:center;padding:25px;color:#8591ab}.qa-activity-list{margin-top:15px}.qa-activity{display:flex;gap:10px;padding:12px 0;border-bottom:1px solid #ffffff0a}.qa-activity:last-child{border:0}.qa-activity-icon{width:27px;height:27px;flex:0 0 27px;border-radius:8px;display:grid;place-items:center;font-size:12px}.qa-activity b{font-size:9px}.qa-activity p{font-size:9px;color:#9aa5bf;margin:4px 0}.qa-activity small{font-size:8px;color:#697691}.qa-footer{display:flex;justify-content:space-between;gap:15px;padding:20px 2px 5px;color:#687590;font-size:8px}.qa-footer b{color:#9b8af1;font-weight:600}
@media(max-width:1150px){.qa-sidebar{width:210px;flex-basis:210px}.qa-content{padding:28px 24px}.qa-topbar{padding:0 24px}.qa-kpis{grid-template-columns:repeat(2,1fr)}}
@media(max-width:850px){.qa-sidebar{width:68px;flex-basis:68px;padding:20px 9px}.qa-brand{padding:0 5px 30px}.qa-brand>span:last-child,.qa-workspace-label,.qa-nav-item:not(.active){font-size:0}.qa-brand>span:last-child{display:none}.qa-nav-item{justify-content:center;padding:0}.qa-nav-item .qa-icon{font-size:17px}.qa-nav-count,.qa-sidebar-bottom .qa-help-card,.qa-user>div:not(.qa-avatar),.qa-more{display:none}.qa-user{justify-content:center}.qa-grid-main,.qa-grid-bottom{grid-template-columns:1fr}.qa-breakdown-panel{min-height:0}.qa-severity-wrap{justify-content:flex-start;padding-left:10%}}
@media(max-width:560px){.qa-topbar{height:58px;padding:0 14px}.qa-system{display:none}.qa-content{padding:23px 13px}.qa-heading{align-items:flex-start;flex-direction:column}.qa-heading h1{font-size:24px}.qa-heading-actions{width:100%}.qa-heading-actions select{flex:1}.qa-primary{min-height:38px}.qa-kpis{gap:9px}.qa-kpi{padding:13px;min-height:132px}.qa-kpi-top{font-size:9px}.qa-kpi>strong{font-size:24px}.qa-kpi-foot{font-size:8px;display:block}.qa-kpi-foot span{display:block;margin-top:3px}.qa-panel{padding:14px}.qa-chart-legend{margin-left:20px}.qa-severity-wrap{gap:15px;padding-left:0}.qa-donut{width:112px;height:112px}.qa-footer{flex-direction:column}.qa-table-tools{flex-direction:column}.qa-table-tools select{width:100%}}
`;
