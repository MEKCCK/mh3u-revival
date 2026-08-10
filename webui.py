#!/usr/bin/env python3
"""Self-contained webui monitoring panel for the MH3U server.

Served by the API server at http://<host>:<port>/ (and /panel). Zero external
dependencies: inline CSS/JS, no CDN (China networks block most), dark theme,
polls the JSON API every 3s. 8 languages (zh/en/ja/ko/fr/de/es/ru) via
?lang= or the header selector (persisted in localStorage; falls back to the
browser language). Privacy: shows names, PIDs, ports, rooms, counts, uptime
and server status only — never IPs or logs.
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
  .toprow { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
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
  .evlog { font-family:Consolas,monospace; font-size:11.5px; color:#9fb3c8; max-height:200px;
           overflow-y:auto; white-space:pre-wrap; }
  .ev .t { color:var(--dim); margin-right:6px; }
  .ev .join { color:var(--ok); } .ev .left { color:var(--bad); }
  .ev .room { color:var(--acc); } .ev .port { color:var(--warn); }
  .badge { display:inline-block; padding:0 6px; border-radius:10px; font-size:11px; background:#1f2a36; margin-left:4px; }
  .badge.host { background:#2d1f14; color:var(--warn); }
  .badge.full { background:#3a1d1d; color:var(--bad); }
  select { background:var(--card); color:var(--txt); border:1px solid var(--line); border-radius:6px; padding:4px 8px; }
</style>
</head>
<body>
<div class="toprow">
  <div>
    <h1>MH3U Revival <span class="dot" id="dot"></span><span id="srv"></span></h1>
    <div class="sub" id="sub"></div>
  </div>
  <select id="lang">
    <option value="zh">中文</option>
    <option value="en">English</option>
    <option value="ja">日本語</option>
    <option value="ko">한국어</option>
    <option value="fr">Français</option>
    <option value="de">Deutsch</option>
    <option value="es">Español</option>
    <option value="ru">Русский</option>
  </select>
</div>

<div class="cards">
  <div class="card"><div class="k" data-i18n="online">在线玩家</div><div class="v" id="c_players">-</div></div>
  <div class="card"><div class="k" data-i18n="huntRooms">狩猎房间</div><div class="v" id="c_rooms">-</div></div>
  <div class="card"><div class="k" data-i18n="uptime">运行时长</div><div class="v" id="c_uptime">-</div></div>
  <div class="card"><div class="k" data-i18n="portCap">港口容量</div><div class="v" id="c_caps">-</div></div>
</div>

<div class="grid">
  <section>
    <h2 data-i18n="players">玩家</h2>
    <table><thead><tr><th>PID</th><th data-i18n="colName">名字</th><th data-i18n="colOnline">在线</th><th data-i18n="colRooms">房间</th><th data-i18n="colPorts">港口</th></tr></thead>
    <tbody id="t_players"><tr><td colspan="5" class="empty" data-i18n="loading">加载中…</td></tr></tbody></table>
  </section>
  <section>
    <h2 data-i18n="huntRooms">狩猎房间</h2>
    <table><thead><tr><th>GID</th><th data-i18n="colHost">房主</th><th data-i18n="colCount">人数</th><th data-i18n="colMode">模式</th><th data-i18n="colRoomPort">所在港口</th><th data-i18n="colMembers">参与者</th></tr></thead>
    <tbody id="t_rooms"><tr><td colspan="6" class="empty" data-i18n="loading">加载中…</td></tr></tbody></table>
  </section>
</div>

<div class="grid">
  <section>
    <h2 data-i18n="ports">港口</h2>
    <table><thead><tr><th>GID</th><th data-i18n="colTitle">名称</th><th data-i18n="colCount">人数</th><th data-i18n="colMax">上限</th><th data-i18n="colType">类型</th></tr></thead>
    <tbody id="t_halls"><tr><td colspan="4" class="empty" data-i18n="loading">加载中…</td></tr></tbody></table>
  </section>
  <section>
    <h2 data-i18n="serverStatus">服务器状态</h2>
    <table><thead><tr><th data-i18n="colKey">项</th><th data-i18n="colValue">值</th></tr></thead>
    <tbody id="t_srv"><tr><td colspan="2" class="empty" data-i18n="loading">加载中…</td></tr></tbody></table>
  </section>
</div>

<section><h2 data-i18n="activity">活动记录</h2><div id="events" class="evlog"></div></section>

<script>
"use strict";
const $ = id => document.getElementById(id);
let statusData = {};
let sysData = null;
function fmtBytes(b){
  if (b == null) return "-";
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b/1024).toFixed(1) + " KB";
  if (b < 1073741824) return (b/1048576).toFixed(1) + " MB";
  return (b/1073741824).toFixed(2) + " GB";
}
// Raw GIDs are never shown to players — every hex id is mapped to a
// human-readable label ("0x1006" -> "房间 6", "0x101" -> "港口 1").
let PORT_LABEL = {};   // hex gid -> port label (built from /api/halls)
function portLabel(g){ return PORT_LABEL[g] || g; }
function roomLabel(g){
  const n = parseInt(String(g).replace("0x",""), 16) & 0xFFF;
  return isNaN(n) ? g : t("roomShort") + " " + n;
}
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

// ---------------- i18n (8 languages) ----------------
const I18N = {
  zh: { online:"在线玩家", huntRooms:"狩猎房间", uptime:"运行时长", portCap:"港口容量", players:"玩家", ports:"港口", serverStatus:"服务器状态",
        activity:"活动记录", colName:"名字", colOnline:"在线", colRooms:"房间", colPorts:"港口",
        colHost:"房主", colRoomPort:"所在港口", colCount:"人数", colMode:"模式", colMembers:"参与者",
        colTitle:"名称", colMax:"上限", colType:"类型", colKey:"项", colValue:"值",
        emptyPlayers:"暂无玩家", emptyRooms:"暂无房间", emptyPorts:"暂无港口", loading:"加载中…",
        loadFail:"加载失败", noData:"无数据", hostBadge:"房主", typePort:"港口",
        srvName:"服务器", pubAddr:"公布地址（玩家填写）", portCount:"港口数量",
        startedAt:"启动时间", pwdPolicy:"密码房策略", ev_joined:"加入服务器", ev_left:"离开服务器", ev_portIn:"进入港口", ev_portOut:"离开港口",
        ev_roomNew:"创建房间", ev_roomGone:"房间解散", ev_roomIn:"进入房间", ev_roomOut:"离开房间",
        titleOnline:"%d 在线", memUsage:"内存占用", cpuUsage:"CPU 占用", netUsage:"网络占用", roomShort:"房间", portShort:"港口", connErr:"无法连接服务器", unit:"人" },
  en: { online:"Online Players", huntRooms:"Hunt Rooms", uptime:"Uptime", portCap:"Port Capacity", players:"Players", ports:"Ports", serverStatus:"Server Status",
        activity:"Activity", colName:"Name", colOnline:"Online", colRooms:"Rooms", colPorts:"Ports",
        colHost:"Host", colRoomPort:"Hafen", colRoomPort:"Port", colCount:"Players", colMode:"Mode", colMembers:"Members",
        colTitle:"Name", colMax:"Max", colType:"Type", colKey:"Key", colValue:"Value",
        emptyPlayers:"No players", emptyRooms:"No rooms", emptyPorts:"No ports", loading:"Loading…",
        loadFail:"Load failed", noData:"No data", hostBadge:"HOST", typePort:"Port",
        srvName:"Server", pubAddr:"Public address (players use)", portCount:"Ports count",
        startedAt:"Started at", pwdPolicy:"Password rooms", ev_joined:"joined server", ev_left:"left server", ev_portIn:"entered port", ev_portOut:"left port",
        ev_roomNew:"created room", ev_roomGone:"room closed", ev_roomIn:"entered room", ev_roomOut:"left room",
        titleOnline:"%d online", memUsage:"Memory", cpuUsage:"CPU", netUsage:"Network", roomShort:"Room", portShort:"Port", connErr:"Cannot reach the server", unit:"players" },
  de: { online:"Online-Spieler", huntRooms:"Jagd-Räume", roomCap:"Raumlimit (global)", connCap:"Verbindungslimit (global)",
        uptime:"Betriebszeit", portCap:"Hafenkapazität", players:"Spieler", ports:"Häfen", serverStatus:"Serverstatus",
        activity:"Aktivität", colName:"Name", colOnline:"Online", colIdle:"Inaktiv", colRooms:"Räume", colPorts:"Häfen",
        colHost:"Host", colCount:"Spieler", colMode:"Modus", colAttrib:"Attr.", colMembers:"Mitglieder",
        colTitle:"Name", colMax:"Max", colType:"Typ", colKey:"Feld", colValue:"Wert",
        emptyPlayers:"Keine Spieler", emptyRooms:"Keine Räume", emptyPorts:"Keine Häfen", loading:"Lädt…",
        loadFail:"Laden fehlgeschlagen", noData:"Keine Daten", hostBadge:"HOST", typePort:"Hafen",
        srvName:"Server", srvId:"Game-Server-ID", nexVer:"NEX-Version", listenPorts:"Lausch-Ports",
        pubAddr:"Öffentliche Adresse (Spieler)", ticketAddr:"Ticket-Adresse (Mesh)", portCount:"Anzahl Häfen",
        portCapRow:"Hafenkapazität", startedAt:"Gestartet um", pwdPolicy:"Passwort-Räume", pwdOn:"An (%d zerstört)", pwdOff:"Aus",
        ev_joined:"dem Server beigetreten", ev_left:"Server verlassen", ev_portIn:"Hafen betreten", ev_portOut:"Hafen verlassen",
        ev_roomNew:"Raum erstellt", ev_roomGone:"Raum geschlossen", ev_roomIn:"Raum betreten", ev_roomOut:"Raum verlassen",
        titleOnline:"%d online", memUsage:"Speicher", cpuUsage:"CPU", netUsage:"Netzwerk", roomShort:"Raum", portShort:"Hafen", connErr:"Server nicht erreichbar", unit:"Spieler" },
  ja: { online:"オンライン", huntRooms:"狩猟部屋", uptime:"稼働時間", portCap:"港容量", players:"プレイヤー", ports:"港", serverStatus:"サーバー状態",
        activity:"アクティビティ", colName:"名前", colOnline:"オンライン", colRooms:"部屋", colPorts:"港",
        colHost:"ホスト", colRoomPort:"港", colCount:"人数", colMode:"モード", colMembers:"メンバー",
        colTitle:"名前", colMax:"上限", colType:"種類", colKey:"項目", colValue:"値",
        emptyPlayers:"プレイヤーなし", emptyRooms:"部屋なし", emptyPorts:"港なし", loading:"読み込み中…",
        loadFail:"読み込み失敗", noData:"データなし", hostBadge:"ホスト", typePort:"港",
        srvName:"サーバー", pubAddr:"公開アドレス（プレイヤー用）", portCount:"港の数",
        startedAt:"起動時刻", pwdPolicy:"パスワード部屋", ev_joined:"サーバーに参加", ev_left:"サーバーから退出", ev_portIn:"港に入場", ev_portOut:"港から退出",
        ev_roomNew:"部屋を作成", ev_roomGone:"部屋が解散", ev_roomIn:"部屋に入場", ev_roomOut:"部屋から退出",
        titleOnline:"%d オンライン", memUsage:"メモリ", cpuUsage:"CPU", netUsage:"ネットワーク", roomShort:"部屋", portShort:"港", connErr:"サーバーに接続できません", unit:"人" },
  ko: { online:"온라인 플레이어", huntRooms:"사냥방", uptime:"가동 시간", portCap:"항구 용량", players:"플레이어", ports:"항구", serverStatus:"서버 상태",
        activity:"활동 기록", colName:"이름", colOnline:"온라인", colRooms:"방", colPorts:"항구",
        colHost:"방장", colRoomPort:"항구", colCount:"인원", colMode:"모드", colMembers:"구성원",
        colTitle:"이름", colMax:"상한", colType:"유형", colKey:"항목", colValue:"값",
        emptyPlayers:"플레이어 없음", emptyRooms:"방 없음", emptyPorts:"항구 없음", loading:"불러오는 중…",
        loadFail:"불러오기 실패", noData:"데이터 없음", hostBadge:"방장", typePort:"항구",
        srvName:"서버", pubAddr:"공개 주소（플레이어용）", portCount:"항구 수",
        startedAt:"시작 시간", pwdPolicy:"비밀번호 방", ev_joined:"서버 접속", ev_left:"서버 접속 종료", ev_portIn:"항구 입장", ev_portOut:"항구 퇴장",
        ev_roomNew:"방 생성", ev_roomGone:"방 해체", ev_roomIn:"방 입장", ev_roomOut:"방 퇴장",
        titleOnline:"%d 온라인", memUsage:"메모리", cpuUsage:"CPU", netUsage:"네트워크", roomShort:"방", portShort:"항구", connErr:"서버에 연결할 수 없습니다", unit:"명" },
  fr: { online:"Joueurs en ligne", huntRooms:"Salles de chasse", uptime:"Temps de fonctionnement", portCap:"Capacité du port", players:"Joueurs", ports:"Ports", serverStatus:"État du serveur",
        activity:"Activité", colName:"Nom", colOnline:"En ligne", colRooms:"Salles", colPorts:"Ports",
        colHost:"Hôte", colRoomPort:"Port", colCount:"Joueurs", colMode:"Mode", colMembers:"Membres",
        colTitle:"Nom", colMax:"Max", colType:"Type", colKey:"Champ", colValue:"Valeur",
        emptyPlayers:"Aucun joueur", emptyRooms:"Aucune salle", emptyPorts:"Aucun port", loading:"Chargement…",
        loadFail:"Échec du chargement", noData:"Pas de données", hostBadge:"HÔTE", typePort:"Port",
        srvName:"Serveur", pubAddr:"Adresse publique (joueurs)", portCount:"Nombre de ports",
        startedAt:"Démarré à", pwdPolicy:"Salles à mot de passe", ev_joined:"a rejoint le serveur", ev_left:"a quitté le serveur", ev_portIn:"est entré au port", ev_portOut:"a quitté le port",
        ev_roomNew:"a créé une salle", ev_roomGone:"salle fermée", ev_roomIn:"est entré en salle", ev_roomOut:"a quitté la salle",
        titleOnline:"%d en ligne", memUsage:"Mémoire", cpuUsage:"CPU", netUsage:"Réseau", roomShort:"Salle", portShort:"Port", connErr:"Impossible de joindre le serveur", unit:"joueurs" },
  de: { online:"Online-Spieler", huntRooms:"Jagd-Räume", uptime:"Betriebszeit", portCap:"Hafenkapazität", players:"Spieler", ports:"Häfen", serverStatus:"Serverstatus",
        activity:"Aktivität", colName:"Name", colOnline:"Online", colRooms:"Räume", colPorts:"Häfen",
        colHost:"Host", colRoomPort:"Hafen", colRoomPort:"Port", colCount:"Spieler", colMode:"Modus", colMembers:"Mitglieder",
        colTitle:"Name", colMax:"Max", colType:"Typ", colKey:"Feld", colValue:"Wert",
        emptyPlayers:"Keine Spieler", emptyRooms:"Keine Räume", emptyPorts:"Keine Häfen", loading:"Lädt…",
        loadFail:"Laden fehlgeschlagen", noData:"Keine Daten", hostBadge:"HOST", typePort:"Hafen",
        srvName:"Server", pubAddr:"Öffentliche Adresse (Spieler)", portCount:"Anzahl Häfen",
        startedAt:"Gestartet um", pwdPolicy:"Passwort-Räume", ev_joined:"dem Server beigetreten", ev_left:"Server verlassen", ev_portIn:"Hafen betreten", ev_portOut:"Hafen verlassen",
        ev_roomNew:"Raum erstellt", ev_roomGone:"Raum geschlossen", ev_roomIn:"Raum betreten", ev_roomOut:"Raum verlassen",
        titleOnline:"%d online", unit:"players" },
  es: { online:"Jugadores online", huntRooms:"Salas de caza", uptime:"Tiempo activo", portCap:"Capacidad del puerto", players:"Jugadores", ports:"Puertos", serverStatus:"Estado del servidor",
        activity:"Actividad", colName:"Nombre", colOnline:"En línea", colRooms:"Salas", colPorts:"Puertos",
        colHost:"Anfitrión", colRoomPort:"Puerto", colCount:"Jugadores", colMode:"Modo", colMembers:"Miembros",
        colTitle:"Nombre", colMax:"Máx", colType:"Tipo", colKey:"Campo", colValue:"Valor",
        emptyPlayers:"Sin jugadores", emptyRooms:"Sin salas", emptyPorts:"Sin puertos", loading:"Cargando…",
        loadFail:"Error al cargar", noData:"Sin datos", hostBadge:"ANFITRIÓN", typePort:"Puerto",
        srvName:"Servidor", pubAddr:"Dirección pública (jugadores)", portCount:"Nº de puertos",
        startedAt:"Iniciado a las", pwdPolicy:"Salas con contraseña", ev_joined:"se unió al servidor", ev_left:"salió del servidor", ev_portIn:"entró al puerto", ev_portOut:"salió del puerto",
        ev_roomNew:"creó una sala", ev_roomGone:"sala cerrada", ev_roomIn:"entró a la sala", ev_roomOut:"salió de la sala",
        titleOnline:"%d en línea", memUsage:"Memoria", cpuUsage:"CPU", netUsage:"Red", roomShort:"Sala", portShort:"Puerto", connErr:"No se puede conectar al servidor", unit:"jugadores" },
  ru: { online:"Игроки онлайн", huntRooms:"Охотничьи комнаты", uptime:"Время работы", portCap:"Вместимость порта", players:"Игроки", ports:"Порты", serverStatus:"Состояние сервера",
        activity:"Активность", colName:"Имя", colOnline:"Онлайн", colRooms:"Комнаты", colPorts:"Порты",
        colHost:"Хост", colRoomPort:"Порт", colCount:"Игроков", colMode:"Режим", colMembers:"Участники",
        colTitle:"Название", colMax:"Макс.", colType:"Тип", colKey:"Поле", colValue:"Значение",
        emptyPlayers:"Нет игроков", emptyRooms:"Нет комнат", emptyPorts:"Нет портов", loading:"Загрузка…",
        loadFail:"Ошибка загрузки", noData:"Нет данных", hostBadge:"ХОСТ", typePort:"Порт",
        srvName:"Сервер", pubAddr:"Публичный адрес (игроки)", portCount:"Кол-во портов",
        startedAt:"Запущен в", pwdPolicy:"Комнаты с паролем", ev_joined:"подключился к серверу", ev_left:"покинул сервер", ev_portIn:"вошёл в порт", ev_portOut:"покинул порт",
        ev_roomNew:"создал комнату", ev_roomGone:"комната закрыта", ev_roomIn:"вошёл в комнату", ev_roomOut:"покинул комнату",
        titleOnline:"%d онлайн", memUsage:"Память", cpuUsage:"CPU", netUsage:"Сеть", roomShort:"Комната", portShort:"Порт", connErr:"Нет связи с сервером", unit:"игроков" },
};

let LANG = "zh";
(function initLang(){
  const q = new URLSearchParams(location.search).get("lang");
  if (q && I18N[q]) { LANG = q; localStorage.setItem("mh3u_lang", q); return; }
  const saved = localStorage.getItem("mh3u_lang");
  if (saved && I18N[saved]) { LANG = saved; return; }
  const nav = (navigator.language || "zh").toLowerCase().slice(0,2);
  LANG = I18N[nav] ? nav : "zh";
})();
function t(k, arg){
  const v = (I18N[LANG] && I18N[LANG][k]) != null ? I18N[LANG][k] : (I18N.zh[k] != null ? I18N.zh[k] : k);
  return arg != null ? String(v).replace("%d", arg) : v;
}
function applyLang(){
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  $("lang").value = LANG;
  tick();   // re-render everything (incl. the title) in the new language
}
$("lang").addEventListener("change", e => {
  LANG = e.target.value;
  localStorage.setItem("mh3u_lang", LANG);
  applyLang();
});

async function getJ(path){
  const r = await fetch(path, {cache:"no-store"});
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
// Encapsulated data layer — components only ever call Api.*, never fetch.
const Api = {
  status:  () => getJ(apiPath("/api/status")),
  players: () => getJ(apiPath("/api/players")),
  rooms:   () => getJ(apiPath("/api/rooms")),
  halls:   () => getJ(apiPath("/api/halls")),
  events:  (since) => getJ(apiPath("/api/events?since=" + since)),
  system:  () => getJ(apiPath("/api/system")),
};

function renderStatus(){
  const s = statusData;
  if (!s) return;
  $("srv").textContent = s.server || "";
  $("sub").textContent = "advertised: " + (s.advertised_address||"-")
    + "  ·  ports: auth=" + (s.ports&&s.ports.auth) + " secure=" + (s.ports&&s.ports.secure)
    + "  ·  " + t("portCount") + " " + (s.halls ? (s.halls.num_worlds||"-") : "-")
    + "  ·  NEX v" + (s.nex_version||"-") + "  ·  " + (s.started_at||"");
  $("c_uptime").textContent = fmtSec(s.uptime_s);
  $("c_caps").textContent = (s.halls && s.halls.hall_max != null ? s.halls.hall_max : "-") + " " + t("unit");
}

async function renderPlayers(){
  const d = await Api.players();
  const html = !d.count
    ? '<tr><td colspan="5" class="empty">' + t("emptyPlayers") + "</td></tr>"
    : d.players.map(p =>
        "<tr><td>" + p.pid + "</td><td>" + esc(p.name) + "</td><td>" + fmtSec(p.uptime_s)
        + '</td><td>' + (p.rooms||[]).map(roomLabel).join(" ") + '</td><td>' + (p.halls||[]).map(portLabel).join(" ") + "</td></tr>"
      ).join("");
  if ($("t_players").innerHTML !== html) $("t_players").innerHTML = html;
  const n = d.count || 0;
  if ($("c_players").textContent !== String(n)) $("c_players").textContent = n;
  document.title = (n ? "[" + t("titleOnline", n) + "] " : "") + "MH3U Revival";
}

async function renderRooms(){
  const d = await Api.rooms();
  const html = !d.count
    ? '<tr><td colspan="6" class="empty">' + t("emptyRooms") + "</td></tr>"
    : d.rooms.map(r => {
        const full = r.num_participants >= r.max_participants;
        const portGid = (r.attribs && r.attribs[0]) ? "0x" + Number(r.attribs[0]).toString(16) : "-";
        return "<tr><td>" + roomLabel(r.gid) + '</td><td>' + esc(r.host_name || r.host_pid)
          + (r.host_name ? ' <span class="badge host">' + t("hostBadge") + "</span>" : "")
          + '</td><td><span class="badge' + (full ? " full" : "") + '">'
          + r.num_participants + "/" + r.max_participants + "</span></td><td>"
          + esc(r.game_mode) + '</td><td>' + (portGid === "-" ? "-" : portLabel(portGid)) + '</td><td>'
          + (r.participants||[]).map(p => esc(p.name||p.pid)).join(", ") + "</td></tr>";
      }).join("");
  if ($("t_rooms").innerHTML !== html) $("t_rooms").innerHTML = html;
  const n = d.count || 0;
  if ($("c_rooms").textContent !== String(n)) $("c_rooms").textContent = n;
}

async function renderHalls(){
  const d = await Api.halls();
  const ports = d.halls.filter(h => !h.is_lobby);   // lobbies are game plumbing, hide them
  const html = !ports.length
    ? '<tr><td colspan="4" class="empty">' + t("emptyPorts") + "</td></tr>"
    : ports.map((h, i) => {
        PORT_LABEL[h.gid] = t("portShort") + " " + (i + 1);
        return "<tr><td>" + esc(h.name)
        + '</td><td>' + h.num_participants + '</td><td>' + (h.displayed_max != null ? h.displayed_max : h.max_participants)
        + '</td><td>' + t("typePort") + "</td></tr>";
      }).join("");
  if ($("t_halls").innerHTML !== html) $("t_halls").innerHTML = html;
}

async function renderSrv(){
  const s = statusData;
  const tb = $("t_srv");
  if (!s){ tb.innerHTML = '<tr><td colspan="2" class="empty">' + t("noData") + "</td></tr>"; return; }
  const rows = [
    [t("pubAddr"), s.public_address || s.advertised_address || "-"],
    [t("portCount"), s.halls ? (s.halls.num_worlds != null ? s.halls.num_worlds : "-") : "-"],
    [t("startedAt"), s.started_at || "-"],
  ];
  if (sysData) {
    if (sysData.memory) {
      const pct = Math.round(100 * sysData.memory.used / sysData.memory.total);
      rows.push([t("memUsage"), fmtBytes(sysData.memory.used) + " / " + fmtBytes(sysData.memory.total) + " (" + pct + "%)"]);
    } else {
      rows.push([t("memUsage"), "-"]);
    }
    rows.push([t("cpuUsage"), sysData.cpu_percent != null ? sysData.cpu_percent + " %" : "-"]);
    rows.push([t("netUsage"), "\u2193 " + fmtBytes(sysData.net_rx_bps) + "/s   \u2191 " + fmtBytes(sysData.net_tx_bps) + "/s"]);
  }
  const html = rows.map(r => "<tr><td>" + esc(r[0]) + "</td><td>" + esc(r[1]) + "</td></tr>").join("");
  if (tb.innerHTML !== html) tb.innerHTML = html;
}

let evSeq = 0;
async function renderEvents(){
  try {
    const d = await Api.events(evSeq);
    evSeq = d.seq || evSeq;
    if (!(d.events||[]).length) return;
    const el = $("events");
    const keys = {player_joined:"ev_joined", player_left:"ev_left", port_joined:"ev_portIn",
                  port_left:"ev_portOut", room_created:"ev_roomNew", room_destroyed:"ev_roomGone",
                  room_joined:"ev_roomIn", room_left:"ev_roomOut"};
    const cls = {player_joined:"join", player_left:"left", port_joined:"port",
                 port_left:"port", room_created:"room", room_destroyed:"room",
                 room_joined:"room", room_left:"room"};
    const frag = d.events.map(e => {
      const who = esc(e.name || e.pid);
      const what = t(keys[e.type] || e.type);
      const where = e.gid ? " " + esc(e.type.indexOf("port") === 0 ? portLabel(e.gid) : roomLabel(e.gid)) : "";
      return '<div class="ev"><span class="t">' + new Date().toTimeString().slice(0,8)
        + '</span><span class="' + (cls[e.type]||"") + '">' + what + '</span> '
        + who + where + "</div>";
    }).join("");
    el.insertAdjacentHTML("afterbegin", frag);
    while (el.children.length > 60) el.lastChild.remove();
  } catch(e){ /* feed is best-effort */ }
}

async function tick(){
  const jobs = [
    Api.status().then(d => { statusData = d; $("dot").classList.add("ok"); })
      .catch(() => { $("dot").classList.remove("ok"); $("sub").textContent = t("connErr"); }),
    renderPlayers().catch(() => {}),
    renderRooms().catch(() => {}),
    renderHalls().catch(() => {}),
    renderEvents().catch(() => {}),
    Api.system().then(d => { sysData = d; }).catch(() => {}),
  ];
  await Promise.all(jobs);
  renderStatus();
  renderSrv();
}
applyLang();
tick(); setInterval(tick, 3000);
</script>
</body>
</html>
"""
