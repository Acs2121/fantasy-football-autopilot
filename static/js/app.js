// Fantasy Football Autopilot - Main App Controller

let state = null;
let currentPosFilter = null;
let pendingPickPlayerId = null;
let lastKnownPickNumber = 0;

// ============ Draft Timer ============

let timerSeconds = 90;
let timerRemaining = 0;
let timerInterval = null;
let _timerValueEl = null;

function startTimer() {
    if (timerSeconds <= 0) return;
    stopTimer();
    timerRemaining = timerSeconds;
    document.getElementById('timer-display').style.display = 'block';
    updateTimerDisplay();
    timerInterval = setInterval(() => {
        timerRemaining = Math.max(0, timerRemaining - 1);
        updateTimerDisplay();
        if (timerRemaining <= 0) stopTimer();
    }, 1000);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function updateTimerDisplay() {
    if (!_timerValueEl) _timerValueEl = document.getElementById('timer-value');
    const el = _timerValueEl;
    if (!el) return;
    const m = Math.floor(timerRemaining / 60);
    const s = timerRemaining % 60;
    el.textContent = `${m}:${s.toString().padStart(2, '0')}`;
    el.className = 'value';
    if (timerRemaining <= 10) el.classList.add('timer-danger');
    else if (timerRemaining <= 20) el.classList.add('timer-warning');
}

// ============ API Helpers ============

async function api(path, opts = {}) {
    const resp = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    return resp.json();
}

// ============ Data Fetching ============

async function refreshAll() {
    const data = await api('/api/dashboard');
    state = data.state;
    const recs = data.recommendations;
    const scarcity = data.scarcity;
    const winProb = data.win_probability;

    // Reset timer on each new pick
    if (state.current_pick !== lastKnownPickNumber) {
        lastKnownPickNumber = state.current_pick;
        if (!state.draft_complete) startTimer();
        else stopTimer();
    }

    renderHeader(state);
    if (state.draft_type === 'auction') {
        renderAuctionBoard(state);
    } else {
        renderDraftBoard(state);
    }
    renderAvailableList(state);
    renderRoster(state, winProb);
    renderRecommendations(recs);
    renderScarcity(scarcity);
    renderAuction(state, recs);
}

// ============ Header ============

function teamName(s, index) {
    return (s.espn_team_names && s.espn_team_names[index]) || `Team ${index + 1}`;
}

const _DRAFT_TYPE_LABELS = {
    snake:     'Snake',
    best_ball: 'Best Ball',
    dynasty:   'Dynasty',
    auction:   'Auction',
};

function renderHeader(s) {
    document.getElementById('current-round').textContent = s.current_round;
    document.getElementById('current-pick').textContent = s.current_pick;
    document.getElementById('on-the-clock').textContent = teamName(s, s.current_team_index);

    const indicator = document.getElementById('user-turn-indicator');
    if (s.is_user_pick && !s.draft_complete) {
        indicator.style.display = 'block';
    } else {
        indicator.style.display = 'none';
    }

    // Disable Simulate Draft when it's the user's turn or draft is over
    const simBtn = document.getElementById('btn-simulate');
    if (simBtn) simBtn.disabled = s.is_user_pick || s.draft_complete;

    // Draft type badge
    const badge = document.getElementById('draft-type-badge');
    if (badge) {
        const label = _DRAFT_TYPE_LABELS[s.draft_type] || s.draft_type || 'Snake';
        badge.textContent = label;
        badge.className = `draft-type-badge draft-type-${s.draft_type || 'snake'}`;
    }
}

// ============ Draft Board ============

function renderDraftBoard(s) {
    if (s.picks.length === lastRenderedPickCount) return;
    lastRenderedPickCount = s.picks.length;
    const board = document.getElementById('draft-board');
    const cols = s.num_teams + 1; // +1 for round label column
    board.style.gridTemplateColumns = `50px repeat(${s.num_teams}, 1fr)`;

    let html = '';

    // Header row
    html += `<div class="board-cell header-cell"></div>`;
    for (let t = 0; t < s.num_teams; t++) {
        const isUser = t === s.user_team_index;
        html += `<div class="board-cell header-cell" style="${isUser ? 'color:var(--gold)' : ''}">
            ${isUser ? 'YOU' : teamName(s, t)}
        </div>`;
    }

    // Dynasty: show keeper row above the draft rounds
    const keeperIds = new Set(s.keeper_player_ids || []);
    if (s.draft_type === 'dynasty' && keeperIds.size > 0) {
        html += `<div class="board-cell round-label keeper-label">K</div>`;
        for (let t = 0; t < s.num_teams; t++) {
            const teamRoster = s.team_rosters && s.team_rosters[String(t)] || [];
            const keepers = teamRoster.filter(p => p && keeperIds.has(p.id));
            const isUser = t === s.user_team_index;
            let cellHtml = '';
            for (const p of keepers) {
                cellHtml += `<div class="keeper-chip">
                    <span class="pos-badge ${p.position}">${p.position}</span>
                    <span>${abbreviateName(p.name)}</span>
                </div>`;
            }
            html += `<div class="board-cell keeper-cell${isUser ? ' user-pick' : ''}">${cellHtml}</div>`;
        }
    }

    // Build pick lookup: [round][team] = pick
    const pickGrid = {};
    for (const pick of s.picks) {
        const r = pick.round;
        if (!pickGrid[r]) pickGrid[r] = {};
        pickGrid[r][pick.team_index] = pick;
    }

    // Rows
    for (let r = 1; r <= s.num_rounds; r++) {
        html += `<div class="board-cell round-label">R${r}</div>`;
        for (let t = 0; t < s.num_teams; t++) {
            // Each column always belongs to the same team — picks stack in their own column
            const pick = pickGrid[r] && pickGrid[r][t];
            const isCurrentPick = r === s.current_round &&
                t === s.current_team_index && !s.draft_complete && !pick;

            let classes = 'board-cell';
            if (pick) {
                classes += ' picked';
                if (pick.team_index === s.user_team_index) classes += ' user-pick';
            }
            if (isCurrentPick) classes += ' current-pick';

            if (pick) {
                const p = pick.player;
                html += `<div class="${classes}">
                    <div class="player-name">${abbreviateName(p.name)}</div>
                    <span class="pos-badge ${p.position}">${p.position}</span>
                </div>`;
            } else {
                html += `<div class="${classes}"></div>`;
            }
        }
    }

    board.innerHTML = html;
}

function abbreviateName(name) {
    // "CeeDee Lamb" -> "C. Lamb" for board cells
    const parts = name.split(' ');
    if (parts.length <= 1) return name;
    if (name.includes(' ')) {
        // Keep last name, abbreviate first
        return parts[0][0] + '. ' + parts.slice(1).join(' ');
    }
    return name;
}

// ============ Player Detail Modal ============

const _RADAR_COMPONENTS = [
    { key: 'volume_score',      label: 'Volume',      color: '#6ea8fe' },
    { key: 'efficiency_score',  label: 'Efficiency',  color: '#4caf8a' },
    { key: 'td_score',          label: 'TD Role',     color: '#f0a040' },
    { key: 'team_env_score',    label: 'Team',        color: '#38bdf8' },
    { key: 'consistency_score', label: 'Consist.',    color: '#a78bfa' },
    { key: 'ceiling_score',     label: 'Ceiling',     color: '#c084fc' },
    { key: '_safety',           label: 'Safety',      color: '#4ade80' },  // 1 - risk
];

function showPlayerDetail(playerId) {
    const player = cachedAvailable.find(p => p.id === playerId);
    if (!player) return;

    const hasStats = player.volume_score != null;

    // Headshot
    const hsEl = document.getElementById('pd-headshot');
    if (player.headshot_url) {
        hsEl.innerHTML = `<img src="${player.headshot_url}" alt="${player.name}" loading="lazy">`;
        hsEl.style.border = '2px solid var(--border)';
    } else {
        hsEl.innerHTML = `<div class="headshot-placeholder ${player.position}">${player.position}</div>`;
        hsEl.style.border = 'none';
    }

    document.getElementById('pd-pos-badge').textContent = player.position;
    document.getElementById('pd-pos-badge').className = `pos-badge ${player.position}`;
    document.getElementById('pd-name').textContent = player.name;
    document.getElementById('pd-meta').textContent =
        `${player.team} | Bye ${player.bye_week} | ADP ${player.adp || '—'}`;

    document.getElementById('pd-proj-pts').textContent =
        hasStats ? `${player.base_value} pts` : `${player.projected_points} pts`;

    const vbdEl = document.getElementById('pd-vbd');
    vbdEl.textContent = `${player.vbd_score > 0 ? '+' : ''}${player.vbd_score}`;
    vbdEl.style.color = player.vbd_score > 0 ? 'var(--green)' : 'var(--red)';

    const ageCard = document.getElementById('pd-age-card');
    if (player.age) {
        document.getElementById('pd-age').textContent = `${player.age}`;
        ageCard.style.display = '';
    } else {
        ageCard.style.display = 'none';
    }

    const gamesCard = document.getElementById('pd-games-card');
    if (hasStats) {
        document.getElementById('pd-games').textContent =
            player.games_played_2024 ? `${player.games_played_2024}` : '—';
        gamesCard.style.display = '';
        drawRadarChart(player);
        document.getElementById('pd-radar-wrap').style.display = '';
        document.getElementById('pd-no-stats').style.display = 'none';
    } else {
        gamesCard.style.display = 'none';
        document.getElementById('pd-radar-wrap').style.display = 'none';
        document.getElementById('pd-no-stats').style.display = '';
    }

    const isAuction = state && state.draft_type === 'auction';
    const draftBtn = document.getElementById('pd-draft-btn');
    draftBtn.textContent = isAuction ? 'Nominate' : 'Draft Player';
    draftBtn.onclick = () => {
        closePlayerDetail();
        if (isAuction) selectAuctionNomPlayer(playerId, player.name);
        else confirmPick(playerId);
    };

    document.getElementById('player-detail-modal').style.display = 'flex';
}

function closePlayerDetail() {
    document.getElementById('player-detail-modal').style.display = 'none';
}

function drawRadarChart(player) {
    const svg = document.getElementById('pd-radar');
    const cx = 100, cy = 100, R = 72, labelR = R + 20;
    const n = _RADAR_COMPONENTS.length;
    const step = (2 * Math.PI) / n;
    const start = -Math.PI / 2;

    const values = _RADAR_COMPONENTS.map(c =>
        c.key === '_safety'
            ? Math.max(0, 1 - (player.risk_score || 0))
            : Math.min(1, Math.max(0, player[c.key] || 0))
    );

    const pt = (i, frac) => ({
        x: cx + R * frac * Math.cos(start + i * step),
        y: cy + R * frac * Math.sin(start + i * step),
    });

    const parts = [];

    // Background grid rings
    [0.25, 0.5, 0.75, 1.0].forEach(frac => {
        const pts = Array.from({length: n}, (_, i) => pt(i, frac));
        const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('') + 'Z';
        parts.push(`<path d="${d}" fill="none" stroke="rgba(255,255,255,${frac === 1 ? 0.15 : 0.07})" stroke-width="1"/>`);
    });

    // Axis spokes
    for (let i = 0; i < n; i++) {
        const e = pt(i, 1);
        parts.push(`<line x1="${cx}" y1="${cy}" x2="${e.x.toFixed(1)}" y2="${e.y.toFixed(1)}" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>`);
    }

    // Filled polygon
    const dataPts = values.map((v, i) => pt(i, v));
    const dataPath = dataPts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('') + 'Z';
    parts.push(`<path d="${dataPath}" fill="rgba(108,99,255,0.25)" stroke="#6c63ff" stroke-width="2" stroke-linejoin="round"/>`);

    // Data dots
    dataPts.forEach((p, i) =>
        parts.push(`<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="${_RADAR_COMPONENTS[i].color}"/>`)
    );

    // Labels + percentages
    for (let i = 0; i < n; i++) {
        const angle = start + i * step;
        const lx = cx + labelR * Math.cos(angle);
        const ly = cy + labelR * Math.sin(angle);
        const pct = Math.round(values[i] * 100);
        const anchor = lx < cx - 5 ? 'end' : lx > cx + 5 ? 'start' : 'middle';
        const c = _RADAR_COMPONENTS[i];
        parts.push(`<text x="${lx.toFixed(1)}" y="${(ly - 4).toFixed(1)}" text-anchor="${anchor}" fill="${c.color}" font-size="8" font-weight="700">${c.label}</text>`);
        parts.push(`<text x="${lx.toFixed(1)}" y="${(ly + 6).toFixed(1)}" text-anchor="${anchor}" fill="rgba(255,255,255,0.5)" font-size="7">${pct}%</text>`);
    }

    svg.innerHTML = parts.join('');
}

// ============ Available Players (Virtual Scroll) ============

const VC_CARD_H  = 140; // fixed card height px
const VC_GAP     = 6;   // gap between cards px
const VC_ROW_H   = VC_CARD_H + VC_GAP;
const VC_COLS    = 2;
const VC_BUFFER  = 4;   // extra rows to render above/below viewport

let _vsPlayers       = [];
let _vsLastFirstRow  = -1;
let _vsLastLastRow   = -1;
let _vsRafPending    = false;

function renderAvailableList() {
    if (!state) return;
    const search = document.getElementById('search-player').value;
    const key = `${cachedAvailable.length}_${currentPosFilter}_${search}`;
    if (key === _lastAvailableKey) return;
    _lastAvailableKey = key;
    _vsPlayers = state.draft_complete ? [] : getFilteredAvailable();
    _vsLastFirstRow = -1; // force re-render
    _vsLastLastRow  = -1;
    _renderVirtualCards();
}

function _renderVirtualCards() {
    const container = document.getElementById('available-list');
    if (!container) return;

    if (!_vsPlayers.length) {
        container.innerHTML = '<p style="padding:20px;text-align:center;color:var(--text-dim);grid-column:1/-1">No players available</p>';
        return;
    }

    const totalRows   = Math.ceil(_vsPlayers.length / VC_COLS);
    const totalHeight = totalRows * VC_ROW_H;
    const scrollTop   = container.scrollTop;
    const viewH       = container.clientHeight || 600;

    const firstRow = Math.max(0, Math.floor(scrollTop / VC_ROW_H) - VC_BUFFER);
    const lastRow  = Math.min(totalRows - 1, Math.ceil((scrollTop + viewH) / VC_ROW_H) + VC_BUFFER);

    if (firstRow === _vsLastFirstRow && lastRow === _vsLastLastRow) return;
    _vsLastFirstRow = firstRow;
    _vsLastLastRow  = lastRow;

    const isAuction = state && state.draft_type === 'auction';
    const btnLabel  = isAuction ? 'Nominate' : 'Draft';

    let cards = '';
    for (let r = firstRow; r <= lastRow; r++) {
        for (let c = 0; c < VC_COLS; c++) {
            const idx = r * VC_COLS + c;
            if (idx >= _vsPlayers.length) break;
            const p = _vsPlayers[idx];
            const top  = r * VC_ROW_H;
            const left = c === 0 ? '0' : `calc(50% + ${VC_GAP / 2}px)`;
            const actionFn = isAuction
                ? `selectAuctionNomPlayer(${p.id}, '${p.name.replace(/'/g, "\\'")}')`
                : `confirmPick(${p.id})`;
            const hasStats = p.volume_score != null;
            const vol  = hasStats ? (p.volume_score  * 100).toFixed(0) : 0;
            const eff  = hasStats ? (p.efficiency_score * 100).toFixed(0) : 0;
            const td   = hasStats ? (p.td_score  * 100).toFixed(0) : 0;
            const ceil = hasStats ? (p.ceiling_score * 100).toFixed(0) : 0;
            const pts  = hasStats ? p.base_value : p.projected_points;
            const vbdColor = p.vbd_score > 0 ? 'var(--green)' : 'var(--red)';
            const headshot = p.headshot_url
                ? `<div class="pc-headshot"><img src="${p.headshot_url}" width="40" height="40" loading="lazy" decoding="async" alt="${p.name}"></div>`
                : `<div class="pc-headshot-placeholder ${p.position}">${p.position === 'DST' ? 'D' : p.position[0]}</div>`;
            cards += `<div class="player-card" style="position:absolute;top:${top}px;left:${left};width:calc(50% - ${VC_GAP / 2}px)" onclick="showPlayerDetail(${p.id})">
                <div class="pc-top">
                    <span class="pc-rank">${idx + 1}</span>
                    <span class="pos-badge ${p.position}">${p.position}${p.positional_rank || ''}</span>
                </div>
                <div class="pc-body">${headshot}<div class="pc-info"><div class="pc-name">${p.name}</div><div class="pc-meta">${p.team} · Bye ${p.bye_week}</div></div></div>
                <div class="pc-bars">
                    <span class="cb" title="Vol ${vol}%"><span class="cb-fill cb-vol" style="width:${vol}%"></span></span>
                    <span class="cb" title="Eff ${eff}%"><span class="cb-fill cb-eff" style="width:${eff}%"></span></span>
                    <span class="cb" title="TD ${td}%"><span class="cb-fill cb-td" style="width:${td}%"></span></span>
                    <span class="cb" title="Ceil ${ceil}%"><span class="cb-fill cb-ceil" style="width:${ceil}%"></span></span>
                </div>
                <div class="pc-stats"><span class="pc-pts">${pts} pts</span><span class="pc-vbd" style="color:${vbdColor}">VBD ${p.vbd_score > 0 ? '+' : ''}${p.vbd_score}</span></div>
                <button class="pc-draft-btn" onclick="event.stopPropagation();${actionFn}">${btnLabel}</button>
            </div>`;
        }
    }

    container.innerHTML = `<div style="position:relative;height:${totalHeight}px">${cards}</div>`;
}

function _onVsScroll() {
    if (_vsRafPending) return;
    _vsRafPending = true;
    requestAnimationFrame(() => {
        _vsRafPending = false;
        if (_vsPlayers.length) _renderVirtualCards();
    });
}

function getFilteredAvailable() {
    if (!state) return [];
    const search = document.getElementById('search-player').value.toLowerCase();

    // Get available players from state
    let players = [];
    // We need to fetch from the available endpoint with the current filter
    // For now, use cached data - we'll fetch fresh on filter change
    return cachedAvailable.filter(p => {
        if (currentPosFilter && p.position !== currentPosFilter) return false;
        if (search && !p.name.toLowerCase().includes(search) && !p.team.toLowerCase().includes(search)) return false;
        return true;
    });
}

let cachedAvailable = [];
let lastRenderedPickCount = -1;
let _lastAvailableKey = '';

async function fetchAvailable() {
    cachedAvailable = await api('/api/available?limit=100');
    window._availablePlayers = cachedAvailable;  // keeper modal uses this
    renderAvailableList();
}

function filterPosition(pos, btn) {
    currentPosFilter = pos;
    document.querySelectorAll('.pos-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderAvailableList();
}

function filterPlayers() {
    renderAvailableList();
}

// ============ Roster ============

function renderRoster(s, winProb) {
    const container = document.getElementById('user-roster');
    const slots = s.roster_slots;
    const roster = s.user_roster;

    let html = '';

    if (s.draft_type === 'best_ball') {
        // Best ball: lineup is auto-optimized weekly — skip slot layout, show all players by pts
        if (roster.length === 0) {
            html = '<div class="roster-slot empty"><span class="slot-player" style="color:var(--text-dim)">No players yet</span></div>';
        } else {
            const sorted = [...roster].sort((a, b) => b.projected_points - a.projected_points);
            for (const p of sorted) {
                html += `<div class="roster-slot">
                    <span class="slot-label"><span class="pos-badge ${p.position}">${p.position}</span></span>
                    <span class="slot-player">${p.name}</span>
                    <span class="slot-pts">${p.projected_points} pts</span>
                </div>`;
            }
        }
        html = `<div class="bb-roster-note">Auto-optimized lineup weekly</div>` + html;
    } else {
        // Standard positional slot layout (snake / dynasty)
        const byPos = {};
        for (const p of roster) {
            if (!byPos[p.position]) byPos[p.position] = [];
            byPos[p.position].push(p);
        }

        const slotOrder = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST'];
        for (const pos of slotOrder) {
            const needed = slots[pos] || 0;
            const players = byPos[pos] || [];
            for (let i = 0; i < needed; i++) {
                const p = players[i];
                if (p) {
                    html += `<div class="roster-slot">
                        <span class="slot-label"><span class="pos-badge ${pos}">${pos}</span></span>
                        <span class="slot-player">${p.name}</span>
                        <span class="slot-pts">${p.projected_points} pts</span>
                    </div>`;
                } else {
                    html += `<div class="roster-slot empty">
                        <span class="slot-label"><span class="pos-badge ${pos}">${pos}</span></span>
                        <span class="slot-player">Empty</span>
                        <span class="slot-pts">-</span>
                    </div>`;
                }
            }
        }

        // Bench
        const benchCount = slots.BN || 0;
        const allStarters = slotOrder.reduce((sum, pos) => sum + (slots[pos] || 0), 0);
        const benchPlayers = roster.slice(allStarters);
        for (let i = 0; i < benchCount; i++) {
            const p = benchPlayers[i];
            if (p) {
                html += `<div class="roster-slot">
                    <span class="slot-label" style="color:var(--text-dim)">BN</span>
                    <span class="slot-player">${p.name}</span>
                    <span class="slot-pts">${p.projected_points} pts</span>
                </div>`;
            } else {
                html += `<div class="roster-slot empty">
                    <span class="slot-label" style="color:var(--text-dim)">BN</span>
                    <span class="slot-player">Empty</span>
                    <span class="slot-pts">-</span>
                </div>`;
            }
        }
    }

    container.innerHTML = html;

    // Win probability
    const prob = winProb.win_probability || 50;
    document.getElementById('win-prob-value').textContent = `${prob}%`;
    document.getElementById('win-prob-fill').style.width = `${prob}%`;
    document.getElementById('projected-pts').textContent =
        `Projected: ${winProb.projected_points || 0} pts (League Avg: ${winProb.league_avg || 0})`;

    // Color the win prob value
    const wpEl = document.getElementById('win-prob-value');
    if (prob >= 60) wpEl.style.color = 'var(--green)';
    else if (prob >= 45) wpEl.style.color = 'var(--yellow)';
    else wpEl.style.color = 'var(--red)';
}

// ============ Recommendations ============

function renderRecommendations(recs) {
    if (!recs.is_auction) {
        // Reset labels to snake defaults in case we switched from auction
        document.getElementById('rec-best-label').textContent     = 'Best Available';
        document.getElementById('rec-need-label').textContent     = 'Best by Need';
        document.getElementById('rec-sleepers-label').textContent = 'Sleeper Picks';
    }
    renderRecCards('rec-best', recs.best_overall, false);
    renderRecCards('rec-need', recs.best_by_need, false);
    renderRecCards('rec-sleepers', recs.sleeper_picks, !recs.is_auction);
}

function renderRecCards(containerId, players, isSleeper) {
    const container = document.getElementById(containerId);
    if (!players || !players.length) {
        container.innerHTML = '<div style="font-size:11px;color:var(--text-dim);padding:4px">None available</div>';
        return;
    }

    let html = '';
    for (const p of players) {
        const scoreLabel = p.need_score !== undefined ? p.need_score : p.vbd_score;
        html += `<div class="rec-card ${isSleeper ? 'sleeper' : ''}" onclick="confirmPick(${p.id})">
            <div class="rec-name">${p.name}</div>
            <div class="rec-meta">
                <span class="pos-badge ${p.position}" style="font-size:9px">${p.position}</span>
                ${p.team}
            </div>
            <div class="rec-score">${scoreLabel > 0 ? '+' : ''}${scoreLabel}</div>
            ${isSleeper ? `<div class="rec-meta" style="color:var(--yellow)">Sleeper: ${(p.sleeper_score * 100).toFixed(0)}%</div>` : ''}
        </div>`;
    }
    container.innerHTML = html;
}

// ============ Scarcity ============

function renderScarcity(scarcity) {
    const container = document.getElementById('scarcity-chart');
    const alertsContainer = document.getElementById('scarcity-alerts');
    const positions = ['QB', 'RB', 'WR', 'TE', 'K', 'DST'];
    const colors = {
        QB: 'var(--qb)', RB: 'var(--rb)', WR: 'var(--wr)',
        TE: 'var(--te)', K: 'var(--k)', DST: 'var(--dst)'
    };

    let html = '';
    let alerts = '';

    for (const pos of positions) {
        const data = scarcity[pos];
        if (!data) continue;

        const pct = Math.round(data.scarcity_index * 100);
        html += `<div class="scarcity-row">
            <span class="pos-label" style="color:${colors[pos]}">${pos}</span>
            <div class="scarcity-bar-bg">
                <div class="scarcity-bar-fill" style="width:${pct}%;background:${colors[pos]}"></div>
                <span class="scarcity-bar-text">${data.available} left (${pct}% gone)</span>
            </div>
        </div>`;

        if (data.alert) {
            alerts += `<div class="scarcity-alert">${data.alert}</div>`;
        }
    }

    container.innerHTML = html;
    alertsContainer.innerHTML = alerts;
}

// ============ Actions ============

function confirmPick(playerId) {
    if (!state) return;

    // Find the player
    let player = cachedAvailable.find(p => p.id === playerId);
    if (!player) {
        // Search in recommendations or state
        for (const p of state.user_roster) {
            if (p.id === playerId) return; // already drafted
        }
        return;
    }

    pendingPickPlayerId = playerId;
    const modal = document.getElementById('pick-modal');
    document.getElementById('pick-modal-player').innerHTML = `
        <div class="pick-name">${player.name}</div>
        <div class="pick-details">
            <span class="pos-badge ${player.position}">${player.position}</span>
            ${player.team} | ${player.projected_points} pts | VBD: ${player.vbd_score}
        </div>
    `;
    document.getElementById('pick-modal-team-name').textContent =
        state.current_team_index === state.user_team_index
            ? 'Your Team'
            : teamName(state, state.current_team_index);

    document.getElementById('btn-confirm-pick').onclick = () => executePick(playerId);
    modal.style.display = 'flex';
}

function closePickModal() {
    document.getElementById('pick-modal').style.display = 'none';
    pendingPickPlayerId = null;
}

async function executePick(playerId) {
    closePickModal();
    await api('/api/pick', {
        method: 'POST',
        body: JSON.stringify({ player_id: playerId }),
    });
    await fetchAvailable();
    await refreshAll();
}

async function undoPick() {
    await api('/api/undo', { method: 'POST' });
    await fetchAvailable();
    await refreshAll();
}

async function resetDraft() {
    if (!confirm('Reset the entire draft? This cannot be undone.')) return;
    stopTimer();
    lastKnownPickNumber = 0;
    lastRenderedPickCount = -1;
    await api('/api/reset', { method: 'POST' });
    await fetchAvailable();
    await refreshAll();
}

async function simCPUPicks() {
    if (!state || state.draft_complete || state.is_user_pick) return;
    const btn = document.getElementById('btn-simulate');
    btn.disabled = true;
    btn.textContent = 'Simulating...';

    // Keep simulating until it's the user's turn or draft ends
    while (true) {
        const s = await api('/api/state');
        if (s.draft_complete || s.is_user_pick) break;
        const result = await api('/api/simulate', { method: 'POST' });
        if (result.error) break;
        await new Promise(r => setTimeout(r, 120));
    }

    await fetchAvailable();
    await refreshAll();
    btn.disabled = false;
    btn.textContent = 'Simulate Draft';
}

function exportRoster() {
    if (!state || !state.user_roster || !state.user_roster.length) {
        alert('No players drafted yet.');
        return;
    }
    const lines = ['Position,Player,Team,Projected Points'];
    for (const p of state.user_roster) {
        lines.push(`${p.position},"${p.name}",${p.team},${p.projected_points}`);
    }
    const csv = lines.join('\n');

    navigator.clipboard.writeText(csv).then(() => {
        const btn = document.getElementById('btn-export');
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
    }).catch(() => {
        // Fallback: trigger download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'my_roster.csv';
        a.click();
        URL.revokeObjectURL(url);
    });
}

// ============ Settings ============

function onDraftTypeChange() {
    const dtype = document.getElementById('set-draft-type').value;
    // Snake toggle applies to snake and dynasty (not auction which has its own nomination order)
    const showSnake = dtype === 'snake' || dtype === 'dynasty';
    document.getElementById('snake-toggle-row').style.display = showSnake ? '' : 'none';
    // Dynasty-specific rows
    const isDynasty = dtype === 'dynasty';
    document.getElementById('dynasty-row').style.display = isDynasty ? '' : 'none';
    const keeperBtn = document.getElementById('btn-open-keepers');
    if (keeperBtn) keeperBtn.style.display = isDynasty ? '' : 'none';
}

function openSettings() {
    const modal = document.getElementById('settings-modal');
    modal.style.display = 'flex';

    // Pre-populate all fields from current draft state
    if (state) {
        document.getElementById('set-teams').value = state.num_teams;
        document.getElementById('set-rounds').value = state.num_rounds;
        document.getElementById('set-snake').checked = state.snake;
        document.getElementById('set-draft-type').value = state.draft_type || 'snake';
    }

    onDraftTypeChange();
    updatePositionOptions();

    // Set position after options are generated
    if (state) {
        document.getElementById('set-position').value = state.user_team_index;
    }
}

function closeSettings() {
    document.getElementById('settings-modal').style.display = 'none';
}

function updatePositionOptions() {
    const teams = parseInt(document.getElementById('set-teams').value);
    const sel = document.getElementById('set-position');
    const prev = parseInt(sel.value) || 0;
    sel.innerHTML = '';
    for (let i = 1; i <= teams; i++) {
        const opt = document.createElement('option');
        opt.value = i - 1;
        opt.textContent = `Pick #${i}`;
        sel.appendChild(opt);
    }
    // Preserve previous position if still valid after team count change
    sel.value = Math.min(prev, teams - 1);
}

document.getElementById('set-teams').addEventListener('change', updatePositionOptions);

async function applySettings() {
    timerSeconds = parseInt(document.getElementById('set-timer').value) || 0;
    stopTimer();
    lastKnownPickNumber = 0;
    lastRenderedPickCount = -1;

    const draftType = document.getElementById('set-draft-type').value;
    const config = {
        draft_type: draftType,
        num_teams: parseInt(document.getElementById('set-teams').value),
        user_team_index: parseInt(document.getElementById('set-position').value),
        num_rounds: parseInt(document.getElementById('set-rounds').value),
        snake: document.getElementById('set-snake').checked,
    };
    await api('/api/configure', {
        method: 'POST',
        body: JSON.stringify(config),
    });
    closeSettings();
    await fetchAvailable();
    await refreshAll();

    // Hide timer display if disabled
    if (timerSeconds <= 0) document.getElementById('timer-display').style.display = 'none';
}

// ============ Dynasty / Keeper ============

// Keeper state: { teamIndex: Set<playerId> }
let keeperAssignments = {};

function openKeepersModal() {
    if (!state) return;
    const numTeams = parseInt(document.getElementById('set-teams').value) || state.num_teams;
    const keeperSlots = parseInt(document.getElementById('set-keeper-slots').value) || 2;

    // Build the UI: one section per team, each with a searchable player list
    const container = document.getElementById('keepers-team-list');
    let html = '';
    for (let t = 0; t < numTeams; t++) {
        const isUser = t === (parseInt(document.getElementById('set-position').value) || 0);
        const label = isUser ? `Team ${t + 1} (YOU)` : `Team ${t + 1}`;
        html += `<div class="keeper-team-block">
            <div class="keeper-team-label">${label} — pick up to ${keeperSlots} keeper${keeperSlots !== 1 ? 's' : ''}</div>
            <input class="keeper-search" type="text" placeholder="Search player…"
                oninput="filterKeeperSearch(this, ${t})" />
            <div class="keeper-player-list" id="keeper-list-${t}"></div>
        </div>`;
    }
    container.innerHTML = html;

    // Populate player lists from available + already-assigned keepers
    const allPlayers = (window._availablePlayers || []).concat(
        Object.values(keeperAssignments).flatMap(s => [...s].map(id => state && state.team_rosters &&
            Object.values(state.team_rosters).flat().find(p => p && p.id === id)).filter(Boolean))
    );
    for (let t = 0; t < numTeams; t++) {
        _renderKeeperList(t, allPlayers, keeperSlots);
    }

    document.getElementById('keepers-modal').style.display = 'flex';
}

function _renderKeeperList(teamIdx, players, keeperSlots, filter = '') {
    const el = document.getElementById(`keeper-list-${teamIdx}`);
    if (!el) return;
    const assigned = keeperAssignments[teamIdx] || new Set();
    const assignedElsewhere = new Set(
        Object.entries(keeperAssignments)
            .filter(([t]) => parseInt(t) !== teamIdx)
            .flatMap(([, s]) => [...s])
    );

    const filtered = filter
        ? players.filter(p => p.name.toLowerCase().includes(filter.toLowerCase()))
        : players;

    let html = '';
    for (const p of filtered.slice(0, 40)) {
        const isAssigned = assigned.has(p.id);
        const blockedByOther = assignedElsewhere.has(p.id);
        const atLimit = assigned.size >= keeperSlots && !isAssigned;
        const disabled = (blockedByOther || atLimit) ? 'disabled' : '';
        const cls = isAssigned ? 'keeper-player selected' : blockedByOther ? 'keeper-player taken' : 'keeper-player';
        html += `<div class="${cls}" ${disabled ? '' : `onclick="toggleKeeper(${teamIdx}, ${p.id}, ${keeperSlots})"`}>
            <span class="pos-badge ${p.position}">${p.position}</span>
            <span>${p.name}</span>
            <span style="color:var(--text-dim);font-size:11px;margin-left:auto">${p.projected_points} pts</span>
            ${isAssigned ? '<span class="keeper-check">✓</span>' : ''}
        </div>`;
    }
    el.innerHTML = html || '<div style="color:var(--text-dim);padding:8px">No players found</div>';
}

function toggleKeeper(teamIdx, playerId, keeperSlots) {
    if (!keeperAssignments[teamIdx]) keeperAssignments[teamIdx] = new Set();
    const set = keeperAssignments[teamIdx];
    if (set.has(playerId)) {
        set.delete(playerId);
    } else {
        if (set.size >= keeperSlots) return;
        set.add(playerId);
    }
    // Re-render all teams to reflect cross-team exclusions
    const numTeams = parseInt(document.getElementById('set-teams').value) || state.num_teams;
    const allPlayers = window._availablePlayers || [];
    for (let t = 0; t < numTeams; t++) {
        const searchEl = document.querySelector(`#keeper-list-${t}`);
        const searchInput = searchEl && searchEl.previousElementSibling;
        const filter = searchInput ? searchInput.value : '';
        _renderKeeperList(t, allPlayers, keeperSlots, filter);
    }
}

function filterKeeperSearch(input, teamIdx) {
    const keeperSlots = parseInt(document.getElementById('set-keeper-slots').value) || 2;
    _renderKeeperList(teamIdx, window._availablePlayers || [], keeperSlots, input.value);
}

async function saveKeepers() {
    const payload = {};
    for (const [teamIdx, set] of Object.entries(keeperAssignments)) {
        payload[teamIdx] = [...set];
    }
    const res = await api('/api/keepers', { method: 'POST', body: JSON.stringify({ keepers: payload }) });
    const statusEl = document.getElementById('keepers-status');
    if (res.ok) {
        statusEl.textContent = `${res.total_keepers} keepers saved.`;
        statusEl.style.color = '#4caf8a';
        setTimeout(closeKeepersModal, 800);
    } else {
        statusEl.textContent = res.error || 'Error saving keepers';
        statusEl.style.color = '#e05c5c';
    }
}

function closeKeepersModal() {
    document.getElementById('keepers-modal').style.display = 'none';
}

// ============ Sleeper Refresh ============

async function sleeperRefresh() {
    const btn    = document.getElementById('btn-sleeper-refresh');
    const status = document.getElementById('sleeper-refresh-status');
    btn.disabled = true;
    status.textContent = 'Fetching…';
    status.className   = 'sleeper-status';
    try {
        const res = await api('/api/sleeper/refresh', { method: 'POST', body: JSON.stringify({ year: 2025 }) });
        if (res.ok) {
            status.textContent = `Updated ${res.updated} / ${res.total} players`;
            status.className   = 'sleeper-status sleeper-ok';
            // Re-fetch available list so new ADP/points show immediately
            await fetchAvailable();
            await refreshAll();
        } else {
            status.textContent = `Error: ${res.error}`;
            status.className   = 'sleeper-status sleeper-err';
        }
    } catch (e) {
        status.textContent = 'Network error';
        status.className   = 'sleeper-status sleeper-err';
    } finally {
        btn.disabled = false;
    }
}

async function nflStatsRefresh() {
    const btn    = document.getElementById('btn-nfl-stats-refresh');
    const status = document.getElementById('nfl-stats-status');
    btn.disabled = true;
    status.textContent = 'Fetching NFL stats… (may take 30s)';
    status.className   = 'sleeper-status';
    try {
        const res = await api('/api/stats/refresh', {
            method: 'POST',
            body: JSON.stringify({ year: 2024, scoring_format: 'PPR', force: false }),
        });
        if (res.ok) {
            const fb = res.fallback ? ` (${res.fallback} K/DST use projections)` : '';
            status.textContent = `${res.updated} players scored from NFL data${fb}`;
            status.className   = 'sleeper-status sleeper-ok';
            await fetchAvailable();
            await refreshAll();
        } else {
            status.textContent = `Error: ${res.error}`;
            status.className   = 'sleeper-status sleeper-err';
        }
    } catch (e) {
        status.textContent = 'Network error';
        status.className   = 'sleeper-status sleeper-err';
    } finally {
        btn.disabled = false;
    }
}

// ============ Auction Draft ============

let _auctionNomPlayerId = null;
let _nomBid = 1;

function renderAuction(s, recs) {
    const isAuction = s.draft_type === 'auction';

    // Header status areas
    document.getElementById('standard-status').style.display = isAuction ? 'none' : 'flex';
    const auctionStatus = document.getElementById('auction-status');
    auctionStatus.style.display = isAuction ? 'flex' : 'none';

    // Auction bid panel visibility
    const panel = document.getElementById('auction-bid-panel');
    panel.style.display = isAuction ? '' : 'none';

    if (!isAuction) return;

    // Update header auction status
    const userBudget = s.team_budgets && s.team_budgets[s.user_team_index];
    document.getElementById('auction-budget').textContent = `$${userBudget ?? '—'}`;

    const active = s.active_auction;
    if (active) {
        document.getElementById('auction-nominator').textContent = teamName(s, active.nominator);
        document.getElementById('auction-player-name').textContent =
            active.player ? active.player.name : '—';
        document.getElementById('auction-current-bid').textContent = `$${active.current_bid}`;
        document.getElementById('auction-high-bidder').textContent = teamName(s, active.current_bidder);
    } else {
        document.getElementById('auction-nominator').textContent = '—';
        document.getElementById('auction-player-name').textContent = '—';
        document.getElementById('auction-current-bid').textContent = '$—';
        document.getElementById('auction-high-bidder').textContent = '—';
    }

    // Recommendation labels
    document.getElementById('rec-best-label').textContent = 'Best Value';
    document.getElementById('rec-need-label').textContent = 'Best by Need';
    document.getElementById('rec-sleepers-label').textContent = 'Bargain Targets';

    // Bid guidance in recommendations if there's an active auction
    if (recs && recs.bid_guidance) {
        _renderBidGuidance(recs.bid_guidance, s);
    }

    // Panel UI state
    const nomUI     = document.getElementById('auction-nominate-ui');
    const bidUI     = document.getElementById('auction-bid-ui');
    const waitingUI = document.getElementById('auction-waiting-ui');

    if (active) {
        nomUI.style.display     = 'none';
        waitingUI.style.display = 'none';

        // Does the user need to act?
        const userPassed   = active.passed && active.passed.includes(s.user_team_index);
        const userIsHigh   = active.current_bidder === s.user_team_index;

        if (!userPassed && !userIsHigh) {
            bidUI.style.display = '';
            // Pre-fill bid input to current + 1
            const input = document.getElementById('auction-bid-amount');
            if (parseInt(input.value) <= active.current_bid) {
                input.value = active.current_bid + 1;
            }
            // Fill in bid guidance
            if (recs && recs.bid_guidance) {
                const g = recs.bid_guidance;
                document.getElementById('auction-est-value').textContent = `$${g.est_value}`;
                document.getElementById('auction-max-bid').textContent   = `$${g.max_bid}`;
                const verdictEl = document.getElementById('auction-verdict-row');
                verdictEl.textContent = g.verdict === 'good deal' ? '✓ Good deal at current price'
                    : g.verdict === 'fair' ? '~ Fair price'
                    : '⚠ Overpay risk';
                verdictEl.className = `auction-verdict verdict-${g.verdict.replace(' ', '-')}`;
            }
        } else {
            bidUI.style.display     = 'none';
            waitingUI.style.display = '';
            document.getElementById('auction-waiting-msg').textContent =
                userIsHigh ? `You're the high bidder at $${active.current_bid} — waiting for others`
                           : 'You passed — waiting for auction to close';
        }
    } else if (s.is_user_nomination_turn) {
        nomUI.style.display     = '';
        bidUI.style.display     = 'none';
        waitingUI.style.display = 'none';
        document.getElementById('auction-panel-title').textContent = 'Your Nomination Turn';
        _nomBid = s.min_bid || 1;
        document.getElementById('nom-bid-display').textContent = `$${_nomBid}`;
    } else {
        nomUI.style.display     = 'none';
        bidUI.style.display     = 'none';
        waitingUI.style.display = '';
        document.getElementById('auction-waiting-msg').textContent =
            s.draft_complete ? 'Auction complete!'
            : `${teamName(s, s.current_nomination_team)} is nominating…`;
        document.getElementById('auction-panel-title').textContent = 'Auction';
    }
}

function _renderBidGuidance(guidance, s) {
    // Guidance is shown inline in the bid UI panel; nothing extra needed here
}

function adjustNomBid(delta) {
    const min = state ? (state.min_bid || 1) : 1;
    const max = state ? (state.team_budgets[state.user_team_index] || 200) : 200;
    _nomBid = Math.max(min, Math.min(max, _nomBid + delta));
    document.getElementById('nom-bid-display').textContent = `$${_nomBid}`;
}

function adjustBid(delta) {
    const input = document.getElementById('auction-bid-amount');
    const current = parseInt(input.value) || 1;
    const min = state && state.active_auction ? state.active_auction.current_bid + 1 : 1;
    const max = state ? (state.team_budgets[state.user_team_index] || 200) : 200;
    input.value = Math.max(min, Math.min(max, current + delta));
}

// Called when user clicks a player in Available Players list in auction mode
function selectAuctionNomPlayer(playerId, playerName) {
    if (!state || state.draft_type !== 'auction' || !state.is_user_nomination_turn) return;
    _auctionNomPlayerId = playerId;
    document.getElementById('auction-nominate-player-name').textContent = `Nominating: ${playerName}`;
    document.getElementById('btn-auction-nominate').disabled = false;
}

async function submitNomination() {
    if (!_auctionNomPlayerId) return;
    const res = await api('/api/auction/nominate', {
        method: 'POST',
        body: JSON.stringify({ player_id: _auctionNomPlayerId, opening_bid: _nomBid }),
    });
    _auctionNomPlayerId = null;
    document.getElementById('btn-auction-nominate').disabled = true;
    document.getElementById('auction-nominate-player-name').textContent = 'No player selected';
    await fetchAvailable();
    await refreshAll();
}

async function placeBid() {
    const amount = parseInt(document.getElementById('auction-bid-amount').value);
    const res = await api('/api/auction/bid', {
        method: 'POST',
        body: JSON.stringify({ amount }),
    });
    if (res.error) { alert(res.error); return; }
    await fetchAvailable();
    await refreshAll();
}

async function passBid() {
    const res = await api('/api/auction/pass', { method: 'POST' });
    if (res.error) { alert(res.error); return; }
    await fetchAvailable();
    await refreshAll();
}

function renderAuctionBoard(s) {
    // Auction board: team columns showing players won, prices, budget remaining
    const board = document.getElementById('draft-board');
    board.style.gridTemplateColumns = `repeat(${s.num_teams}, 1fr)`;

    let html = '';
    // Header: team name + budget
    for (let t = 0; t < s.num_teams; t++) {
        const isUser  = t === s.user_team_index;
        const budget  = s.team_budgets ? s.team_budgets[t] : '—';
        const label   = isUser ? 'YOU' : teamName(s, t);
        html += `<div class="board-cell header-cell auction-header-cell" style="${isUser ? 'color:var(--gold)' : ''}">
            <div>${label}</div>
            <div class="auction-budget-chip">$${budget}</div>
        </div>`;
    }

    // Build per-team player lists from auction_results
    const teamPlayers = {};
    for (let t = 0; t < s.num_teams; t++) teamPlayers[t] = [];
    for (const result of (s.auction_results || [])) {
        if (teamPlayers[result.team_index] !== undefined) {
            teamPlayers[result.team_index].push(result);
        }
    }

    // Rows: one row per player slot (num_rounds)
    for (let r = 0; r < s.num_rounds; r++) {
        for (let t = 0; t < s.num_teams; t++) {
            const result = teamPlayers[t][r];
            const isUser = t === s.user_team_index;
            if (result) {
                const p = result.player;
                html += `<div class="board-cell picked${isUser ? ' user-pick' : ''}">
                    <div class="player-name">${abbreviateName(p.name)}</div>
                    <span class="pos-badge ${p.position}">${p.position}</span>
                    <span class="auction-price-tag">$${result.price}</span>
                </div>`;
            } else {
                html += `<div class="board-cell"></div>`;
            }
        }
    }

    board.innerHTML = html;
}

// ============ Tabs ============

function showTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    if (tab === 'board') {
        document.getElementById('board-view').style.display = 'block';
        document.getElementById('available-view').style.display = 'none';
        document.querySelectorAll('.tab')[0].classList.add('active');
    } else {
        document.getElementById('board-view').style.display = 'none';
        document.getElementById('available-view').style.display = 'block';
        document.querySelectorAll('.tab')[1].classList.add('active');
    }
}

// ============ Keyboard Shortcuts ============

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closePickModal();
        closeSettings();
    }
    if (e.ctrlKey && e.key === 'z') {
        e.preventDefault();
        undoPick();
    }
});

