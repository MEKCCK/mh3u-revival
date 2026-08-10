#!/usr/bin/env python3
"""Self-contained webui monitoring panel for the MH3U server.

Served by the API server at http://<host>:<port>/ (and /panel). Zero external
dependencies: inline CSS/JS, no CDN (China networks block most), dark theme,
polls the JSON API every 3s (log every 5s). Reads nothing but the API, so it
works against any deployment of api.py.
"""
WEBUI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MH3U Revival — 服务器监视面板</title>
<style>
  :root { --bg:#0f1419; --card:#171e26; --line:#26313c; --txt:#dbe4ee;
          --dim:#7d8b99; --ok:#3fb950; --warn:#d29922; --bad:#f85149; --acc:#58a6ff; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--txt); font:13px/1.5 "Segoe UI",system-ui,sans-serif; padding:16px; }
  h1 { font-size:18px; margin-bottom:2px; }
  .sub { color:var(--dim); margin-bottom:14px; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:var(--bad); margin-right:6px; }
  .dot.ok { background:var(--ok); }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .card .k { color:var(--dim); font-size:11px; }
  .card .v { font-size:20px; font-weight:600; margin-top:2px; }
  .card .v small { font-size:12px; color:var(--dim); font-weight:400; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:1100px){ .grid { grid-template-columns:1fr; } }
  section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:14px; }
  h2 { font-size:13px; color:var(--acc); margin-bottom:8px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:4px 6px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--dim); font-weight:500; }
  .empty { color:var(--dim); padding:8px 4px; }
  #log { font-family:Consolas,monospace; font-size:11.5px; color:#9fb3c8; max-height:280px;
         overflow-y:auto; white-space:pre-wrap; word-break:break-all; }
  .badge { display:inline-block; padding:0 6px; border-radius:10px; font-size:11px; background:#1f2a36; margin-left:4px; }
  .badge.host { background:#2d1f14; color:var(--warn); }
</style>
</head>
<body>
<h1>MH3U Revival <span class="dot" id="dot"></span><span id="srv"></span></h1>
<div class="sub" id="sub"></div>

<div class="cards">
  <div class="card"><div class="k">在线玩家</div><div class="v" id="c_players">-</div></div>
  <div class="card"><div class="k">狩猎房间</div><div class="v" id="c_rooms">-</div></div>
  <div class="card"><div class="k">房间上限（全服）</div><div class="v" id="c_rooms_cap">-</div></div>
  <div class="card"><div class="k">在线上限（全服）</div><div class="v" id="c_conns_cap">-</div></div>
  <div class="card"><div class="k">运行时长</div><div class="v" id="c_uptime">-</div></div>
  <div class="card"><div class="k">大厅容量（每厅）</div><div class="v" id="c_caps">-</div></div>
</div>

<div class="grid">
  <section>
    <h2>玩家</h2>
    <table><thead><tr><th>PID</th><th>名字</th><th>在线</th><th>空闲</th><th>房间</th><th>大厅</th></tr></thead>
    <tbody id="t_players"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody></table>
  </section>
  <section>
    <h2>狩猎房间</h2>
    <table><thead><tr><th>GID</th><th>房主</th><th>人数</th><th>模式</th><th>参与者</th></tr></thead>
    <tbody id="t_rooms"><tr><td colspan="5" class="empty">加载中…</td></tr></tbody></table>
  </section>
</div>

<div class="grid">
  <section>
    <h2>大厅 / 集会所（港口）</h2>
    <table><thead><tr><th>GID</th><th>名称</th><th>人数</th><th>上限</th><th>类型</th><th>成员</th></tr></thead>
    <tbody id="t_halls"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody></table>
  </section>
  <section>
    <h2>服务器状态</h2>
    <table><thead><tr><th>项</th><th>值</th></tr></thead>
    <tbody id="t_srv"><tr><td colspan="2" class="empty">加载中…</td></tr></tbody></table>
  </section>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let statusData = {};
// If the operator opened the panel as /panel?token=xxx, pass the token along
// to the API so they see full detail; otherwise the API sanitizes.
const TOKEN = new URLSearchParams(location.search).get("token") || "";
function apiPath(p){
  return TOKEN ? p + (p.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : p;
}

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtSec(s){
  if (s == null) return "-";
  if (s < 60) return Math.round(s)+"s";
  if (s < 3600) return (s/60).toFixed(1)+"m";
  return (s/3600).toFixed(1)+"h";
}

async function getJ(path){
  const r = await fetch(path, {cache:"no-store"});
  if (!r.ok) throw new Error(path+" "+r.status);
  return r.json();
}

function renderStatus(){
  const s = statusData;
  if (!s) return;
  $("srv").textContent = s.server || "";
  $("sub").textContent = "advertised: " + (s.advertised_address||"-")
    + "  ·  ports: auth=" + (s.ports&&s.ports.auth) + " secure=" + (s.ports&&s.ports.secure)
    + "  ·  NEX v" + (s.nex_version||"-") + "  ·  " + (s.started_at||"");
  $("c_uptime").textContent = fmtSec(s.uptime_s);
  const caps = s.caps||{};
  $("c_rooms_cap").textContent = caps.rooms != null ? caps.rooms : "-";
  $("c_conns_cap").textContent = caps.connections != null ? caps.connections : "-";
  $("c_caps").textContent = (s.halls && s.halls.hall_max != null ? s.halls.hall_max : "-") + " 人";
}

async function renderPlayers(){
  const d = await getJ(apiPath("/api/players"));
  $("c_players").textContent = d.count;
  const tb = $("t_players");
  if (!d.count){ tb.innerHTML = '<tr><td colspan="6" class="empty">暂无玩家</td></tr>'; return; }
  tb.innerHTML = d.players.map(p =>
    "<tr><td>" + p.pid + "</td><td>" + esc(p.name) + "</td><td>" + fmtSec(p.uptime_s)
    + '</td><td>' + fmtSec(p.idle_s)
    + '</td><td>' + (p.rooms||[]).join(" ") + '</td><td>' + (p.halls||[]).join(" ") + "</td></tr>"
  ).join("");
}

async function renderRooms(){
  const d = await getJ(apiPath("/api/rooms"));
  $("c_rooms").textContent = d.count;
  const tb = $("t_rooms");
  if (!d.count){ tb.innerHTML = '<tr><td colspan="5" class="empty">暂无房间</td></tr>'; return; }
  tb.innerHTML = d.rooms.map(r =>
    "<tr><td>" + esc(r.gid) + '</td><td>' + esc(r.host_name || r.host_pid)
    + '</td><td>' + r.num_participants + "/" + r.max_participants
    + '</td><td>' + esc(r.game_mode) + '</td><td>'
    + (r.participants||[]).map(p => esc(p.name||p.pid)).join(", ") + "</td></tr>"
  ).join("");
}

async function renderHalls(){
  const d = await getJ(apiPath("/api/halls"));
  const tb = $("t_halls");
  const ports = d.halls.filter(h => !h.is_lobby);   // lobbies are game plumbing, hide them
  if (!ports.length){ tb.innerHTML = '<tr><td colspan="6" class="empty">暂无港口</td></tr>'; return; }
  tb.innerHTML = ports.map(h =>
    "<tr><td>" + esc(h.gid) + "</td><td>" + esc(h.name)
    + '</td><td>' + h.num_participants + '</td><td>' + h.max_participants
    + '</td><td>' + (h.official ? "官方" : "自建")
    + '</td><td>' + (h.participants||[]).map(p => esc(p.name||p.pid)).join(", ") + "</td></tr>"
  ).join("");
}

async function renderSrv(){
  const s = statusData;
  const tb = $("t_srv");
  if (!s){ tb.innerHTML = '<tr><td colspan="2" class="empty">无数据</td></tr>'; return; }
  const rows = [
    ["服务器", s.server || "-"],
    ["游戏服务器 ID", s.game_server_id || "-"],
    ["NEX 版本", s.nex_version != null ? "v" + s.nex_version : "-"],
    ["监听端口", "auth=" + ((s.ports||{}).auth) + " · secure=" + ((s.ports||{}).secure)
                 + " · natcheck=" + ((s.ports||{}).natcheck)],
    ["公布地址", s.advertised_address || "-"],
    ["港口数量", s.halls ? (s.halls.num_worlds != null ? s.halls.num_worlds : "-") : "-"],
    ["大厅容量", s.halls ? (s.halls.hall_max != null ? s.halls.hall_max + " 人/港" : "-") : "-"],
    ["启动时间", s.started_at || "-"],
    ["密码房策略", s.password_room_policy
                  ? (s.password_room_policy.enabled ? "启用（已销毁 " + (s.password_room_policy.destroyed||0) + " 个）" : "关闭")
                  : "-"],
  ];
  tb.innerHTML = rows.map(r => "<tr><td>" + esc(r[0]) + "</td><td>" + esc(r[1]) + "</td></tr>").join("");
}

async function tick(){
  try {
    statusData = await getJ(apiPath("/api/status"));
    $("dot").classList.add("ok");
  } catch(e){
    $("dot").classList.remove("ok");
    $("sub").textContent = "API 不可达: " + e.message;
  }
  renderStatus();
  renderSrv();
  try { await renderPlayers(); } catch(e){ $("t_players").innerHTML = '<tr><td colspan="6" class="empty">加载失败: '+esc(e.message)+'</td></tr>'; }
  try { await renderRooms(); } catch(e){ $("t_rooms").innerHTML = '<tr><td colspan="5" class="empty">加载失败: '+esc(e.message)+'</td></tr>'; }
  try { await renderHalls(); } catch(e){ $("t_halls").innerHTML = '<tr><td colspan="6" class="empty">加载失败: '+esc(e.message)+'</td></tr>'; }
}
tick(); setInterval(tick, 3000);
</script>
</body>
</html>
"""
