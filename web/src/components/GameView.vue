<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { leaveRoom, rejoinRoom, sendInput, showLobby, startRoom, state, toggleReady } from '../store'
import { PHASES, WEAPONS } from '../gameMeta'
import { drawScene } from '../renderer'
import ChatPanel from './ChatPanel.vue'

const canvas = ref(null)
const canvasWrap = ref(null)
const minimap = ref(null)
const weapon = ref('pistol')
const isFullscreen = ref(false)
const canvasSize = reactive({ width: 960, height: 600 })
const minimapSize = reactive({ width: 240, height: 150 })
const keys = reactive(new Set())
const mouse = reactive({ x: 480, y: 300, down: false })
const view = {
  cameraX: 0,
  cameraY: 0,
  cameraReady: false,
  dpr: 1,
  viewW: 960,
  viewH: 600,
  previous: {},
  phases: {},
  moving: {},
  lastTick: -1,
  lastFrame: 0,
  mouseX: 480,
  mouseY: 300,
}
let raf = 0
let inputTimer = 0
let resizeObserver = null

const own = computed(() => state.snapshot?.players?.find((player) => player.username === state.username))
const isSpectator = computed(() => Boolean(state.snapshot && !own.value))
const ranked = computed(() => [...(state.snapshot?.players || [])].sort((a, b) => b.kills - a.kills || a.deaths - b.deaths))
const remaining = computed(() => Math.max(0, Math.ceil(state.snapshot?.remaining || 0)))
const weaponOrder = Object.keys(WEAPONS)
const isEliminated = computed(() =>
  Boolean(!own.value && state.snapshot?.eliminated?.includes(state.username)))
const isOwner = computed(() => state.room?.owner === state.username)
const allReady = computed(() =>
  (state.room?.player_list?.length || 0) >= 2 &&
  state.room?.player_list?.every((p) => p.ready))

function resizeCanvas() {
  if (!canvas.value || !canvasWrap.value) return
  const rect = canvasWrap.value.getBoundingClientRect()
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  // 逻辑尺寸用 CSS 像素；位图按 DPR 放大，渲染器内部再缩放回去。
  const cssWidth = Math.max(320, Math.round(rect.width))
  const cssHeight = Math.max(240, Math.round(rect.height))
  view.dpr = dpr
  view.viewW = cssWidth
  view.viewH = cssHeight
  canvasSize.width = Math.round(cssWidth * dpr)
  canvasSize.height = Math.round(cssHeight * dpr)
  // 小地图也按 DPR 提高位图分辨率，避免被 CSS 拉伸后发糊。
  minimapSize.width = Math.round(240 * dpr)
  minimapSize.height = Math.round(150 * dpr)
  view.cameraReady = false
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
    } else if (canvasWrap.value) {
      await canvasWrap.value.requestFullscreen()
    }
  } catch {
    state.notice = '当前浏览器不支持全屏切换'
  }
}

function onFullscreenChange() {
  isFullscreen.value = Boolean(document.fullscreenElement)
  requestAnimationFrame(resizeCanvas)
}

function render(now) {
  view.mouseX = mouse.x
  view.mouseY = mouse.y
  if (canvas.value) drawScene(canvas.value, minimap.value, state.snapshot, state.username, view, now)
  raf = requestAnimationFrame(render)
}

function updateMouse(event) {
  const rect = canvas.value.getBoundingClientRect()
  // 鼠标坐标统一用 CSS 逻辑像素，与渲染器的逻辑坐标系一致。
  mouse.x = event.clientX - rect.left
  mouse.y = event.clientY - rect.top
}

function keyName(event) {
  return event.key.toLowerCase()
}