// ============ ESPN Sync ============

let espnSyncInterval = null;
let lastKnownEspnPickCount = -1;

function openESPNModal() {
    document.getElementById('espn-modal').style.display = 'flex';
    // Pre-fill league ID if already configured
    api('/api/espn/config').then(cfg => {
        if (cfg.league_id) {
            document.getElementById('espn-league-id').value = cfg.league_id;
        }
    });
    // Refresh current status
    refreshESPNStatus();
}

function closeESPNModal() {
    document.getElementById('espn-modal').style.display = 'none';
}

async function connectESPN() {
    const leagueId = document.getElementById('espn-league-id').value.trim();
    if (!leagueId) {
        showESPNStatus('error', 'Please enter your League ID');
        return;
    }

    showESPNStatus('info', 'Connecting to ESPN...');
    document.getElementById('btn-espn-connect').disabled = true;

    const result = await api('/api/espn/connect', {
        method: 'POST',
        body: JSON.stringify({ league_id: leagueId }),
    });

    document.getElementById('btn-espn-connect').disabled = false;

    if (result.ok) {
        const info = result.league;
        const autoMsg = result.auto_configured
            ? ` Draft auto-configured: ${info.num_teams} teams, pick #${info.user_index + 1}, ${info.rounds} rounds.`
            : '';
        showESPNStatus('success', `Connected! Found: ${info.name}.${autoMsg}`);
        document.getElementById('espn-league-info').style.display = 'block';
        document.getElementById('espn-league-name').textContent = info.name;
        document.getElementById('espn-league-meta').textContent =
            `${info.num_teams} teams · ${info.rounds} rounds · ${info.draft_type} draft · Your pick: #${info.user_index + 1}`;
        document.getElementById('btn-espn-start').style.display = 'inline-block';
        document.getElementById('btn-espn-stop').style.display = 'none';
        updateSyncBadge('connected');

        // Refresh app state since draft was reconfigured
        if (result.auto_configured) {
            lastKnownPickNumber = 0;
            lastRenderedPickCount = -1;
            await fetchAvailable();
            await refreshAll();
        }
    } else {
        showESPNStatus('error', result.error || 'Connection failed');
    }
}

