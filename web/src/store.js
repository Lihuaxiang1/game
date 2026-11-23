import { reactive } from 'vue'

const SESSION_KEY = 'blackline.session.v2'

export const state = reactive({
  connected: false,
  reconnecting: false,
  screen: 'login',
  username: '',
  token: localStorage.getItem(SESSION_KEY) || '',
  rooms: [],
  room: null,
  snapshot: null,
  result: null,
  chats: [],
  leaderboard: [],
  leaderboardOpen: false,
  eliminations: [],
  notice: '正在连接游戏服务器…',
  rtt: null,
})

let socket = null
let socketGeneration = 0
let reconnectTimer = null
let pingTimer = null
let manualLobby = false
let loggingOut = false
let sequence = 0
let pingSequence = 0
const pings = new Map()

export function connect() {
  if (loggingOut) return
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
  clearTimeout(reconnectTimer)
  const generation = ++socketGeneration
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const nextSocket = new WebSocket(`${protocol}://${location.host}/ws`)
  socket = nextSocket
  nextSocket.onopen = () => {
    if (generation !== socketGeneration || loggingOut) {
      nextSocket.close()
      return
    }
    state.connected = true
    state.reconnecting = false
    state.notice = state.token ? '正在恢复登录状态…' : '服务器已连接'
    if (state.token) send('reconnect', { token: state.token })
    clearInterval(pingTimer)
    pingTimer = setInterval(ping, 2000)
  }
  nextSocket.onmessage = (event) => {
    if (generation !== socketGeneration) return
    try {
      const incoming = JSON.parse(event.data)
      handleMessage(incoming.type, incoming.data || {})
    } catch {
      state.notice = '收到无法解析的服务器消息'
    }
  }
  nextSocket.onclose = () => {
    if (generation !== socketGeneration) return
    state.connected = false
    clearInterval(pingTimer)
    state.rtt = null
    if (loggingOut) return
    if (state.token) {
      state.reconnecting = true
      state.notice = '连接中断，正在自动恢复对局…'
    } else {
      state.notice = '服务器连接失败，正在重试…'
    }
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connect, 1500)
  }
  nextSocket.onerror = () => nextSocket.close()
}

export function send(kind, data = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false
  socket.send(JSON.stringify({ type: kind, data }))
  return true
}

function handleMessage(kind, data) {
  switch (kind) {
    case 'auth_ok':
      state.username = data.username
      state.token = data.token
      localStorage.setItem(SESSION_KEY, data.token)
      state.reconnecting = false
      state.notice = data.reconnected ? '对局已恢复' : `欢迎，${data.username}`
      if (state.screen === 'login') showLobby()
      break
    case 'eliminated':
      state.notice = `${data.username} ${data.reason}`
      state.eliminations = [...(state.eliminations || []), data].slice(-20)
      break
    case 'rooms':
      state.rooms = data.rooms || []
      break
    case 'joined':
      manualLobby = false
      state.chats = []
      state.result = null
      state.notice = data.notice || '已加入房间'
      break
    case 'room_state':
      state.room = data.room
      break
    case 'snapshot': {
      state.snapshot = data
      state.result = data.result || null
      // 页面刷新后模块内的 seq 从 0 开始，而服务端仍保留 last_seq。
      // 用自己快照里的 ack_seq 追平，避免刷新后输入被服务端全部丢弃。
      const ownState = data.players?.find((player) => player.username === state.username)
      const acknowledged = Number(ownState?.ack_seq)
      if (Number.isFinite(acknowledged) && acknowledged > sequence) sequence = acknowledged
      if (['playing', 'finished'].includes(data.phase) && state.screen !== 'game' && !manualLobby) {
        state.screen = 'game'
      }
      break
    }
    case 'game_over':
      state.result = data.results || []
      if (state.snapshot) {
        state.snapshot.phase = 'finished'
        state.snapshot.result = state.result
      }
      break
    case 'left':
      state.room = null
      state.snapshot = null
      state.result = null
      state.chats = []
      state.eliminations = []
      showLobby()
      state.notice = '已离开房间'
      break
    case 'chat':
      state.chats.push(data)
      if (state.chats.length > 100) state.chats.shift()
      break
    case 'leaderboard':
      state.leaderboard = data.entries || []
      break
    case 'pong': {
      const started = pings.get(data.client_time)
      if (started) state.rtt = Math.max(0, Math.round(performance.now() - started))
      pings.delete(data.client_time)
      break
    }
    case 'notice':
    case 'error':
      state.notice = data.text || '操作失败'
      if (data.code === 'auth_required') {
        localStorage.removeItem(SESSION_KEY)
        state.token = ''
        state.username = ''
        state.reconnecting = false
        state.room = null
        state.snapshot = null
        state.result = null
        state.chats = []
        manualLobby = false
        state.screen = 'login'
      }
      break
  }
}

function ping() {
  if (!state.connected) return
  const id = ++pingSequence
  pings.set(id, performance.now())
  if (pings.size > 10) pings.delete(pings.keys().next().value)
  send('ping', { client_time: id })
}

export function authenticate(action, username, password) {
  if (!send(action, { username, password })) {
    state.notice = '服务器尚未连接，请稍候重试'
    return false
  }
  state.notice = '正在验证账号…'
  return true
}

export function showLobby(manual = false) {
  manualLobby = manual
  state.screen = 'lobby'
  send('list_rooms')
  send('leaderboard')
}

export function showGame() {
  if (state.snapshot && ['playing', 'finished'].includes(state.snapshot.phase)) {
    manualLobby = false
    state.screen = 'game'
  } else {
    state.notice = '对局尚未开始，请等待房主开始'
  }
}

export function createRoom(name) {
  send('create_room', { name })
}

export function joinRoom(roomId, spectator = false) {
  manualLobby = false
  send('join_room', { room_id: Number(roomId), spectator })
}

export function leaveRoom() {
  send('leave_room')
}

export function toggleReady() {
  const own = state.room?.player_list?.find((player) => player.username === state.username)
  if (!own) {
    state.notice = '观众不能准备'
    return
  }
  manualLobby = false
  send('ready', { ready: !own.ready })
}

export function startRoom() {
  send('start_room')
}

export function dissolveRoom() {
  send('dissolve_room')
}

export function rejoinRoom() {
  manualLobby = false
  send('rejoin_room')
}

export function sendChat(text) {
  if (text.trim()) send('chat', { text: text.trim() })
}

export function sendInput(input) {
  sequence += 1
  send('input', { ...input, seq: sequence })
}

export function refresh() {
  send('list_rooms')
  send('leaderboard')
}

export function logout() {
  if (loggingOut) return
  loggingOut = true
  // 先同步释放房间席位，再关闭连接；否则服务端会保留一个离线玩家，
  // 使等待中的房间因“有玩家未连接”而永远无法开局。
  send('leave_room')
  localStorage.removeItem(SESSION_KEY)
  state.token = ''
  state.username = ''
  state.reconnecting = false
  state.notice = '正在退出登录…'
  clearInterval(pingTimer)
  clearTimeout(reconnectTimer)
  window.setTimeout(() => {
    socket?.close()
    location.reload()
  }, 150)
}