function isTextEntry(event) {
  const target = event.target
  if (!target) return false
  const tag = target.tagName
  return Boolean(target.isContentEditable) || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

function onKeyDown(event) {
  // 聊天输入框/可编辑区域获取焦点时，不能把 WASD、空格和数字当成战斗按键。
  if (isTextEntry(event)) return
  const key = keyName(event)
  if (['w', 'a', 's', 'd', ' ', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'].includes(key)) event.preventDefault()
  keys.add(key)
  const index = Number(event.key)
  if (index >= 1 && index <= 4) weapon.value = weaponOrder[index - 1]
}

function onKeyUp(event) {
  keys.delete(keyName(event))
}

function onBlur() {
  keys.clear()
  mouse.down = false
}

function emitInput() {
  const snapshot = state.snapshot
  if (!snapshot || snapshot.phase !== 'playing' || !own.value || state.reconnecting) return
  const worldX = view.cameraX + mouse.x
  const worldY = view.cameraY + mouse.y
  const angle = Math.atan2(worldY - own.value.y, worldX - own.value.x)
  const moveX = Number(keys.has('d') || keys.has('arrowright')) - Number(keys.has('a') || keys.has('arrowleft'))
  const moveY = Number(keys.has('s') || keys.has('arrowdown')) - Number(keys.has('w') || keys.has('arrowup'))
  sendInput({
    move_x: moveX,
    move_y: moveY,
    angle,
    shoot: mouse.down || keys.has(' '),
    weapon: weapon.value,
  })
}

onMounted(() => {
  // 重新进入战斗/刷新恢复时沿用服务器快照里的当前武器，而不是一律回退到手枪。
  const initialWeapon = own.value?.weapon
  if (initialWeapon && WEAPONS[initialWeapon]) weapon.value = initialWeapon
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', onBlur)
  document.addEventListener('fullscreenchange', onFullscreenChange)
  resizeCanvas()
  resizeObserver = new ResizeObserver(resizeCanvas)
  if (canvasWrap.value) resizeObserver.observe(canvasWrap.value)
  raf = requestAnimationFrame(render)
  inputTimer = window.setInterval(emitInput, 1000 / 30)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', onBlur)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  resizeObserver?.disconnect()
  if (document.fullscreenElement) document.exitFullscreen?.()?.catch?.(() => {})
  cancelAnimationFrame(raf)
  clearInterval(inputTimer)
})
</script>

<template>
  <main class="game-page page-content">
    <section class="battle-layout">
      <div class="canvas-column">
        <div class="battle-toolbar">
          <div class="battle-title"><span class="live-dot"></span><b>SECTOR 07</b><small>{{ PHASES[state.snapshot?.phase] || '等待同步' }}</small></div>
          <div class="battle-stats">
            <div><small>剩余时间</small><b>{{ String(Math.floor(remaining / 60)).padStart(2, '0') }}:{{ String(remaining % 60).padStart(2, '0') }}</b></div>
            <div><small>TICK</small><b>{{ state.snapshot?.tick || 0 }}</b></div>
            <div v-if="own"><small>生命</small><b :class="{ critical: own.health <= 30 }">{{ own.health }}</b></div>
            <div v-if="own"><small>弹药/5s</small><b :class="{ critical: own.ammo <= 20 }">{{ own.ammo ?? 100 }}</b></div>
            <div v-if="own"><small>死亡</small><b>{{ own.deaths }}/{{ state.snapshot?.death_limit ?? 5 }}</b></div>
            <div v-else><small>身份</small><b>观战</b></div>
          </div>
          <div class="toolbar-actions">
            <button class="button ghost small" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏 (Esc)' : '全屏作战' }}</button>
            <button class="button ghost small" @click="showLobby(true)">房间面板</button>
            <button class="button ghost small" @click="leaveRoom">离开</button>
          </div>
        </div>

        <div ref="canvasWrap" class="canvas-wrap" :class="{ fullscreen: isFullscreen }">
          <canvas
            ref="canvas"
            :width="canvasSize.width"
            :height="canvasSize.height"
            class="battle-canvas"
            @mousemove="updateMouse"
            @mousedown.left="mouse.down = true"
            @mouseup.left="mouse.down = false"
            @mouseleave="mouse.down = false"
            @contextmenu.prevent
          ></canvas>
          <div class="map-seed">MAP SEED · {{ state.snapshot?.map_seed ?? '----' }} · {{ state.snapshot?.width }}×{{ state.snapshot?.height }}</div>
          <div v-if="isFullscreen" class="fs-hud">
            <span class="fs-time">{{ String(Math.floor(remaining / 60)).padStart(2, '0') }}:{{ String(remaining % 60).padStart(2, '0') }}</span>
            <span v-if="own" class="fs-hp" :class="{ critical: own.health <= 30 }">HP {{ own.health }}</span>
            <span v-if="own" class="fs-hp">AMMO {{ own.ammo ?? 100 }}</span>
            <span v-else class="fs-hp">观战</span>
            <span class="fs-weapon">{{ WEAPONS[weapon].name }}</span>
            <button class="fs-exit" @click="toggleFullscreen">退出全屏 Esc</button>
          </div>
          <div v-if="state.reconnecting" class="reconnect-banner">连接中断，正在自动恢复对局…</div>
          <div v-if="isEliminated && state.snapshot?.phase !== 'finished'" class="elimination-banner">
            <b>你已被淘汰，本局积分已清零</b>
            <p>死亡达到 {{ state.snapshot?.death_limit ?? 5 }} 次。可重新参战或返回房间面板。</p>
            <div>
              <button class="button primary small" @click="rejoinRoom">重新参战</button>
              <button class="button ghost small" @click="showLobby(true)">房间面板</button>
            </div>
          </div>
          <div v-if="isSpectator && !isEliminated" class="spectator-banner">观战模式：可实时观察玩家操作与服务器同步状态</div>
          <Transition name="modal">
            <div v-if="state.snapshot?.phase === 'finished' && state.result?.length" class="result-overlay">
              <section class="result-card">
                <p class="eyebrow">MISSION REPORT</p>
                <h2>对局结算</h2>
                <p class="muted">{{ state.result[0].reason }}</p>
                <div class="result-list">
                  <div v-for="item in state.result" :key="item.username" class="result-row" :class="{ winner: item.won, eliminated: item.eliminated }">
                    <span>#{{ item.rank }}</span><b>{{ item.username }}</b>
                    <small>{{ item.kills }} 击杀 / {{ item.deaths }} 阵亡{{ item.eliminated ? ' · 已淘汰' : '' }}</small>
                    <strong>{{ item.score_delta > 0 ? '+' : '' }}{{ item.score_delta }}</strong>
                  </div>
                </div>
                <!-- 被淘汰者（当前为观战身份）：可重新参战或返回大厅 -->
                <div v-if="isEliminated || isSpectator" class="result-actions">
                  <button class="button primary full" @click="rejoinRoom">重新参战</button>
                  <button class="button ghost full" @click="showLobby(true)">返回房间面板</button>
                </div>
                <!-- 仍在场的普通玩家：准备下一局 -->
                <button v-else-if="!isOwner" class="button primary full" @click="toggleReady">准备下一局</button>
                <!-- 房主：全员准备后可直接开始，否则提示等待 -->
                <div v-else class="result-actions">
                  <button class="button primary full" :disabled="!allReady" @click="startRoom">
                    {{ allReady ? '开始新一局' : '等待其他玩家准备…' }}
                  </button>
                  <button class="button ghost full" @click="showLobby(true)">房间面板</button>
                </div>
              </section>
            </div>
          </Transition>
        </div>

        <div class="weapon-dock">
          <button
            v-for="(spec, index) in WEAPONS"
            :key="spec.name"
            class="weapon-card"
            :class="{ active: weapon === weaponOrder[index] }"
            @click="weapon = weaponOrder[index]"
          >
            <span class="weapon-key">{{ index + 1 }}</span>
            <span><b>{{ spec.name }}</b><small>{{ spec.trait }}</small></span>
          </button>
        </div>
      </div>

      <aside class="battle-sidebar">
        <section class="panel score-panel">
          <div class="section-heading"><div><h2>实时战绩</h2><span>COMBAT FEED</span></div><span class="tick-badge">30 HZ</span></div>
          <div v-for="player in ranked" :key="player.username" class="score-row" :class="{ self: player.username === state.username, offline: !player.connected }">
            <span class="player-avatar">{{ player.username.slice(0, 1).toUpperCase() }}</span>
            <div><b>{{ player.username }}<small v-if="player.username === state.username">你</small></b><small>{{ player.connected ? '在线' : '离线重连中' }}</small></div>
            <strong>{{ player.kills }}<small>/{{ player.deaths }}</small></strong>
          </div>
        </section>

        <section class="panel minimap-panel">
          <div class="section-heading"><div><h2>战区雷达</h2><span>TACTICAL MAP</span></div></div>
          <canvas ref="minimap" :width="minimapSize.width" :height="minimapSize.height" class="minimap"></canvas>
        </section>

        <section class="panel weapon-detail">
          <h2>{{ WEAPONS[weapon].name }}</h2>
          <p>{{ WEAPONS[weapon].description }}</p>
          <div class="control-grid"><span>WASD</span>移动<span>鼠标</span>瞄准<span>左键 / 空格</span>射击<span>1～4</span>切枪</div>
        </section>
        <ChatPanel compact />
      </aside>
    </section>
  </main>
</template>
