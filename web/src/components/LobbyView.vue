<script setup>
import { computed, ref } from 'vue'
import {
  createRoom, dissolveRoom, joinRoom, leaveRoom, refresh,
  rejoinRoom, showGame, startRoom, state, toggleReady,
} from '../store'
import { PHASES } from '../gameMeta'
import ChatPanel from './ChatPanel.vue'

const roomName = ref('')
const selectedId = ref(null)
const own = computed(() => state.room?.player_list?.find((player) => player.username === state.username))
const isPlayer = computed(() => Boolean(own.value))
const isOwner = computed(() => state.room?.owner === state.username)
const isSpectating = computed(() =>
  Boolean(state.room && !isPlayer.value && state.room.spectator_list?.includes(state.username)))
const canStart = computed(() =>
  isOwner.value
  && state.room?.phase !== 'playing'
  && (state.room?.player_list?.length || 0) >= 2
  && state.room?.player_list?.every((p) => p.ready))
const canEnter = computed(() => ['playing', 'finished'].includes(state.snapshot?.phase))

function create() {
  createRoom(roomName.value.trim() || `${state.username} 的房间`)
  roomName.value = ''
}
</script>

<template>
  <main class="lobby-page page-content">
    <section class="page-title-row">
      <div><p class="eyebrow">OPERATIONS LOBBY</p><h1>作战大厅</h1><p class="muted">组建 2～5 人小队，自由交战。全员在线并准备后，由房主开始对局。</p></div>
      <div class="lobby-metrics"><div><b>{{ state.rooms.length }}</b><span>活跃房间</span></div><div><b>30</b><span>同步频率 / HZ</span></div><div><b>5</b><span>最大玩家</span></div></div>
    </section>

    <section class="create-room-bar">
      <div><span class="create-icon">＋</span><span><b>建立新战区</b><small>创建一个房间并邀请其他玩家加入</small></span></div>
      <form @submit.prevent="create"><input v-model="roomName" maxlength="20" :placeholder="`${state.username} 的房间`" /><button class="button primary" :disabled="Boolean(state.room)">创建房间 →</button></form>
    </section>

    <div class="lobby-grid">
      <section class="room-browser panel">
        <div class="section-heading"><div><h2>房间列表</h2><span>AVAILABLE OPERATIONS</span></div><button class="button ghost small" @click="refresh">↻ 刷新</button></div>
        <div class="room-table-header"><span>战区</span><span>玩家</span><span>观众</span><span>状态</span><span>操作</span></div>
        <div class="room-list">
          <article v-for="room in state.rooms" :key="room.id" class="room-row" :class="{ selected: selectedId === room.id }" @click="selectedId = room.id">
            <div class="room-name"><span class="room-index">R{{ String(room.id).padStart(2, '0') }}</span><div><b>{{ room.name }}</b><small>HOST / {{ room.owner }}</small></div></div>
            <span class="player-count"><b>{{ room.players }}</b> / {{ room.max_players }}</span>
            <span>{{ room.spectators }}</span>
            <span><i class="phase-pill" :class="room.phase">{{ PHASES[room.phase] }}</i></span>
            <div class="room-actions"><button @click.stop="joinRoom(room.id)">加入</button><button @click.stop="joinRoom(room.id, true)">观战</button></div>
          </article>
          <div v-if="!state.rooms.length" class="empty-rooms"><span>∷</span><h3>当前没有活跃战区</h3><p>在上方输入房间名，创建第一场对局。</p></div>
        </div>
        <div class="room-browser-footer"><span><i class="legend-dot waiting"></i>等待</span><span><i class="legend-dot playing"></i>战斗中</span><span><i class="legend-dot finished"></i>已结束</span><span class="footer-note">对局中空位可直接参战；满员或主动选择观战</span></div>
      </section>

      <aside class="lobby-sidebar">
        <section class="current-room panel">
          <div class="section-heading"><div><h2>当前房间</h2><span>YOUR OPERATION</span></div><i v-if="state.room" class="phase-pill" :class="state.room.phase">{{ PHASES[state.room.phase] }}</i></div>
          <template v-if="state.room">
            <div class="current-room-title"><span>R{{ String(state.room.id).padStart(2, '0') }}</span><div><h3>{{ state.room.name }}</h3><p>房主 / {{ state.room.owner }}</p></div></div>
            <div class="ready-progress"><div><span>玩家席位</span><b>{{ state.room.players }} / {{ state.room.max_players }}</b></div><div class="progress-track"><i :style="{ width: `${state.room.players / state.room.max_players * 100}%` }"></i></div></div>
            <div class="lobby-player-list">
              <div v-for="player in state.room.player_list" :key="player.username" class="lobby-player">
                <span class="player-avatar">{{ player.username.slice(0, 1).toUpperCase() }}</span>
                <div><b>{{ player.username }}<small v-if="player.username === state.username">你</small></b><span :class="{ offline: !player.connected }">{{ player.connected ? '在线' : '离线 · 等待重连' }}</span></div>
                <i :class="{ ready: player.ready }">{{ player.ready ? 'READY' : 'WAIT' }}</i>
              </div>
            </div>
            <p class="spectator-line">观战 / {{ state.room.spectator_list?.length ? state.room.spectator_list.join('、') : '暂无观众' }}</p>
            <p v-if="state.room.death_limit" class="spectator-line">规则：死亡 {{ state.room.death_limit }} 次即淘汰并清零本局积分，可重新参战</p>
            <button v-if="isPlayer && state.room.phase !== 'playing'" class="button primary full" @click="toggleReady">{{ own.ready ? '取消准备' : '准备作战' }}</button>
            <button v-if="isOwner && state.room.phase !== 'playing'" class="button primary full" :disabled="!canStart" @click="startRoom">
              {{ canStart ? '开始游戏' : '等待至少 2 名玩家全部准备' }}
            </button>
            <button v-if="isSpectating" class="button primary full" @click="rejoinRoom">重新参战</button>
            <button v-if="canEnter" class="button secondary full" @click="showGame">进入战斗 / 观战</button>
            <button v-if="isOwner" class="button ghost full danger" @click="dissolveRoom">解散房间</button>
            <button v-else class="button ghost full" @click="leaveRoom">离开房间</button>
          </template>
          <div v-else class="no-room"><span>01</span><h3>尚未加入战区</h3><p>创建房间，或从左侧列表选择一个房间加入。</p><div><b>2</b> 人即可开局 <b>5</b> 人上限</div></div>
        </section>
        <ChatPanel class="panel" />
      </aside>
    </div>
  </main>
</template>