async function startESPNSync() {
    showESPNStatus('info', 'Starting live sync...');
    const result = await api('/api/espn/start', { method: 'POST' });
    if (result.ok) {
        showESPNStatus('success', 'Live sync active! Polling every 5 seconds.');
        document.getElementById('btn-espn-start').style.display = 'none';
        document.getElementById('btn-espn-stop').style.display = 'inline-block';
        updateSyncBadge('live');

        // Poll and refresh the app every 5s while syncing
        lastKnownEspnPickCount = -1;
        if (espnSyncInterval) clearInterval(espnSyncInterval);
        espnSyncInterval = setInterval(async () => {
            const status = await api('/api/espn/status');
            updateSyncBadge(status.status);
            if (status.espn_draft_complete) {
                document.getElementById('espn-draft-complete-notice').style.display = 'block';
            }
            // Only re-render when ESPN has recorded new picks
            if (status.known_picks !== lastKnownEspnPickCount) {
                lastKnownEspnPickCount = status.known_picks;
                await fetchAvailable();
                await refreshAll();
            }
        }, 5000);
    } else {
        showESPNStatus('error', result.msg || 'Failed to start sync');
    }
}

async function stopESPNSync() {
    await api('/api/espn/stop', { method: 'POST' });
    if (espnSyncInterval) {
        clearInterval(espnSyncInterval);
        espnSyncInterval = null;
    }
    document.getElementById('btn-espn-start').style.display = 'inline-block';
    document.getElementById('btn-espn-stop').style.display = 'none';
    showESPNStatus('info', 'Sync stopped.');
    updateSyncBadge('connected');
}

