/**
 * APEX FUSION BOT — Dashboard Web
 * Express server que sirve el panel de control
 */

const express = require('express');
const path    = require('path');
const app     = express();

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Importar módulos del bot (si están disponibles)
let controller, scanner;
try {
  controller = require('../signals/tradeController');
  scanner    = require('../signals/scanner');
} catch {}

// ─── API Endpoints ─────────────────────────────────────────────

app.get('/api/status', async (req, res) => {
  res.json({
    mode: process.env.MODE || 'paper',
    uptime: process.uptime(),
    openTrades: 0,
    symbolsScanned: 0,
    lastScan: new Date().toISOString(),
  });
});

app.get('/api/stats', (req, res) => {
  res.json({ total: 0, wins: 0, losses: 0, pnl: 0 });
});

// ─── HTML Dashboard ────────────────────────────────────────────
app.get('/', (req, res) => {
  res.send(getDashboardHTML());
});

function getDashboardHTML() {
  return `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>APEX FUSION BOT</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;700&family=Orbitron:wght@400;700;900&display=swap');
    
    :root {
      --bg: #050810;
      --panel: #0a0f1a;
      --border: #1a2540;
      --accent: #00e5aa;
      --red: #ff3b5c;
      --gold: #ffd700;
      --blue: #00cfff;
      --text: #c8d8f0;
      --dim: #5a6a80;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'JetBrains Mono', monospace;
      min-height: 100vh;
      overflow-x: hidden;
    }

    .grid-bg {
      position: fixed; inset: 0;
      background-image: 
        linear-gradient(rgba(0,229,170,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,229,170,0.03) 1px, transparent 1px);
      background-size: 40px 40px;
      pointer-events: none;
    }

    header {
      border-bottom: 1px solid var(--border);
      padding: 20px 40px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: rgba(10,15,26,0.9);
      backdrop-filter: blur(10px);
      position: sticky; top: 0; z-index: 100;
    }

    .logo {
      font-family: 'Orbitron', monospace;
      font-size: 1.4rem;
      font-weight: 900;
      color: var(--accent);
      letter-spacing: 2px;
    }

    .logo span { color: var(--gold); }

    .status-pill {
      display: flex; align-items: center; gap: 8px;
      background: rgba(0,229,170,0.1);
      border: 1px solid rgba(0,229,170,0.3);
      border-radius: 20px;
      padding: 6px 16px;
      font-size: 0.75rem;
    }

    .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--accent);
      animation: pulse 2s infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(1.3); }
    }

    main {
      padding: 40px;
      max-width: 1400px;
      margin: 0 auto;
    }

    .grid-3 {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
      margin-bottom: 30px;
    }

    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 30px;
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      position: relative;
      overflow: hidden;
    }

    .card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 2px;
      background: linear-gradient(90deg, var(--accent), transparent);
    }

    .card-label {
      font-size: 0.65rem;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: var(--dim);
      margin-bottom: 12px;
    }

    .card-value {
      font-family: 'Orbitron', monospace;
      font-size: 2rem;
      font-weight: 700;
      color: var(--accent);
    }

    .card-sub {
      font-size: 0.75rem;
      color: var(--dim);
      margin-top: 6px;
    }

    .score-bar {
      height: 4px;
      background: var(--border);
      border-radius: 2px;
      margin-top: 16px;
      overflow: hidden;
    }

    .score-fill {
      height: 100%;
      border-radius: 2px;
      background: linear-gradient(90deg, var(--accent), var(--blue));
      transition: width 0.5s ease;
    }

    .panel-title {
      font-family: 'Orbitron', monospace;
      font-size: 0.85rem;
      letter-spacing: 2px;
      color: var(--accent);
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .panel-title::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--border);
    }

    .trade-row {
      display: grid;
      grid-template-columns: 140px 80px 100px 100px 1fr;
      gap: 12px;
      padding: 14px 0;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      font-size: 0.8rem;
      align-items: center;
    }

    .trade-row:last-child { border-bottom: none; }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 10px;
      border-radius: 4px;
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 1px;
    }

    .badge-long  { background: rgba(0,229,170,0.15); color: var(--accent); border: 1px solid rgba(0,229,170,0.3); }
    .badge-short { background: rgba(255,59,92,0.15);  color: var(--red);    border: 1px solid rgba(255,59,92,0.3); }
    .badge-prime { background: rgba(255,215,0,0.15);  color: var(--gold);   border: 1px solid rgba(255,215,0,0.4); }

    .pnl-pos { color: var(--accent); }
    .pnl-neg { color: var(--red); }

    .signal-feed {
      max-height: 360px;
      overflow-y: auto;
    }

    .signal-item {
      padding: 12px 0;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      font-size: 0.78rem;
    }

    .signal-time { color: var(--dim); font-size: 0.68rem; }

    .no-trades {
      text-align: center;
      padding: 40px;
      color: var(--dim);
      font-size: 0.8rem;
    }

    .mode-badge {
      padding: 4px 12px;
      border-radius: 4px;
      font-size: 0.7rem;
      letter-spacing: 2px;
      text-transform: uppercase;
    }

    .mode-paper { background: rgba(255,215,0,0.15); color: var(--gold); border: 1px solid rgba(255,215,0,0.3); }
    .mode-live  { background: rgba(0,229,170,0.15); color: var(--accent); border: 1px solid rgba(0,229,170,0.3); }

    footer {
      text-align: center;
      padding: 30px;
      color: var(--dim);
      font-size: 0.7rem;
      border-top: 1px solid var(--border);
      margin-top: 20px;
    }

    @media (max-width: 768px) {
      main { padding: 20px; }
      .grid-3 { grid-template-columns: 1fr; }
      .grid-2 { grid-template-columns: 1fr; }
      .trade-row { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <div class="grid-bg"></div>

  <header>
    <div class="logo">APEX <span>FUSION</span> BOT</div>
    <div style="display:flex;gap:12px;align-items:center;">
      <div id="mode-badge" class="mode-badge mode-paper">PAPER</div>
      <div class="status-pill">
        <div class="dot"></div>
        <span id="status-text">Activo</span>
      </div>
    </div>
  </header>

  <main>
    <!-- KPI Cards -->
    <div class="grid-3">
      <div class="card">
        <div class="card-label">Trades Abiertos</div>
        <div class="card-value" id="open-trades">0</div>
        <div class="card-sub" id="max-trades">máx. 5</div>
        <div class="score-bar"><div class="score-fill" id="trades-bar" style="width:0%"></div></div>
      </div>
      <div class="card">
        <div class="card-label">PnL Total (USDT)</div>
        <div class="card-value" id="pnl-total" style="color:var(--accent)">+0.00</div>
        <div class="card-sub" id="win-rate">Win Rate: 0%</div>
      </div>
      <div class="card">
        <div class="card-label">Pares Escaneados</div>
        <div class="card-value" id="pairs-scanned">—</div>
        <div class="card-sub" id="last-scan">Último scan: —</div>
      </div>
    </div>

    <div class="grid-2">
      <!-- Posiciones Activas -->
      <div class="card">
        <div class="panel-title">Posiciones Activas</div>
        <div id="positions-list">
          <div class="no-trades">Sin posiciones abiertas</div>
        </div>
      </div>

      <!-- Feed de Señales -->
      <div class="card">
        <div class="panel-title">Feed de Señales</div>
        <div class="signal-feed" id="signal-feed">
          <div class="no-trades">Esperando señales...</div>
        </div>
      </div>
    </div>

    <!-- Stats -->
    <div class="card">
      <div class="panel-title">Estadísticas de Sesión</div>
      <div class="grid-3" style="margin:0;">
        <div>
          <div class="card-label">Total Trades</div>
          <div style="font-size:1.5rem;font-weight:700;color:var(--blue)" id="stat-total">0</div>
        </div>
        <div>
          <div class="card-label">Ganados / Perdidos</div>
          <div style="font-size:1.2rem;font-weight:700;">
            <span style="color:var(--accent)" id="stat-wins">0</span>
            <span style="color:var(--dim)"> / </span>
            <span style="color:var(--red)" id="stat-losses">0</span>
          </div>
        </div>
        <div>
          <div class="card-label">Mejor / Peor Trade</div>
          <div style="font-size:0.9rem;">
            <span class="pnl-pos" id="stat-best">+0.00</span>
            <span style="color:var(--dim)"> / </span>
            <span class="pnl-neg" id="stat-worst">0.00</span>
          </div>
        </div>
      </div>
    </div>
  </main>

  <footer>
    APEX FUSION BOT v1.0 · Sniper VSA V8 × QF Machine v3.1 · BingX Perpetual Futures
  </footer>

  <script>
    const signals = [];

    async function fetchStatus() {
      try {
        const r = await fetch('/api/status');
        const d = await r.json();
        document.getElementById('open-trades').textContent = d.openTrades || 0;
        document.getElementById('pairs-scanned').textContent = d.symbolsScanned || '—';
        document.getElementById('last-scan').textContent = 'Último: ' + new Date(d.lastScan).toLocaleTimeString();
        const mode = d.mode || 'paper';
        const mb = document.getElementById('mode-badge');
        mb.textContent = mode.toUpperCase();
        mb.className = 'mode-badge mode-' + mode;
      } catch {}
    }

    async function fetchStats() {
      try {
        const r = await fetch('/api/stats');
        const s = await r.json();
        const pnl = (s.pnl || 0).toFixed(2);
        document.getElementById('pnl-total').textContent = (s.pnl >= 0 ? '+' : '') + pnl;
        document.getElementById('pnl-total').style.color = s.pnl >= 0 ? 'var(--accent)' : 'var(--red)';
        const wr = s.total > 0 ? ((s.wins / s.total) * 100).toFixed(1) : 0;
        document.getElementById('win-rate').textContent = 'Win Rate: ' + wr + '%';
        document.getElementById('stat-total').textContent = s.total || 0;
        document.getElementById('stat-wins').textContent = s.wins || 0;
        document.getElementById('stat-losses').textContent = s.losses || 0;
        document.getElementById('stat-best').textContent = '+' + (s.bestTrade || 0).toFixed(2);
        document.getElementById('stat-worst').textContent = (s.worstTrade || 0).toFixed(2);
      } catch {}
    }

    // Simular señal de ejemplo
    function addSignalToFeed(symbol, level, dir, score) {
      const feed = document.getElementById('signal-feed');
      if (feed.querySelector('.no-trades')) feed.innerHTML = '';
      const time = new Date().toLocaleTimeString();
      const item = document.createElement('div');
      item.className = 'signal-item';
      const emoji = {PRIME:'🔱', SUPREMA:'⭐', FUEL:'🔥', STD:'📶'}[level] || '•';
      item.innerHTML = \`
        <div>
          <div>\${emoji} <strong>\${symbol}</strong></div>
          <div class="signal-time">\${time}</div>
        </div>
        <div class="badge badge-\${dir.toLowerCase()}">\${dir}</div>
        <div style="color:var(--blue);font-weight:700">\${score}/100</div>
        <div style="color:var(--dim);font-size:0.7rem">\${level}</div>
      \`;
      feed.prepend(item);
      if (feed.children.length > 20) feed.removeChild(feed.lastChild);
    }

    // Actualizar cada 5 segundos
    setInterval(fetchStatus, 5000);
    setInterval(fetchStats, 10000);
    fetchStatus();
    fetchStats();
  </script>
</body>
</html>`;
}

const PORT = process.env.DASHBOARD_PORT || 3000;
app.listen(PORT, () => {
  console.log(`Dashboard disponible en http://localhost:${PORT}`);
});

module.exports = app;