async function refreshESPNStatus() {
    const status = await api('/api/espn/status');
    updateSyncBadge(status.status);

    if (status.espn_draft_complete) {
        document.getElementById('espn-draft-complete-notice').style.display = 'block';
    }

    if (status.status === 'live') {
        document.getElementById('btn-espn-start').style.display = 'none';
        document.getElementById('btn-espn-stop').style.display = 'inline-block';
        showESPNStatus('success', `Live sync active. Picks tracked: ${status.known_picks}`);
    } else if (status.status === 'connected' && status.league) {
        document.getElementById('btn-espn-start').style.display = 'inline-block';
        document.getElementById('btn-espn-stop').style.display = 'none';
        const info = status.league;
        document.getElementById('espn-league-info').style.display = 'block';
        document.getElementById('espn-league-name').textContent = info.name;
        document.getElementById('espn-league-meta').textContent =
            `${info.num_teams} teams · ${info.rounds} rounds`;
    } else if (status.status === 'error') {
        showESPNStatus('error', status.error || 'Unknown error');
    }
}

function updateSyncBadge(status) {
    const badge = document.getElementById('espn-sync-badge');
    const label = document.getElementById('espn-sync-label');
    badge.className = 'sync-badge';

    const map = {
        disconnected: ['sync-disconnected', 'ESPN Sync'],
        connected:    ['sync-connected',    'ESPN Connected'],
        live:         ['sync-live',         'ESPN Live'],
        error:        ['sync-error',        'ESPN Error'],
        syncing:      ['sync-live',         'ESPN Syncing...'],
    };
    const [cls, text] = map[status] || map.disconnected;
    badge.classList.add(cls);
    label.textContent = text;
}

function showESPNStatus(type, msg) {
    const box = document.getElementById('espn-connection-status');
    box.style.display = 'block';
    box.className = `espn-status-box ${type}`;
    box.textContent = msg;
}

// ============ Init ============

async function silentDataRefresh() {
    try {
        // 1. Sleeper: fresh ADP + projections (fast, hits Sleeper API)
        await api('/api/sleeper/refresh', {
            method: 'POST',
            body: JSON.stringify({ year: 2025 }),
        });
    } catch (_) { /* non-fatal */ }

    try {
        // 2. NFL stats: component scores + base_value (uses cache if available, instant)
        await api('/api/stats/refresh', {
            method: 'POST',
            body: JSON.stringify({ year: 2024, scoring_format: 'PPR', force: false }),
        });
    } catch (_) { /* non-fatal */ }

    // Re-render with updated data
    await fetchAvailable();
    await refreshAll();
}

async function init() {
    // Wire virtual scroll listener once
    const availEl = document.getElementById('available-list');
    if (availEl) availEl.addEventListener('scroll', _onVsScroll, { passive: true });

    await fetchAvailable();
    await refreshAll();

    // Check if ESPN is already configured
    const espnCfg = await api('/api/espn/config');
    if (espnCfg.league_id && espnCfg.has_credentials) {
        updateSyncBadge('connected');
    }

    // Silently refresh player data + NFL stats in the background
    silentDataRefresh();
}

init();
